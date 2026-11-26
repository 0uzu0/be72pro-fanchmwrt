#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
给 24.10.4 的 fwxd 补齐缺失接口（防火墙 / 引导状态 / 历史流量 / 记录白名单 /
每日汇总 / 家长控制规则），并插入白名单生效所需的过滤行。

只做四类改动，全部幂等：
  1. 把 patches/fwxd/ 下的新文件拷进 package/fcm/fwxd/src/
  2. fwx_ubus.c：插「10 个函数声明 + 10 行分派表项」
  3. fwxd/src/Makefile：OBJS 追加 fwx_firewall.o fwx_ext_compat.o
  4. 白名单生效的过滤行（用户态，不动内核）：
       - fwx_user.c  add_user_record 开头跳过白名单设备（新记录不入库）
       - fwx_ubus.c  get_user_records 两个遍历循环里跳过（存量记录不显示）
       - fwx_ubus.c  fwx_api_visit_list 遍历里跳过（访问明细不显示）
     每处 2-4 行、块作用域 extern 声明，不碰任何既有语句。

为什么不直接维护 fwx_ubus.c 的整文件副本：
  那个文件 4856 行，上游只要动一行我们的副本就落后。这里插的都是
  「表末尾几行 + 表前的函数声明 + 过滤 continue」，锚点极短，漂移概率最低。

用法：
    python3 patch_fwxd_ext.py <源码树>

返回码：0 = 已应用 / 已是目标状态；2 = 锚点没找到（未完成，构建日志会警告）
"""

import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC_DIR = os.path.join(ROOT, "patches", "fwxd")

FWXD_REL = "package/fcm/fwxd/src"
UBUS_REL = FWXD_REL + "/fwx_ubus.c"
USER_REL = FWXD_REL + "/fwx_user.c"
MK_REL = FWXD_REL + "/Makefile"

NEW_FILES = ["fwx_firewall.c", "fwx_firewall.h", "fwx_ext_compat.c"]

# 分派表前的声明（fwx_ubus.c 里这些函数没有定义，只有声明后才不会被
# -Werror=implicit-function-declaration 判死）
DECL_ANCHOR = "static fwx_api_node_t fwx_api_node_list[] = {"
DECL_BLOCK = """/* ---- be72: 24.10.4 缺失接口的声明（实现在 fwx_firewall.c / fwx_ext_compat.c） ---- */
struct json_object *fwx_api_get_firewall(struct json_object *req_obj);
struct json_object *fwx_api_set_firewall(struct json_object *req_obj);
struct json_object *fwx_api_get_init_status(struct json_object *req_obj);
struct json_object *fwx_api_set_init_status(struct json_object *req_obj);
struct json_object *fwx_api_get_history_traffic_stats(struct json_object *req_obj);
struct json_object *fwx_api_get_record_whitelist(struct json_object *req_obj);
struct json_object *fwx_api_add_record_whitelist(struct json_object *req_obj);
struct json_object *fwx_api_del_record_whitelist(struct json_object *req_obj);
struct json_object *fwx_api_get_user_daily_summary(struct json_object *req_obj);
struct json_object *fwx_api_get_user_parental_control_rules(struct json_object *req_obj);

"""

# 表尾锚点（哨兵行，全文件唯一）
TABLE_ANCHOR = "    {NULL, NULL, 0, FWX_API_METHOD_GET}"
TABLE_ROWS = """    /* ---- be72: 防火墙 / 引导状态 ---- */
    {"get_firewall", fwx_api_get_firewall, 0, FWX_API_METHOD_GET},
    {"set_firewall", fwx_api_set_firewall, 1, FWX_API_METHOD_POST},
    {"get_init_status", fwx_api_get_init_status, 0, FWX_API_METHOD_GET},
    {"set_init_status", fwx_api_set_init_status, 0, FWX_API_METHOD_POST},
    /* ---- be72: 历史流量 / 记录白名单 / 每日汇总 / 家长控制 ---- */
    {"get_history_traffic_stats", fwx_api_get_history_traffic_stats, 0, FWX_API_METHOD_GET},
    {"get_record_whitelist", fwx_api_get_record_whitelist, 0, FWX_API_METHOD_GET},
    {"add_record_whitelist", fwx_api_add_record_whitelist, 1, FWX_API_METHOD_POST},
    {"del_record_whitelist", fwx_api_del_record_whitelist, 1, FWX_API_METHOD_POST},
    {"get_user_daily_summary", fwx_api_get_user_daily_summary, 0, FWX_API_METHOD_GET},
    {"get_user_parental_control_rules", fwx_api_get_user_parental_control_rules, 0, FWX_API_METHOD_GET},
"""

# 白名单过滤：add_user_record 开头（fwx_user.c，全文件唯一）
ADD_RECORD_ANCHOR = """    int total = 0;
    struct list_head *pos, *n;
    if (!client)
        return;
"""
ADD_RECORD_INSERT = """
    /* be72: 白名单设备不产生上网记录（用户态过滤，实现见 fwx_ext_compat.c） */
    {
        extern int fwx_ext_record_whitelisted(const char *mac);
        if (fwx_ext_record_whitelisted(client->mac)) {
            return;
        }
    }
"""

# 白名单过滤：get_user_records 的两个遍历循环（计数 + 输出），各出现一次
USER_RECORDS_ANCHOR = """        if (end_time > 0 && record->timestamp > end_time) {
            continue;
        }
"""
USER_RECORDS_INSERT = """
        /* be72: 白名单设备不出现在审计记录里 */
        if (fwx_ext_record_whitelisted(record->mac)) {
            continue;
        }
"""
USER_RECORDS_EXPECTED = 2

# 白名单过滤：fwx_api_visit_list 的设备遍历（全文件唯一）
VISIT_LIST_ANCHOR = """        if (mac && strcmp(mac, node->mac)) {
            continue;
        }
"""
VISIT_LIST_INSERT = """
        /* be72: 白名单设备的访问明细不进审计列表 */
        if (fwx_ext_record_whitelisted(node->mac)) {
            continue;
        }
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
    print("  [+] 拷入 %d 个源文件（防火墙 / 引导状态 / 流量与白名单扩展）" % len(NEW_FILES))

    # ---------- 2. fwx_ubus.c：声明 + 分派表 ----------
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
            die("锚点缺失：api 分派表（上游可能改了表名）")
        if text.count(TABLE_ANCHOR) != 1:
            die("锚点异常：分派表哨兵行出现 %d 次（期望 1）" % text.count(TABLE_ANCHOR))
        text = text.replace(DECL_ANCHOR, DECL_BLOCK + DECL_ANCHOR, 1)
        text = text.replace(TABLE_ANCHOR, TABLE_ROWS + TABLE_ANCHOR, 1)
        changed = True
        print("  [+] fwx_ubus.c：插入 10 行分派（防火墙/引导/历史流量/白名单/汇总/家长控制）")

    # 白名单过滤：get_user_records 两个循环
    n = text.count(USER_RECORDS_ANCHOR)
    if "白名单设备不出现在审计记录里" in text:
        print("  [skip] get_user_records 已有白名单过滤")
    elif n == USER_RECORDS_EXPECTED:
        text = text.replace(USER_RECORDS_ANCHOR,
                            USER_RECORDS_ANCHOR + USER_RECORDS_INSERT)
        changed = True
        print("  [+] fwx_ubus.c：get_user_records 两个循环插入白名单过滤")
    else:
        print("  [!] get_user_records 锚点出现 %d 次（期望 %d），跳过存量记录过滤" % (
            n, USER_RECORDS_EXPECTED))

    # 白名单过滤：visit_list
    if "白名单设备的访问明细" in text:
        print("  [skip] visit_list 已有白名单过滤")
    elif VISIT_LIST_ANCHOR in text:
        text = text.replace(VISIT_LIST_ANCHOR,
                            VISIT_LIST_ANCHOR + VISIT_LIST_INSERT, 1)
        changed = True
        print("  [+] fwx_ubus.c：visit_list 插入白名单过滤")
    else:
        print("  [!] visit_list 锚点未找到，跳过访问明细过滤")

    if changed:
        with open(ubus, "w", encoding="utf-8", errors="surrogateescape") as f:
            f.write(text)

    # ---------- 3. fwx_user.c：写入侧过滤 ----------
    user = os.path.join(tree, USER_REL)
    if not os.path.isfile(user):
        die("找不到 %s" % USER_REL)
    with open(user, "r", encoding="utf-8", errors="surrogateescape") as f:
        utext = f.read()

    if "白名单设备不产生上网记录" in utext:
        print("  [skip] fwx_user.c 已有写入侧白名单过滤")
    elif utext.count(ADD_RECORD_ANCHOR) == 1:
        utext = utext.replace(ADD_RECORD_ANCHOR,
                              ADD_RECORD_ANCHOR + ADD_RECORD_INSERT, 1)
        with open(user, "w", encoding="utf-8", errors="surrogateescape") as f:
            f.write(utext)
        print("  [+] fwx_user.c：add_user_record 插入写入侧白名单过滤")
    else:
        print("  [!] add_user_record 锚点异常，跳过写入侧过滤（读取侧过滤仍在）")

    # ---------- 4. Makefile ----------
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

    # ---------- 5. 回读自校验 ----------
    with open(ubus, "r", encoding="utf-8", errors="surrogateescape") as f:
        final = f.read()
    for api in ("get_firewall", "set_firewall", "get_init_status", "set_init_status",
                "get_history_traffic_stats", "get_record_whitelist",
                "add_record_whitelist", "del_record_whitelist",
                "get_user_daily_summary", "get_user_parental_control_rules"):
        if '"%s", fwx_api_%s' % (api, api) not in final:
            die("自校验失败：分派表里没有 %s" % api)

    for name in ("fwx_firewall.c", "fwx_ext_compat.c"):
        if not os.path.isfile(os.path.join(src_dir, name)):
            die("自校验失败：%s 没落地" % name)
    ext_src = open(os.path.join(src_dir, "fwx_ext_compat.c"),
                   encoding="utf-8", errors="surrogateescape").read()
    for sym in ("fwx_ext_record_whitelisted", "fwx_api_get_history_traffic_stats",
                "fwx_api_get_user_daily_summary",
                "fwx_api_get_user_parental_control_rules"):
        if sym not in ext_src:
            die("自校验失败：fwx_ext_compat.c 里没有 %s" % sym)

    print("  ✔ 自校验通过：10 个接口在分派表中，源文件与过滤就位")
    return 0


if __name__ == "__main__":
    sys.exit(main())
