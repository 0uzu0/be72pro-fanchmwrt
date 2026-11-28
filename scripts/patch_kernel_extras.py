#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
锚点式修改：往既有 C 文件里插入代码，不依赖 unified diff 的上下文行。

背景：0002/0003 原来是手工构造的 diff，实测出现过
「git apply 能过、GNU patch 在 hunk 中间对不上」的情况（上游改一行就整体失效）。
这两处改动本质都是「在某条确定的语句后面插几行」，用锚点匹配比用 diff 稳得多。

用法：
    python3 patch_kernel_extras.py --hnat <源码树>
    python3 patch_kernel_extras.py --fwx  <源码树>

返回码：0 = 已应用或已是目标状态；2 = 锚点没找到（未修改）
"""

import argparse
import os
import sys

STAMP_HNAT = "fwx_data.action"
STAMP_FWX = "fwx_hnat_flush_mac"

HNAT_REL = "target/linux/mediatek/files-6.6/drivers/net/ethernet/mediatek/mtk_hnat/hnat_nf_hook.c"
FWX_REL = "package/fcm/fwx/src/fwx_mac_filter.c"
INIT_REL = "package/fcm/fwxd/files/fwx.init"
FWX_MAIN_REL = "package/fcm/fwx/src/fwx_main.c"
FWX_LOG_REL = "package/fcm/fwx/src/fwx_log.c"
FWX_LOG_H_REL = "package/fcm/fwx/src/fwx_log.h"
CONN_LOCK_ANCHOR = (
    "\tspin_lock(&af_conn_lock);\n"
    "   \tconn = af_conn_find_and_add(flow.src, flow.dst, flow.sport, flow.dport, flow.l4_protocol);\n"
    "\tif (!conn){\n"
    "\t\treturn NF_ACCEPT;\n"
    "\t}\n"
)
CONN_LOCK_FIXED = (
    "\tspin_lock(&af_conn_lock);\n"
    "   \tconn = af_conn_find_and_add(flow.src, flow.dst, flow.sport, flow.dport, flow.l4_protocol);\n"
    "\tif (!conn){\n"
    "\t\tspin_unlock(&af_conn_lock);\n"
    "\t\treturn NF_ACCEPT;\n"
    "\t}\n"
)

# 建 PPE 表项的调用点（全文件唯一）
HNAT_ANCHOR = (
    "\t\tspin_lock(&hnat_priv->entry_lock);\n"
    "\t\tskb_to_hnat_info(skb, out, entry, &hw_path);\n"
)

HNAT_INSERT = """\t\t/*
\t\t * fwx: 被 DPI 判为 drop（ct->fwx_data.action == 1）的连接不进 PPE。
\t\t * 一旦 BIND，后续报文在 PRE_ROUTING 的 NF_IP_PRI_FIRST+1 钩子里
\t\t * 就被 NF_STOLEN，conntrack / fwx / nftables 都不会再被调用，
\t\t * 应用过滤与审计会失效。不建表则每包都走软件路径，fwx 才拦得住。
\t\t */
\t\t{
\t\t\tstruct nf_conn *ct;
\t\t\tenum ip_conntrack_info ctinfo;

\t\t\tct = nf_ct_get(skb, &ctinfo);
\t\t\tif (ct && ct->fwx_data.action)
\t\t\t\tbreak;
\t\t}

"""

# 往规则里加 MAC 的位置：add 节点 → 解锁 → 返回
FWX_ANCHOR = (
    "\t\tfwx_add_mac_node(&rule->mac_list, mac);\n"
    "\t\tmac_filter_write_unlock();\n"
)

# 规则修改路径（fwx_api_mod_mac_filter_rule）的收尾清表：
# 这条路径直接用 fwx_add_mac_node 建节点，不经过 fwx_add_mac_to_rule，
# 不补这一段的话「立即断网」在 UI 改规则时根本不触发。
FWX_MOD_MARK = "fwx-flush-mod"
FWX_MOD_ANCHOR = "\n\treturn 0;\n}\n\nint fwx_api_del_mac_filter_rule(cJSON *data_obj) {"

# 空 MAC 列表的匹配语义：上游把"空列表"当成"匹配所有设备"（101 除外），
# 我们新增的 102（设备黑名单）空列表时会把全部转发流量丢掉并发 RST。
FWX_EMPTY_MARK = "rule->rule_id == 102"
FWX_EMPTY_ANCHOR = (
    "\t\tif (mac_list_empty) {\n"
    "\n"
    "\t\t\tif (rule->rule_id == 101) {\n"
)
FWX_EMPTY_FIX = (
    "\t\tif (mac_list_empty) {\n"
    "\n"
    "\t\t\t/* 101（单设备规则聚合）与 102（设备黑名单）的空列表一律不匹配：\n"
    "\t\t\t * 空列表意味着\"还没拉黑任何设备\"。若沿用上游语义（空 = 匹配所有），\n"
    "\t\t\t * 规则 102 一建出来就会丢掉全部转发流量并发 TCP RST ——\n"
    "\t\t\t * 表现为\"能拨号、能开管理页、但局域网所有设备上不了网\"。 */\n"
    "\t\t\tif (rule->rule_id == 101 || " + FWX_EMPTY_MARK + ") {\n"
)

FWX_MOD_INSERT = """\t/* fwx-flush-mod: 规则变更后清掉相关终端的 PPE 表项，让拦截立即生效。
\t * 单独再取一次 mac_list，避免依赖上面那段里可能未初始化的 mac_array。
\t */
\tif (action_obj) {
\t\tint act = action_obj->valueint;

\t\tif (act == 3) {
\t\t\tcJSON *m = cJSON_GetObjectItem(data_obj, "mac");
\t\t\tu8 mac_bin[ETH_ALEN] = {0};

\t\t\tif (m && mac_str_to_bin(m->valuestring, mac_bin))
\t\t\t\tfwx_hnat_flush_mac(mac_bin);
\t\t} else if (act == 1 || act == 2) {
\t\t\tcJSON *ma = cJSON_GetObjectItem(data_obj, "mac_list");
\t\t\tint n = ma ? cJSON_GetArraySize(ma) : 0;

\t\t\tfor (i = 0; i < n; i++) {
\t\t\t\tcJSON *m = cJSON_GetArrayItem(ma, i);
\t\t\t\tu8 mac_bin[ETH_ALEN] = {0};

\t\t\t\tif (m && mac_str_to_bin(m->valuestring, mac_bin))
\t\t\t\t\tfwx_hnat_flush_mac(mac_bin);
\t\t\t}
\t\t}
\t}
"""

FWX_CALL_INSERT = """\t\t/*
\t\t * 立即清掉该终端已绑定的 PPE 表项：下一包重新走 netfilter，
\t\t * fwx 的 drop 即刻生效，不必等硬件表项老化。
\t\t * 放在解锁之后——清表要遍历整张 FOE 表，不能持锁。
\t\t */
\t\tfwx_hnat_flush_mac(mac);
"""

FWX_HELPER = """#include <linux/module.h>

/*
 * hnat 通过 EXPORT_SYMBOL 导出 ppe_del_entry_by_mac（按 MAC 清 PPE 表项）。
 *
 * 这里用 __symbol_get 动态解析，而不是 extern 直接引用：直接调用这个函数指针
 * 会让 fwx.ko 出现未定义符号，OpenWrt 依赖扫描就会报
 *   "Package kmod-fwx is missing dependencies ... mtkhnat.ko"
 * 而本树没有对应的 kmod 包可以用 DEPENDS 补（mtkhnat 编在以太网驱动里）。
 *
 * 解析不到时返回 NULL，此时退化为「不清表、等硬件表项自然老化」，不影响其他功能。
 */
#ifdef CONFIG_MODULES
static void fwx_hnat_flush_mac(const unsigned char *mac)
{
	static int (**del_by_mac)(unsigned char *mac);

	if (!del_by_mac) {
		/*
		 * 这里必须直接调 __symbol_get("名字")，不能用 symbol_get(x)：
		 * 6.6 的 symbol_get 宏是 ((typeof(&x))(__symbol_get(__stringify(x))))，
		 * typeof(&x) 要求符号先有声明；而一旦 extern 声明并在别处直接调用，
		 * 又会让 fwx.ko 出现未定义符号、被 OpenWrt 判成依赖 mtkhnat.ko。
		 * 直接传字符串两头都不沾。
		 */
		del_by_mac = (int (**)(unsigned char *mac))
			__symbol_get("ppe_del_entry_by_mac");
		if (!del_by_mac)
			return;
	}

	if (*del_by_mac)
		(*del_by_mac)((unsigned char *)mac);
}
#else
static void fwx_hnat_flush_mac(const unsigned char *mac)
{
	(void)mac;
}
#endif
"""


# fwxd 的 init 脚本：没有 fwx.ko 时不要拉起 rule_manager。
# 降级版（无内核模块）下 rule_manager 每 10s 轮询一次必然失败，会持续刷日志、
# 也让界面上的规则状态错乱。用标记串做幂等判断。
INIT_MARK = "fwx-guard"
INIT_ANCHOR = (
    '\tprocd_open_instance\n'
    '\tprocd_set_param respawn 60 5 5\n'
    '\tprocd_set_param stderr 1\n'
    '\tprocd_set_param command "/usr/bin/rule_manager"\n'
)

INIT_INSERT = """\t# fwx-guard: fwx.ko 没加载成功就不要拉起 rule_manager
\tif ! lsmod | grep -q "^fwx "; then
\t\tlogger -t fwx "fwx.ko not loaded, skip rule_manager"
\t\treturn 0
\tfi

"""


def read(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8", "surrogateescape")


def write(path, text):
    with open(path, "wb") as f:
        f.write(text.encode("utf-8", "surrogateescape"))


def apply_hnat(root):
    path = os.path.join(root, HNAT_REL)
    if not os.path.isfile(path):
        print("  [!] 找不到 %s" % HNAT_REL)
        return 2

    src = read(path)
    if STAMP_HNAT in src:
        print("  [skip] hnat 已含 fwx 判断")
        return 0

    if src.count(HNAT_ANCHOR) != 1:
        print("  [!] hnat 锚点命中 %d 次（期望 1 次），未修改" % src.count(HNAT_ANCHOR))
        return 2

    src = src.replace(HNAT_ANCHOR, HNAT_INSERT + HNAT_ANCHOR, 1)
    write(path, src)

    ok = STAMP_HNAT in read(path)
    print("  [+] hnat: 被判 drop 的连接不再进 PPE" if ok else "  [!] hnat 写入校验失败")
    return 0 if ok else 2


def apply_fwx(root):
    path = os.path.join(root, FWX_REL)
    if not os.path.isfile(path):
        print("  [!] 找不到 %s" % FWX_REL)
        return 2

    src = read(path)
    todo = []

    # 1) 辅助函数：插到最后一个 #include 之后（这个位置上游几乎不会动）
    if "static void fwx_hnat_flush_mac" not in src:
        lines = src.splitlines(keepends=True)
        idxs = [i for i, ln in enumerate(lines) if ln.startswith("#include")]
        if not idxs:
            print("  [!] 找不到 #include，未修改")
            return 2
        lines.insert(idxs[-1] + 1, FWX_HELPER)
        src = "".join(lines)
        todo.append("helper")

    # 2) add_mac_to_rule 路径（当前上游未调用，留着兼容后续版本）
    if FWX_ANCHOR in src and "fwx_hnat_flush_mac(mac);" not in src:
        src = src.replace(FWX_ANCHOR, FWX_ANCHOR + FWX_CALL_INSERT, 1)
        todo.append("add_mac")

    # 3) 规则修改路径（UI 实际走这里）
    if FWX_MOD_MARK not in src:
        if FWX_MOD_ANCHOR not in src:
            print("  [!] 规则修改函数的锚点未命中，未插入（立即断开将不生效）")
            return 2
        src = src.replace(FWX_MOD_ANCHOR, FWX_MOD_INSERT + FWX_MOD_ANCHOR, 1)
        todo.append("mod")

    # 4) 空 MAC 列表的匹配语义（关键 bug 修复）
    #    上游：mac_list_empty 时，只有 101 跳过，其它 rule_id 一律 return rule
    #    —— 等于"空列表 = 匹配所有设备"。我们新建的黑名单规则 102 在还没拉黑任何
    #    设备时列表就是空的，于是每台设备的转发流量都被丢、还发 RST（拨号与
    #    LuCI 不受影响，因为一个走 WAN 侧、一个走 input 链）。
    #    修法：102 也按"空列表 = 不匹配"处理，与 101 一致；其它 id 保留上游语义。
    if FWX_EMPTY_MARK not in src:
        if FWX_EMPTY_ANCHOR not in src:
            print("  [!] 空列表匹配语义的锚点未命中，未修复（黑名单会误杀全部流量）")
            return 2
        src = src.replace(FWX_EMPTY_ANCHOR, FWX_EMPTY_FIX, 1)
        todo.append("empty-list")

    if not todo:
        print("  [skip] fwx_mac_filter 已是目标状态")
        return 0

    write(path, src)
    chk = read(path)
    ok = (STAMP_FWX in chk) and (FWX_MOD_MARK in chk) and (FWX_EMPTY_MARK in chk)
    print("  [+] fwx: 清表调用已插入 (%s)" % ",".join(todo) if ok else "  [!] fwx 写入校验失败")
    return 0 if ok else 2


def apply_connlock(root):
    """上游 bug 修复：fwx_main.c 里 af_conn_lock 的持锁提前返回。

    netfilter hook（softirq）里 kmalloc(GFP_ATOMIC) 可能失败，
    af_conn_find_and_add 返回 NULL 时原代码带着自旋锁直接 return，
    锁永不释放 —— 之后任何进入该路径的包都在 spin_lock 上死自旋，
    表现为「PPPoE 拨通、公网流量涌入的瞬间整机卡死」。
    原厂固件没有 fwx.ko 所以从不触发；这是 fanchmwrt 24.10 上游自带
    的 bug，首次在锐捷树跑就被打出来了（build-19 起所有版本都带）。
    锚点含原样缩进（tab + 空格混合），幂等。"""
    path = os.path.join(root, FWX_MAIN_REL)
    src = read(path)
    if CONN_LOCK_FIXED in src:
        print("  [skip] fwx_main.c 的 af_conn_lock 泄漏已修复")
        return 0
    if src.count(CONN_LOCK_ANCHOR) != 1:
        print("  [!] af_conn_lock 锚点出现 %d 次（期望 1），未修改" % src.count(CONN_LOCK_ANCHOR))
        return 2
    src = src.replace(CONN_LOCK_ANCHOR, CONN_LOCK_FIXED, 1)
    write(path, src)
    chk = read(path)
    print("  [+] fwx_main.c：af_conn_lock 持锁返回已修复" if CONN_LOCK_FIXED in chk
          else "  [!] fwx_main.c 写入校验失败")
    return 0 if CONN_LOCK_FIXED in chk else 2


# ---------------------------------------------------------------- 上报报文上限
# 内核 af_send_msg_to_user() 里 `if (len >= MAX_FWX_NL_MSG_LEN) return -1;`
# —— MAX_FWX_NL_MSG_LEN 只有 1024，而报文里除了 up_flow/down_flow 还塞了
# visit_info（含 URL）。只要有点访问记录就超长，整条消息被**静默丢弃**：
# 内核不发、Rmem 恒 0、fwxd 收不到 → 统计全零、访问记录 404、应用中心空白。
# 实测（用户设备 90 秒采样）：Rmem/Drops 恒 0，全局小时统计 22 个全零。
# fwxd 侧同一道门：MAX_FWX_NETLINK_MSG_LEN 也是 1024，接收缓冲 4096。
# 三处一起放宽到 16384，并给超长加日志，避免以后再"无声失败"。
NL_FWX_H = "package/fcm/fwx/src/fwx.h"
NL_FWX_MAIN = "package/fcm/fwx/src/fwx_main.c"
NL_FWXD_H = "package/fcm/fwxd/src/fwx_netlink.h"
NL_FWXD_C = "package/fcm/fwxd/src/fwx_netlink.c"
NL_MARK = "16384 /* be72pro: 原 1024 太窄"
NL_LOG_MARK = "be72pro: 报文超长"


def apply_netlink_limit(root):
    todo = []

    fh = os.path.join(root, NL_FWX_H)
    if os.path.isfile(fh):
        src = read(fh)
        if NL_MARK not in src:
            old = "#define MAX_FWX_NL_MSG_LEN 1024"
            if old in src:
                src = src.replace(old, "#define MAX_FWX_NL_MSG_LEN 16384 /* be72pro: 原 1024 太窄"
                                     "，visit_info 一多就超长被丢弃 */", 1)
                write(fh, src)
                todo.append("fwx.h")

    fm = os.path.join(root, NL_FWX_MAIN)
    if os.path.isfile(fm):
        src = read(fm)
        if NL_LOG_MARK not in src:
            old = ("\tif (len >= MAX_FWX_NL_MSG_LEN)\n"
                   "\t\treturn -1;")
            new = ("\tif (len >= MAX_FWX_NL_MSG_LEN) {\n"
                   "\t\t/* be72pro: 报文超长以前是静默 return，排查时完全看不见 */\n"
                   "\t\tprintk_ratelimited(KERN_WARNING \"fwx: \"\n"
                   "\t\t\t\"be72pro: 报文超长 %d >= %d，本条统计被丢弃\\n\",\n"
                   "\t\t\tlen, MAX_FWX_NL_MSG_LEN);\n"
                   "\t\treturn -1;\n"
                   "\t}")
            if old in src:
                src = src.replace(old, new, 1)
                write(fm, src)
                todo.append("fwx_main.c")

    dh = os.path.join(root, NL_FWXD_H)
    if os.path.isfile(dh):
        src = read(dh)
        if NL_MARK not in src:
            old = "#define MAX_FWX_NETLINK_MSG_LEN 1024"
            if old in src:
                src = src.replace(old, "#define MAX_FWX_NETLINK_MSG_LEN 16384 /* be72pro: 原 1024 太窄 */", 1)
                write(dh, src)
                todo.append("fwx_netlink.h")

    dc = os.path.join(root, NL_FWXD_C)
    if os.path.isfile(dc):
        src = read(dc)
        if "#define MAX_NL_RCV_BUF_SIZE 4096" in src:
            src = src.replace("#define MAX_NL_RCV_BUF_SIZE 4096",
                              "#define MAX_NL_RCV_BUF_SIZE 16384 /* be72pro: 与报文上限一致 */", 1)
            write(dc, src)
            todo.append("fwx_netlink.c")

    if not todo:
        print("  [skip] netlink 报文上限已是目标状态")
        return 0
    print("  [+] netlink 报文上限 1024 -> 16384（%s）" % ",".join(todo))
    return 0


# ---------------------------------------------------------------- rule_manager 联动 HNAT 钩子
RM_REL = "package/fcm/fwxd/files/rule_manager.lua"
RM_MARK = "hook_toggle"
RM_ANCHOR = ('        log(string.format("Record enable set to %d successfully", enable))\n'
             '        return true')
RM_INSERT = ('        log(string.format("Record enable set to %d successfully", enable))\n'
             '        -- be72pro: 审计开着时必须停掉 HNAT 钩子，否则 offload 吞包，\n'
             '        -- 统计/审计/过滤全部失效（实测 800MB 下载只计入几百字节）。\n'
             '        -- 模块本身保持加载，这里只切钩子；审计关掉则恢复硬件加速。\n'
             '        local ht = io.open("/sys/kernel/debug/hnat/hook_toggle", "w")\n'
             '        if ht then\n'
             '            if enable == 1 then ht:write("0") else ht:write("1") end\n'
             '            ht:close()\n'
             '            log(string.format("HNAT hook_toggle -> %s (审计%s)",\n'
             '                enable == 1 and "disabled" or "enabled",\n'
             '                enable == 1 and "开" or "关"))\n'
             '        else\n'
             '            log("HNAT hook_toggle debugfs 不可写，跳过")\n'
             '        end\n'
             '        return true')


def apply_rm_hnat(root):
    path = os.path.join(root, RM_REL)
    if not os.path.isfile(path):
        print("  [!] 找不到 %s" % RM_REL)
        return 2
    src = read(path).replace("\r\n", "\n")   # be72: vendor 的 rule_manager.lua 是 CRLF
    if RM_MARK in src:
        print("  [skip] rule_manager 已含 HNAT 联动")
        return 0
    if RM_ANCHOR not in src:
        print("  [!] rule_manager 锚点未命中，未联动 hook_toggle（统计会被 offload 吞掉）")
        return 2
    src = src.replace(RM_ANCHOR, RM_INSERT, 1)
    write(path, src)
    chk = read(path)
    print("  [+] rule_manager：审计开关已联动 HNAT hook_toggle" if RM_MARK in chk else "  [!] 写入校验失败")
    return 0 if RM_MARK in chk else 2


def apply_init(root):
    """降级版（无 fwx.ko）下别让 rule_manager 空转。"""
    path = os.path.join(root, INIT_REL)
    if not os.path.isfile(path):
        print("  [!] 找不到 %s" % INIT_REL)
        return 2

    src = read(path)
    if INIT_MARK in src:
        print("  [skip] fwx.init 已含守卫")
        return 0

    if src.count(INIT_ANCHOR) != 1:
        print("  [!] fwx.init 锚点命中 %d 次（期望 1 次），未修改" % src.count(INIT_ANCHOR))
        return 2

    src = src.replace(INIT_ANCHOR, INIT_INSERT + INIT_ANCHOR, 1)
    write(path, src)

    ok = INIT_MARK in read(path)
    print("  [+] fwx.init: 无 fwx.ko 时跳过 rule_manager" if ok else "  [!] fwx.init 写入校验失败")
    return 0 if ok else 2


# ---------------------------------------------------------------- batch4: 四类加速（内核）
# fwx_log.c：accel_ids sysctl（写入即解析为 app_id 位图）
# fwx_main.c：钩子里命中加速位图的应用直接放行（跳过记录与过滤，省 CPU）
ACCEL_LOG_ANCHOR = "int g_tcp_rst = 1;"
ACCEL_LOG_ADD = """int g_tcp_rst = 1;

/* be72: 四类网络加速（游戏/视频/下载/办公）。用户态（rule_manager）把开启类别的
   app_id 列表写入 /proc/sys/fwx/accel_ids，写入时解析为位图；钩子里命中的应用
   直接 NF_ACCEPT —— 跳过 DPI 深度解析、审计记录与过滤，降低 CPU 占用。
   注意：开启加速的类别不再参与应用过滤/黑名单/审计，页面上有说明。 */
#define FWX_ACCEL_BITMAP_SIZE 4096   /* 位图：32768 个 app_id */
static char fwx_accel_ids[8192] = "";
static unsigned char fwx_accel_bitmap[FWX_ACCEL_BITMAP_SIZE] = {0};

static void fwx_accel_parse_ids(const char *s)
{
	int id;
	int got;
	const char *p = s;

	memset(fwx_accel_bitmap, 0, sizeof(fwx_accel_bitmap));
	while (*p) {
		id = 0;
		got = 0;
		while (*p >= '0' && *p <= '9') {
			id = id * 10 + (*p - '0');
			got = 1;
			p++;
		}
		if (got && id > 0 && id < 32768)
			fwx_accel_bitmap[id >> 3] |= (unsigned char)(1u << (id & 7));
		while (*p && (*p < '0' || *p > '9'))
			p++;
	}
}

static int fwx_accel_ids_handler(struct ctl_table *table, int write,
				 void *buffer, size_t *lenp, loff_t *ppos)
{
	int ret = proc_dostring(table, write, buffer, lenp, ppos);
	if (write && ret == 0)
		fwx_accel_parse_ids((const char *)table->data);
	return ret;
}

int fwx_accel_test(int app_id)
{
	if (app_id <= 0 || app_id >= 32768)
		return 0;
	return (fwx_accel_bitmap[app_id >> 3] & (1u << (app_id & 7))) != 0;
}
"""
ACCEL_SYSCTL_ANCHOR = """	{
		.procname	= "tcp_rst","""
ACCEL_SYSCTL_ADD = """	{
		.procname	= "accel_ids",
		.data		= &fwx_accel_ids,
		.maxlen		= sizeof(fwx_accel_ids) - 1,
		.mode		= 0644,
		.proc_handler	= fwx_accel_ids_handler,
	},
	{
		.procname	= "tcp_rst","""
ACCEL_MARK = "be72: 四类网络加速"
ACCEL_HOOK_ANCHOR = """		app_id = ct->fwx_data.app_id;"""
ACCEL_HOOK_ADD = """		app_id = ct->fwx_data.app_id;
		/* be72: 开了加速的类别直接放行（跳过记录与过滤），见 fwx_log.c */
		if (fwx_accel_test(app_id))
			return NF_ACCEPT;"""
ACCEL_LOGH_ANCHOR = "extern int g_tcp_rst;"
ACCEL_LOGH_ADD = "extern int g_tcp_rst;\nextern int fwx_accel_test(int app_id);"


def apply_accel(root):
    rc = 0
    log_path = os.path.join(root, FWX_LOG_REL)
    if not os.path.isfile(log_path):
        print("  [!] 找不到 %s" % FWX_LOG_REL)
        return 2
    src = read(log_path)
    todo = []
    if ACCEL_MARK not in src:
        if src.count(ACCEL_LOG_ANCHOR) != 1:
            print("  [!] fwx_log.c 变量锚点未命中")
            return 2
        src = src.replace(ACCEL_LOG_ANCHOR, ACCEL_LOG_ADD, 1)
        if src.count(ACCEL_SYSCTL_ANCHOR) != 1:
            print("  [!] fwx_log.c sysctl 锚点未命中")
            return 2
        src = src.replace(ACCEL_SYSCTL_ANCHOR, ACCEL_SYSCTL_ADD, 1)
        todo.append("fwx_log.c")
    if todo:
        write(log_path, src)
    # fwx_main.c：钩子检查
    main_path = os.path.join(root, FWX_MAIN_REL)
    src = read(main_path)
    if ACCEL_HOOK_ANCHOR in src and "fwx_accel_test(app_id)" not in src:
        if src.count(ACCEL_HOOK_ANCHOR) != 1:
            print("  [!] fwx_main.c 钩子锚点出现 %d 次" % src.count(ACCEL_HOOK_ANCHOR))
            return 2
        src = src.replace(ACCEL_HOOK_ANCHOR, ACCEL_HOOK_ADD, 1)
        write(main_path, src)
        todo.append("fwx_main.c")
    # fwx_log.h：导出
    h_path = os.path.join(root, FWX_LOG_H_REL)
    src = read(h_path)
    if ACCEL_LOGH_ANCHOR in src and "fwx_accel_test" not in src:
        src = src.replace(ACCEL_LOGH_ANCHOR, ACCEL_LOGH_ADD, 1)
        write(h_path, src)
        todo.append("fwx_log.h")
    if todo:
        print("  [+] 四类加速内核位图：%s" % ",".join(todo))
    else:
        print("  [skip] 四类加速内核位图已是目标状态")
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hnat", metavar="TREE")
    ap.add_argument("--fwx", metavar="TREE")
    a = ap.parse_args()

    rc = 0
    if a.hnat:
        rc |= apply_hnat(a.hnat)
    if a.fwx:
        rc |= apply_fwx(a.fwx)
        rc |= apply_connlock(a.fwx)
        rc |= apply_init(a.fwx)
        # be72: 这三个都依赖 fwx 树（含 fwxd），--hnat-only 调用时 a.fwx=None
        # v2.0.0 前放在块外，3.2（--hnat）调用时 TypeError 被 || warn 吞掉，
        # 导致 accel 位图/hook_toggle 联动/netlink 上限静默缺失（build-46）。
        rc |= apply_netlink_limit(a.fwx)
        rc |= apply_rm_hnat(a.fwx)
        rc |= apply_accel(a.fwx)
    sys.exit(0 if rc == 0 else 2)


if __name__ == "__main__":
    main()
