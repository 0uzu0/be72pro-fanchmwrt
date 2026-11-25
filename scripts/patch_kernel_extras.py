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
 * 这里用 symbol_get 动态解析，而不是 extern 直接引用：mtkhnat 在本树里可能
 * 被编成模块，直接引用会让 OpenWrt 依赖扫描报
 *   "Package kmod-fwx is missing dependencies ... mtkhnat.ko"
 * 而且没有对应的 kmod 包可以用 DEPENDS 补。
 *
 * symbol_get 解析不到时返回 NULL，此时退化为「不清表、等硬件表项自然老化」，
 * 不影响其余功能。
 */
#ifdef CONFIG_MODULES
static void fwx_hnat_flush_mac(const unsigned char *mac)
{
	static int (**del_by_mac)(unsigned char *mac);
	int (**p)(unsigned char *mac);

	if (!del_by_mac) {
		p = (int (**)(unsigned char *mac))symbol_get(ppe_del_entry_by_mac);
		if (!p)
			return;
		del_by_mac = p;
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
    if STAMP_FWX in src:
        print("  [skip] fwx_mac_filter 已含清表调用")
        return 0

    # 1) 辅助函数插到最后一个 #include 之后（这个位置上游几乎不会动）
    lines = src.splitlines(keepends=True)
    idxs = [i for i, ln in enumerate(lines) if ln.startswith("#include")]
    if not idxs:
        print("  [!] 找不到 #include，未修改")
        return 2
    lines.insert(idxs[-1] + 1, FWX_HELPER)
    src = "".join(lines)

    # 2) 调用点插到 add_mac → unlock 之后
    if src.count(FWX_ANCHOR) != 1:
        print("  [!] fwx 锚点命中 %d 次（期望 1 次），未修改" % src.count(FWX_ANCHOR))
        return 2
    src = src.replace(FWX_ANCHOR, FWX_ANCHOR + FWX_CALL_INSERT, 1)

    write(path, src)
    ok = STAMP_FWX in read(path)
    print("  [+] fwx: 拉黑时按 MAC 清 PPE 表项" if ok else "  [!] fwx 写入校验失败")
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
