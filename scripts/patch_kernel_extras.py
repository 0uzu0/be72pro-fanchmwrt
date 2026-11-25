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
}
#endif
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

    if not todo:
        print("  [skip] fwx_mac_filter 已是目标状态")
        return 0

    write(path, src)
    chk = read(path)
    ok = (STAMP_FWX in chk) and (FWX_MOD_MARK in chk)
    print("  [+] fwx: 清表调用已插入 (%s)" % ",".join(todo) if ok else "  [!] fwx 写入校验失败")
    return 0 if ok else 2


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
    sys.exit(0 if rc == 0 else 2)


if __name__ == "__main__":
    main()
