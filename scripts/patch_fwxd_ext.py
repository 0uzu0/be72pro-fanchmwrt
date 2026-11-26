#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
给 24.10.4 的 fwxd 补上两个缺失接口（防火墙 / 仪表板引导状态）。

只做三件事，全部幂等：
  1. 把 patches/fwxd/ 下的新文件拷进 package/fcm/fwxd/src/
  2. 在 fwx_ubus.c 的 api 分派表里插「声明 + 4 行表项」
  3. 在 fwxd/src/Makefile 的 OBJS 里追加两个 .o

为什么不直接维护 fwx_ubus.c 的整文件副本：
  那个文件 4856 行、上游只要动一行我们的副本就落后。这里插的都是
  「表末尾几行 + 表前的函数声明」，锚点极短，漂移概率最低。

用法：
    python3 patch_fwxd_ext.py <源码树>

返回码：0 = 已应用 / 已是目标状态；2 = 锚点没找到（未修改任何内容）
"""

import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC_DIR = os.path.join(ROOT, "patches", "fwxd")

FWXD_REL = "package/fcm/fwxd/src"
UBUS_REL = FWXD_REL + "/fwx_ubus.c"
MK_REL = FWXD_REL + "/Makefile"

NEW_FILES = ["fwx_firewall.c", "fwx_firewall.h", "fwx_ext_compat.c"]

# 表前声明（fwx_ubus.c 里这些函数没有定义，只有这里声明后才不会被
# -Werror=implicit-function-declaration 判死）
DECL_ANCHOR = "static fwx_api_node_t fwx_api_node_list[] = {"
DECL_BLOCK = """/* ---- be72: 24.10.4 缺失接口的声明（实现在 fwx_firewall.c / fwx_ext_compat.c） ---- */
struct json_object *fwx_api_get_firewall(struct json_object *req_obj);
struct json_object *fwx_api_set_firewall(struct json_object *req_obj);
struct json_object *fwx_api_get_init_status(struct json_object *req_obj);
struct json_object *fwx_api_set_init_status(struct json_object *req_obj);

"""

# 表尾锚点（哨兵行，全文件唯一）
TABLE_ANCHOR = "    {NULL, NULL, 0, FWX_API_METHOD_GET}"
TABLE_ROWS = """    /* ---- be72: 防火墙 / 引导状态 ---- */
    {"get_firewall", fwx_api_get_firewall, 0, FWX_API_METHOD_GET},
    {"set_firewall", fwx_api_set_firewall, 1, FWX_API_METHOD_POST},
    {"get_init_status", fwx_api_get_init_status, 0, FWX_API_METHOD_GET},
    {"set_init_status", fwx_api_set_init_status, 0, FWX_API_METHOD_POST},
"""


def die(msg):
    print("  [x] " + msg)
    sys.exit(2)


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        return 2
    tree = os.path.abspath(sys.argv[1])
    src_dir = os.path.join(tree, FWXD_REL)
    if not os.path.isdir(src_dir):
        die("找不到 %s —— 第 2 步没有把 fwxd 搬进源码树？" % FWXD_REL)

    # ---------- 1. 拷新文件 ----------
    for name in NEW_FILES:
        src = os.path.join(SRC_DIR, name)
        dst = os.path.join(src_dir, name)
        if not os.path.isfile(src):
            die("仓库里缺 patches/fwxd/%s" % name)
        shutil.copyfile(src, dst)
    print("  [+] 拷入 %d 个源文件（防火墙 / 引导状态）" % len(NEW_FILES))

    # ---------- 2. fwx_ubus.c ----------
    ubus = os.path.join(tree, UBUS_REL)
    if not os.path.isfile(ubus):
        die("找不到 %s" % UBUS_REL)
    with open(ubus, "r", encoding="utf-8", errors="surrogateescape") as f:
        text = f.read()

    changed = False
    if '"get_firewall"' in text:
        print("  [skip] fwx_ubus.c 已含 get_firewall 表项")
    else:
        if DECL_ANCHOR not in text:
            die("锚点缺失：%s（上游可能改了表名）" % DECL_ANCHOR)
        text = text.replace(DECL_ANCHOR, DECL_BLOCK + DECL_ANCHOR, 1)

        if TABLE_ANCHOR not in text:
            die("锚点缺失：分派表哨兵行")
        text = text.replace(TABLE_ANCHOR, TABLE_ROWS + TABLE_ANCHOR, 1)
        changed = True
        print("  [+] fwx_ubus.c：插入 4 行分派（get/set_firewall、get/set_init_status）")

    if changed:
        with open(ubus, "w", encoding="utf-8", errors="surrogateescape") as f:
            f.write(text)

    # ---------- 3. Makefile ----------
    mk = os.path.join(tree, MK_REL)
    if not os.path.isfile(mk):
        die("找不到 %s" % MK_REL)
    with open(mk, "r", encoding="utf-8", errors="surrogateescape") as f:
        mk_text = f.read()

    if "fwx_firewall.o" in mk_text:
        print("  [skip] Makefile 已含 fwx_firewall.o")
    else:
        lines = mk_text.splitlines(True)
        hit = False
        for i, line in enumerate(lines):
            if line.startswith("OBJS:="):
                lines[i] = line.rstrip("\n") + " fwx_firewall.o fwx_ext_compat.o\n"
                hit = True
                break
        if not hit:
            die("锚点缺失：Makefile 里没有 OBJS:= 行")
        with open(mk, "w", encoding="utf-8", errors="surrogateescape") as f:
            f.write("".join(lines))
        print("  [+] Makefile：OBJS 追加 fwx_firewall.o fwx_ext_compat.o")

    # ---------- 4. 回读自校验 ----------
    # 写完不一定真的对：插入位置错、锚点被替换到注释里都可能发生。
    # 这里按「表里真的有这 4 行」回读一次，失败直接返回 2，让构建日志一眼看到。
    with open(ubus, "r", encoding="utf-8", errors="surrogateescape") as f:
        final = f.read()
    for api in ("get_firewall", "set_firewall", "get_init_status", "set_init_status"):
        if '"%s", fwx_api_%s' % (api, api) not in final:
            die("自校验失败：分派表里没有 %s" % api)

    for name in ("fwx_firewall.c", "fwx_ext_compat.c"):
        if not os.path.isfile(os.path.join(src_dir, name)):
            die("自校验失败：%s 没落地" % name)

    print("  ✔ 自校验通过：4 个接口在分派表中，2 个新源文件就位")
    return 0


if __name__ == "__main__":
    sys.exit(main())
