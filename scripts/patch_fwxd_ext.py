#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
给 24.10.4 的 fwxd 补齐缺失接口（防火墙 / 引导状态 / 历史流量 / 记录白名单 /
每日汇总 / 家长控制规则与详情 / 设备黑名单），并插入白名单生效所需的过滤行。

只做五类改动，全部幂等：
  1. 把 patches/fwxd/ 下的新文件拷进 package/fcm/fwxd/（源码进 src/，
     mac_blacklist.config 进 files/，并在顶层 Makefile 补安装行）
  2. fwx_ubus.c：插「15 个函数声明 + 15 行分派表项」
  3. fwxd/src/Makefile：OBJS 追加 fwx_firewall.o fwx_ext_compat.o
  4. 顶层 fwxd/Makefile：追加 mac_blacklist.config 的 INSTALL_DATA 行
     （named section 'base'，黑名单 uci key 依赖它；安装行在顶层
       Makefile 的 install 段，OBJS 在 src/Makefile —— 两个文件别混）
  5. 白名单生效的过滤行（用户态，不动内核）：
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
FWXD_TOP_REL = "package/fcm/fwxd"
UBUS_REL = FWXD_REL + "/fwx_ubus.c"
USER_REL = FWXD_REL + "/fwx_user.c"
MK_REL = FWXD_REL + "/Makefile"
MK_TOP_REL = FWXD_TOP_REL + "/Makefile"

NEW_FILES = ["fwx_firewall.c", "fwx_firewall.h", "fwx_ext_compat.c",
             "mac_blacklist.config",   # named section base，key 与 25.12 一致
             "fwx_record.config",      # 记录白名单的 whitelist 节（25.12 才有）
             "feature.cfg"]            # v26.4.10 社区特征库（305 条，去重清洗版）

# 分派表前的声明（fwx_ubus.c 里这些函数没有定义，只有声明后才不会被
# -Werror=implicit-function-declaration 判死）。注意：fwx_ext_record_whitelisted
# 的声明必须在文件顶部（build-23 的教训：首个调用点在 4198 行，表声明在
# 4663 行 —— 声明晚于使用 = implicit declaration = 编译失败）。
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
struct json_object *fwx_api_get_mac_blacklist(struct json_object *req_obj);
struct json_object *fwx_api_add_mac_blacklist(struct json_object *req_obj);
struct json_object *fwx_api_del_mac_blacklist(struct json_object *req_obj);
struct json_object *fwx_api_get_system_base_info(struct json_object *req_obj);
struct json_object *fwx_api_get_parental_control_detail(struct json_object *req_obj);

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
    /* ---- be72: 设备黑名单 / 系统基础信息 / 家长控制详情 ---- */
    {"get_mac_blacklist", fwx_api_get_mac_blacklist, 0, FWX_API_METHOD_GET},
    {"add_mac_blacklist", fwx_api_add_mac_blacklist, 1, FWX_API_METHOD_POST},
    {"del_mac_blacklist", fwx_api_del_mac_blacklist, 1, FWX_API_METHOD_POST},
    {"get_system_base_info", fwx_api_get_system_base_info, 0, FWX_API_METHOD_GET},
    {"get_parental_control_detail", fwx_api_get_parental_control_detail, 0, FWX_API_METHOD_GET},
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
        if name.endswith(".config") or name.endswith(".cfg"):
            # mac_blacklist.config 进 files/（顶层 Makefile INSTALL）；
            # feature.cfg 进 files/（fwxd 包的 $(CP) ./files/*.cfg 通配安装）
            dst = os.path.join(tree, FWXD_TOP_REL, "files", name)
        else:
            dst = os.path.join(src_dir, name)
        if not os.path.isfile(src):
            die("仓库里缺 patches/fwxd/%s" % name)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
    print("  [+] 拷入 %d 个源文件（防火墙 / 引导状态 / 扩展接口 / 黑名单配置）" % len(NEW_FILES))

    # ---------- 2. fwx_ubus.c：顶部声明 + 分派表 ----------
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
        # 顶部前向声明（必须在第一个过滤点之前，见 DECL_BLOCK 上方注释）
        top_anchor = "extern fwx_status_t g_fwx_status;\nextern void reload_oaf_rule(void);\n"
        if text.count(top_anchor) != 1:
            die("锚点异常：fwx_ubus.c 顶部声明区锚点出现 %d 次（期望 1）" % text.count(top_anchor))
        top_insert = ("\n/* be72: 记录白名单查询（实现见 fwx_ext_compat.c），供下方多个过滤点调用 */\n"
                      "int fwx_ext_record_whitelisted(const char *mac);\n")
        text = text.replace(top_anchor, top_anchor + top_insert, 1)
        text = text.replace(DECL_ANCHOR, DECL_BLOCK + DECL_ANCHOR, 1)
        text = text.replace(TABLE_ANCHOR, TABLE_ROWS + TABLE_ANCHOR, 1)
        changed = True
        print("  [+] fwx_ubus.c：顶部前向声明 + 15 行分派（防火墙/引导/流量/白名单/汇总/家长控制/黑名单/基础信息/详情）")

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

    # ---------- 3b. rule_manager.lua：设备黑名单的执行链 ----------
    # 25.12 的黑名单生效机制：rule_manager（fwxd 的规则守护）把
    # mac_blacklist.base.mac_list 读出、作为内核规则 102 下发（mode=SINGLE_USER，
    # mac_list 命中的设备被断网）—— 24.10 的 rule_manager.lua 没有这段，
    # 只补 fwxd api 的话黑名单只是存了个列表，不生效。
    # 以下 Lua 全部取自 25.12 原文（自包含、空格缩进），锚点式插入。
    lua = os.path.join(tree, FWXD_TOP_REL, "files", "rule_manager.lua")
    if not os.path.isfile(lua):
        print("  [!] 找不到 rule_manager.lua，跳过黑名单执行链")
    else:
        with open(lua, "r", encoding="utf-8", errors="surrogateescape") as f:
            ltext = f.read()
        lchanged = False

        # 1) 常量（行尾有尾随空格，按行前缀匹配插入）
        if "BLACKLIST_MAC_FILTER_RULE_ID" not in ltext:
            lconst = 'local BLACKLIST_MAC_FILTER_RULE_ID = 102\n'
            hit_c = False
            for i, ln in enumerate(ltext.splitlines(True)):
                if ln.startswith("local SINGLE_MAC_FILTER_RULE_ID = 101"):
                    ltext = "".join(ltext.splitlines(True)[:i + 1]) + lconst + \
                        "".join(ltext.splitlines(True)[i + 1:])
                    hit_c = True
                    break
            if hit_c:
                lchanged = True
                print("  [+] rule_manager.lua：BLACKLIST 规则常量")
            else:
                print("  [!] rule_manager: 常量锚点未命中")

        # 2) load_mac_blacklist()（25.12 原样，含 get_list 探测降级）
        if "local function load_mac_blacklist()" not in ltext:
            lfn = (
                'local function load_mac_blacklist()\n'
                '    local uci_cursor = uci.cursor()\n'
                '    local mac_list = {}\n'
                '    local ok = false\n'
                '\n'
                '    if type(uci_cursor.get_list) == "function" then\n'
                '        local list_ret = uci_cursor:get_list("mac_blacklist", "base", "mac_list")\n'
                '        if type(list_ret) == "table" then\n'
                '            mac_list = list_ret\n'
                '            ok = true\n'
                '        elseif type(list_ret) == "string" then\n'
                '            mac_list = {list_ret}\n'
                '            ok = true\n'
                '        end\n'
                '    end\n'
                '\n'
                '    if not ok then\n'
                '        local raw = uci_cursor:get("mac_blacklist", "base", "mac_list")\n'
                '        if type(raw) == "table" then\n'
                '            mac_list = raw\n'
                '        elseif type(raw) == "string" then\n'
                '            mac_list = {raw}\n'
                '        else\n'
                '            mac_list = {}\n'
                '        end\n'
                '    end\n'
                '\n'
                '    if type(uci_cursor.unload) == "function" then\n'
                '        pcall(function()\n'
                '            uci_cursor:unload("mac_blacklist")\n'
                '        end)\n'
                '    end\n'
                '\n'
                '    local uniq = {}\n'
                '    local normalized_list = {}\n'
                '    for _, mac in ipairs(mac_list) do\n'
                '        local normalized_mac = tostring(mac or ""):upper()\n'
                '        if normalized_mac ~= "" and not uniq[normalized_mac] then\n'
                '            uniq[normalized_mac] = true\n'
                '            table.insert(normalized_list, normalized_mac)\n'
                '        end\n'
                '    end\n'
                '    table.sort(normalized_list)\n'
                '    log(string.format("Loaded %d MAC blacklist entries", #normalized_list))\n'
                '    return normalized_list\n'
                'end\n'
                '\n')
            b = 'local function init_single_mac_rule()\n'
            if b not in ltext:
                print("  [!] rule_manager: init_single_mac_rule 锚点未命中")
            else:
                ltext = ltext.replace(b, lfn + b, 1)
                lchanged = True
                print("  [+] rule_manager.lua：load_mac_blacklist()")

        # 3) 同步函数：每周期把黑名单 mac_list 设到内核规则 102
        if "local function sync_blacklist_mac_rule()" not in ltext:
            lsfn = (
                'local function sync_blacklist_mac_rule()\n'
                '    local mac_list = load_mac_blacklist()\n'
                '    -- 24.10 的 set_macfilter_rule_mac_list 只有 2 个参数（mode 是 25.12 才有的）：\n'
                '    -- 24.10 内核里"规则里带 mac_list"即"命中断网"，黑名单语义正好对上\n'
                '    if set_macfilter_rule_mac_list(BLACKLIST_MAC_FILTER_RULE_ID, mac_list) then\n'
                '        log(string.format("MACFilter blacklist rule synced: %d devices", #mac_list))\n'
                '        return true\n'
                '    end\n'
                '    return false\n'
                'end\n'
                '\n'
                '-- 规则 102 必须先创建：mod_mac_filter_rule 找不到规则时直接\n'
                '-- "rule 102 not found" 返回，MAC 列表一条也进不去 —— 黑名单会变成摆设。\n'
                '-- 注意 flush_macfilter_rules() 会把 102 一起清掉（macfilter reinit 分支），\n'
                '-- 所以【启动】和【reinit】两条路径都要调用本函数重建。\n'
                'local function init_blacklist_mac_rule()\n'
                '    if create_macfilter_rule(BLACKLIST_MAC_FILTER_RULE_ID) then\n'
                '        log(string.format("MACFilter blacklist rule initialized: id=%d", BLACKLIST_MAC_FILTER_RULE_ID))\n'
                '    else\n'
                '        -- 已存在时内核返回 -1（不会重置已有列表），视为就绪\n'
                '        log(string.format("MACFilter blacklist rule already present: id=%d", BLACKLIST_MAC_FILTER_RULE_ID))\n'
                '    end\n'
                '    return true\n'
                'end\n'
                '\n')
            c = 'local function process_appfilter_rules(current_info)\n'
            if c not in ltext:
                print("  [!] rule_manager: process_appfilter_rules 锚点未命中")
            else:
                ltext = ltext.replace(c, lsfn + c, 1)
                lchanged = True
                print("  [+] rule_manager.lua：sync_blacklist_mac_rule()")

        # 4) 主循环调用（在 process_macfilter_rules 之后）
        if "sync_blacklist_mac_rule()" in ltext and "            sync_blacklist_mac_rule()" not in ltext:
            d = "            process_macfilter_rules(current_info)\n"
            if d not in ltext:
                print("  [!] rule_manager: 主循环锚点未命中")
            else:
                ltext = ltext.replace(d, d + "            sync_blacklist_mac_rule()\n", 1)
                lchanged = True
                print("  [+] rule_manager.lua：主循环加入黑名单同步")

        # 5) 两条初始化路径（启动 / macfilter reinit）都要重建规则 102
        has_call = any(ln.strip() == "init_blacklist_mac_rule()"
                       for ln in ltext.splitlines())
        if not has_call:
            out_lines = []
            hits = 0
            for ln in ltext.splitlines(True):
                out_lines.append(ln)
                if ln.strip() == "init_single_mac_rule()":
                    indent = ln[:len(ln) - len(ln.lstrip())]
                    out_lines.append(indent + "init_blacklist_mac_rule()\n")
                    hits += 1
            if hits:
                ltext = "".join(out_lines)
                lchanged = True
                print("  [+] rule_manager.lua：%d 处初始化路径创建黑名单规则" % hits)
            else:
                print("  [!] rule_manager: init_single_mac_rule() 调用点未命中")

        if lchanged:
            with open(lua, "w", encoding="utf-8", errors="surrogateescape") as f:
                f.write(ltext)

    # ---------- 4. src/Makefile：OBJS ----------
    mk = os.path.join(tree, MK_REL)
    if not os.path.isfile(mk):
        die("找不到 %s" % MK_REL)
    with open(mk, "r", encoding="utf-8", errors="surrogateescape") as f:
        mk_text = f.read()
    mk_changed = False

    if "fwx_firewall.o" in mk_text:
        print("  [skip] src/Makefile 已含 fwx_firewall.o")
    else:
        hit = False
        for line in mk_text.splitlines(True):
            if line.startswith("OBJS:="):
                new_line = line.rstrip("\n") + " fwx_firewall.o fwx_ext_compat.o\n"
                mk_text = mk_text.replace(line, new_line, 1)
                hit = True
                break
        if not hit:
            die("锚点缺失：src/Makefile 里没有 OBJS:= 行")
        mk_changed = True
        print("  [+] src/Makefile：OBJS 追加 fwx_firewall.o fwx_ext_compat.o")

    # ---------- 5. 顶层 fwxd/Makefile：mac_blacklist.config 安装 ----------
    # 注意：安装行在顶层 fwxd/Makefile（install 段），OBJS 在 src/Makefile —— 两个文件别混。
    # Makefile 命令行以 tab 开头，锚点必须含 tab。
    mk_top = os.path.join(tree, MK_TOP_REL)
    if not os.path.isfile(mk_top):
        print("  [!] 找不到顶层 %s，跳过 mac_blacklist.config 安装" % MK_TOP_REL)
    else:
        with open(mk_top, "r", encoding="utf-8", errors="surrogateescape") as f:
            top_text = f.read()
        anchor_tab = "\t"
        prev = (anchor_tab + "$(INSTALL_DATA) ./files/appfilter_whitelist.config "
                "$(1)/etc/config/appfilter_whitelist\n")
        # 新增的 uci 默认配置：黑名单（mac_blacklist.base.mac_list）与
        # 记录白名单（fwx_record.@whitelist[0].whitelist）。两者都是 25.12
        # 才有的包，24.10 缺文件时写入会失败，必须随包安装。
        if prev not in top_text:
            print("  [!] 顶层 Makefile 安装行锚点未找到——mac_blacklist / fwx_record 的 uci 包将不存在")
        else:
            for cfg in ("mac_blacklist", "fwx_record"):
                need = "%s.config $(1)/etc/config/%s" % (cfg, cfg)
                if need in top_text:
                    print("  [skip] 顶层 Makefile 已安装 %s.config" % cfg)
                    continue
                line = anchor_tab + "$(INSTALL_DATA) ./files/" + need + "\n"
                top_text = top_text.replace(prev, prev + line, 1)
                prev = prev + line
                print("  [+] 顶层 Makefile：追加 %s.config 安装行" % cfg)
            with open(mk_top, "w", encoding="utf-8", errors="surrogateescape") as f:
                f.write(top_text)

    if mk_changed:
        with open(mk, "w", encoding="utf-8", errors="surrogateescape") as f:
            f.write(mk_text)

    # ---------- 6. 回读自校验 ----------
    with open(ubus, "r", encoding="utf-8", errors="surrogateescape") as f:
        final = f.read()
    apis = ("get_firewall", "set_firewall", "get_init_status", "set_init_status",
            "get_history_traffic_stats", "get_record_whitelist",
            "add_record_whitelist", "del_record_whitelist",
            "get_user_daily_summary", "get_user_parental_control_rules",
            "get_mac_blacklist", "add_mac_blacklist", "del_mac_blacklist",
            "get_system_base_info", "get_parental_control_detail")
    for api in apis:
        if '"%s", fwx_api_%s' % (api, api) not in final:
            die("自校验失败：分派表里没有 %s" % api)

    # 前向声明必须严格早于第一个调用点（build-23 的教训）
    decl_pos = final.find("int fwx_ext_record_whitelisted(const char *mac);")
    first_call = min(
        [p for p in (final.find("fwx_ext_record_whitelisted(record->mac)"),
                     final.find("fwx_ext_record_whitelisted(node->mac)")) if p >= 0]
        or [-1])
    if decl_pos < 0 or first_call < 0 or decl_pos > first_call:
        die("自校验失败：fwx_ext_record_whitelisted 声明（%s）必须早于首个调用点（%s）" % (
            decl_pos, first_call))

    for name in ("fwx_firewall.c", "fwx_ext_compat.c"):
        if not os.path.isfile(os.path.join(src_dir, name)):
            die("自校验失败：%s 没落地" % name)
    ext_src = open(os.path.join(src_dir, "fwx_ext_compat.c"),
                   encoding="utf-8", errors="surrogateescape").read()
    for sym in ("fwx_ext_record_whitelisted", "fwx_api_get_history_traffic_stats",
                "fwx_api_get_user_daily_summary",
                "fwx_api_get_user_parental_control_rules",
                "fwx_api_get_mac_blacklist", "fwx_api_get_parental_control_detail",
                "fwx_api_get_system_base_info"):
        if sym not in ext_src:
            die("自校验失败：fwx_ext_compat.c 里没有 %s" % sym)

    # 黑名单执行链必须完整：规则 102 不创建的话，mod_mac_filter_rule 会
    # "rule not found"，整个黑名单是摆设（本轮修复的缺陷）。
    lbl = os.path.join(tree, FWXD_TOP_REL, "files", "rule_manager.lua")
    if os.path.isfile(lbl):
        lb = open(lbl, encoding="utf-8", errors="surrogateescape").read()
        if "local function init_blacklist_mac_rule()" not in lb:
            die("自校验失败：rule_manager.lua 缺 init_blacklist_mac_rule()（规则 102 不会被创建）")
        if not any(ln.strip() == "init_blacklist_mac_rule()" for ln in lb.splitlines()):
            die("自校验失败：rule_manager.lua 里没有 init_blacklist_mac_rule() 的调用")
        if "set_macfilter_rule_mac_list(BLACKLIST_MAC_FILTER_RULE_ID, mac_list, 2)" in lb:
            die("自校验失败：24.10 的 set_macfilter_rule_mac_list 只有 2 个参数，传 3 个无效")
        if "sync_blacklist_mac_rule()" not in lb:
            die("自校验失败：rule_manager.lua 缺黑名单同步调用")
    ft = os.path.join(tree, FWXD_TOP_REL, "files", "feature.cfg")
    if os.path.isfile(ft):
        with open(ft, encoding="utf-8", errors="replace") as f:
            n_feat = sum(1 for ln in f if ln[:1].isdigit())
        if n_feat < 200:
            die("自校验失败：feature.cfg 只有 %d 条特征（应为社区库 300+ 条）" % n_feat)

    # uci 默认配置必须真的进镜像（否则对应页面的写入直接失败）
    if os.path.isfile(os.path.join(tree, MK_TOP_REL)):
        top_chk = open(os.path.join(tree, MK_TOP_REL),
                       encoding="utf-8", errors="surrogateescape").read()
        for cfg in ("mac_blacklist", "fwx_record"):
            if "%s.config $(1)/etc/config/%s" % (cfg, cfg) not in top_chk:
                die("自校验失败：顶层 Makefile 没有安装 %s.config" % cfg)
            if not os.path.isfile(os.path.join(tree, FWXD_TOP_REL, "files", cfg + ".config")):
                die("自校验失败：files/%s.config 没落地" % cfg)

    print("  ✔ 自校验通过：%d 个接口在分派表中，源文件与过滤就位" % len(apis))
    return 0


if __name__ == "__main__":
    sys.exit(main())
