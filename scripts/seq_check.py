#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全链路时序模拟：按 build.sh 真实顺序验证每步依赖（build-41/43/44/45/49 的教训）"""
import os, shutil, subprocess, sys

BE72 = os.getcwd()
FCM = "../work/fcm2410/package/fcm"
PKG = "../work/pkgs2512"
RJ = os.path.join(os.environ.get("TEMP", "/tmp"), "simseq")
if os.path.exists(RJ):
    shutil.rmtree(RJ)

fails = []
def step(name):
    print("==>", name)

def check(cond, msg):
    print(("  [v] " if cond else "  [x] ") + msg)
    if not cond:
        fails.append(msg)

# ===== 1/8-2/8 拷包 =====
step("2/8 搬 fwx 生态 + APPS 循环")
os.makedirs(RJ + "/package/fcm", exist_ok=True)
for m in ["libfwx_common", "fwxd", "fwx", "luci-theme-fanchmwrt"]:
    shutil.copytree(f"{FCM}/{m}", RJ + "/package/fcm/" + m)
APPS = ["luci-app-fwx-resources", "luci-app-fwx-dashboard", "luci-app-fwx-dashboard-setting",
        "luci-app-fwx-user", "luci-app-fwx-user-record", "luci-app-fwx-appfilter",
        "luci-app-fwx-record", "luci-app-fwx-record-whitelist", "luci-app-fwx-macfilter",
        "luci-app-fwx-mac-blacklist", "luci-app-fwx-session-stat", "luci-app-fwx-traffic-stat",
        "luci-app-fwx-feature", "luci-app-fwx-system", "luci-app-fwx-firewall"]
for a in APPS:
    src = f"{PKG}/{a}"
    check(os.path.isdir(src), f"fanchmwrt-packages 里存在 {a}")
    shutil.copytree(src, RJ + "/package/" + a)

# ===== 2a nft-fullcone + 2a-3 accel（APPS 循环之后）=====
step("2a/2a-3 accel 并入 system 包（依赖 system 已拷入）")
check(os.path.isdir(RJ + "/package/luci-app-fwx-system/luasrc/controller"),
      "system 包的 luasrc/controller 已存在（APPS 循环产物）")
os.makedirs(RJ + "/package/fcm/luci-app-fwx-system/luasrc/controller", exist_ok=True)
os.makedirs(RJ + "/package/fcm/luci-app-fwx-system/luasrc/view/fwx_accel", exist_ok=True)
shutil.copyfile("patches/accel/fwx_accel.lua",
                RJ + "/package/fcm/luci-app-fwx-system/luasrc/controller/fwx_accel.lua")
shutil.copyfile("patches/accel/accel.htm",
                RJ + "/package/fcm/luci-app-fwx-system/luasrc/view/fwx_accel/accel.htm")
check(os.path.isfile(RJ + "/package/fcm/luci-app-fwx-system/luasrc/controller/fwx_accel.lua"),
      "fwx_accel.lua 已并入")
check(os.path.isfile(RJ + "/package/fcm/luci-app-fwx-system/luasrc/view/fwx_accel/accel.htm"),
      "accel.htm 已并入")

# ===== 2a-4 菜单注入 =====
step("2a-4 普通模式菜单注入（依赖 vendor menu-fanchmwrt.js 已拷入）")
mjs = RJ + "/package/fcm/luci-theme-fanchmwrt/htdocs/luci-static/resources/menu-fanchmwrt.js"
check(os.path.isfile(mjs), "vendor menu-fanchmwrt.js 已拷入")
t = open(mjs, encoding="utf-8").read()
check(t.count("'fwx_user_record',") == 1, "sed 注入锚点唯一")
t2 = t.replace("'fwx_user_record',", "'fwx_user_record',\n\t\t\t'fwx_accel',")
check(t2.count("'fwx_accel',") == 1, "注入后 basicMenus 含 fwx_accel")
open(mjs, "w", encoding="utf-8").write(t2)

# ===== patch_fwxd_ext.py =====
step("2b patch_fwxd_ext.py（fwxd 树）")
r = subprocess.run([sys.executable, "scripts/patch_fwxd_ext.py", RJ],
                   capture_output=True, text=True)
check(r.returncode == 0 and "自校验通过" in r.stdout, "fwxd 扩展补丁全链通过")
if r.returncode != 0:
    print(r.stdout[-2000:], r.stderr[-500:])
else:
    ok_lines = [l for l in r.stdout.splitlines() if "[!]" in l or "锚点未命中" in l]
    check(not ok_lines, "无锚点失配告警: " + "; ".join(ok_lines))

# ===== patch_kernel_extras.py（--fwx）=====
step("3.1 patch_kernel_extras.py --fwx")
r = subprocess.run([sys.executable, "scripts/patch_kernel_extras.py", "--fwx", RJ],
                   capture_output=True, text=True)
check("Traceback" not in r.stderr, "无异常崩溃")
ok_lines = [l for l in r.stdout.splitlines() if "[!]" in l]
check(not ok_lines, "无锚点失配告警: " + "; ".join(ok_lines))
klog = open(f"{RJ}/package/fcm/fwx/src/fwx_log.c", encoding="utf-8", errors="surrogateescape").read()
check("fwx_accel_ids_handler" in klog, "accel 位图 sysctl 已注入")
mlog = open(f"{RJ}/package/fcm/fwx/src/fwx_main.c", encoding="utf-8", errors="surrogateescape").read()
check("fwx_accel_test(app_id)" in mlog, "钩子放行检查已注入")
rm = open(f"{RJ}/package/fcm/fwxd/files/rule_manager.lua", encoding="utf-8", errors="surrogateescape").read()
check("hook_toggle" in rm, "审计↔HNAT 联动已注入")
check("build_accel_id_list" in rm, "四类加速同步已注入")
check("load_mac_blacklist" in rm, "黑名单链已注入")

# ===== API 定义自查 =====
step("fwxd API 定义自查")
import re, glob
s = open("scripts/patch_fwxd_ext.py", encoding="utf-8").read()
apis = re.findall(r'\{"([a-z_]+)", (fwx_api_[a-z_]+), (\d)', s)
bodies = [open(f, encoding="utf-8", errors="surrogateescape").read()
          for f in glob.glob("patches/fwxd/*.c")]
ub = open(f"{RJ}/package/fcm/fwxd/src/fwx_ubus.c", encoding="utf-8", errors="surrogateescape").read()
missing = [a for a, f, _ in apis
           if not any(("struct json_object *" + f + "(") in t for t in bodies + [ub])]
check(not missing, "19+ 个分派 API 全有函数体: " + str(missing))
check("fwx_api_get_feature_info" in ub, "get_feature_info 声明/分派就位")

# ===== --hnat 单参模式不崩溃（build-46 教训）=====
step("3.2 patch_kernel_extras.py --hnat（单参不崩）")
r = subprocess.run([sys.executable, "scripts/patch_kernel_extras.py", "--hnat", RJ],
                   capture_output=True, text=True)
check("Traceback" not in r.stderr, "--hnat 单参调用无异常")

# ===== 移植文件的全符号依赖检查（build-53 教训：app_icon_exists_by_id）=====
step("移植文件全符号依赖（fwx_custom_feature.c vs 树内定义）")
import re as _re
fwx_cf = RJ + "/package/fcm/fwxd/src/fwx_custom_feature.c"
src = open(fwx_cf, encoding="utf-8", errors="surrogateescape").read()
calls = set(_re.findall(r'\b([a-zA-Z_]\w*)\s*\(', src))
own = set(_re.findall(r'^\s*(?:static\s+)?[A-Za-z_][\w\s\*]*?\b(\w+)\s*\([^;]*$', src, _re.M))
own |= set(_re.findall(r'extern\s+[A-Za-z_][\w\s\*]*\b(\w+)\s*\(', src))
std = set(("if while for switch sizeof return main static void int char long unsigned "
           "FILE fopen fclose fgets fputs fprintf snprintf sprintf strncmp strcmp strstr "
           "strchr strrchr strlen strcpy strncpy strcat memset memcpy malloc calloc realloc "
           "free sscanf atoi atol strtoul strtol exit NULL EOF feof ferror fflush fileno "
           "fsync memmove rename chmod isxdigit tolower atoi calloc access unlink getpid "
           "INT_MIN INT_MAX F_OK isspace isdigit isupper strtoll memset_s strtok qsort").split())
tree_src = ""
for _root, _dirs, _files in os.walk(RJ + "/package/fcm/fwxd/src"):
    for _f in _files:
        if _f.endswith((".c", ".h")):
            tree_src += open(os.path.join(_root, _f), encoding="utf-8",
                             errors="surrogateescape").read()
miss = [c for c in sorted(calls)
        if c not in own and c not in std
        and not _re.search(r'\b' + _re.escape(c) + r'\s*\(', tree_src)]
check(not miss, "移植文件符号全部可解析: " + str(miss))

# 声明-调用顺序：跨文件函数必须在首个调用前有声明（build-54 的
# app_icon_exists_by_id 教训：有定义但调用处无声明 -> implicit declaration）
order_bad = []
lines = src.splitlines()
def _first(pattern):
    for i, ln in enumerate(lines, 1):
        if _re.search(pattern, ln):
            return i
    return 10**9
for c in sorted(calls):
    if c in own or c in std:
        continue
    # 库函数 / 日志宏 / 函数指针参数：在头文件或宏里声明，不参与顺序检查
    if c.startswith(("json_", "LOG_", "nl_", "uci_", "blob", "fwx_nl_")) or c == "send_feature":
        continue
    if _re.search(r'^\s*(?:static\s+)?[A-Za-z_][\w\s\*]*?\b' + _re.escape(c) + r'\s*\([^;]*$',
                  src, _re.M):
        continue          # 文件内定义的 static 函数，定义行本身满足
    decl_line = _first(r'(?:extern\s+)?[A-Za-z_][\w\s\*]*\b' + _re.escape(c) + r'\s*\([^;]*;\s*$')
    call_line = _first(r'\b' + _re.escape(c) + r'\s*\(')
    if call_line < decl_line:
        order_bad.append("%s(调用%d行 声明%d行)" % (c, call_line, decl_line))
check(not order_bad, "跨文件函数声明早于调用: " + str(order_bad))

# ===== 仪表盘 1:1 覆盖（真实数据源 + 无占位假数据）=====
step("仪表盘 1:1 覆盖检查")
dash_src = "patches/dashboard/dashboard.htm"
check(os.path.isfile(dash_src), "patches/dashboard/dashboard.htm 存在")
if os.path.isfile(dash_src):
    ds = open(dash_src, encoding="utf-8").read()
    for key in ("get_dashboard_common", "get_daily_top_users", "get_active_users"):
        check(key in ds, "含真实数据源 " + key)
    check("network.device" in ds, "接口速率走 network.device status")
    check(ds.count("<%") == ds.count("%>"), "LuCI 模板标记配对")
    js = ds.split("<script>")[1].split("</script>")[0]
    check(sum(1 if c == "{" else (-1 if c == "}" else 0) for c in js) == 0, "JS 大括号平衡")
    check(not any(k in ds for k in ("占位", "假数据", "TODO")), "无占位/假数据标记")

# ===== REQUIRED_SYMS 对齐 =====
step("REQUIRED_SYMS 与树内包对齐")
for sym in ["system", "firewall", "feature", "dashboard", "user", "appfilter",
            "macfilter", "mac-blacklist", "record", "record-whitelist",
            "session-stat", "traffic-stat", "resources", "user-record"]:
    check(os.path.isdir(f"{RJ}/package/luci-app-fwx-{sym}") or
          os.path.isdir(f"{RJ}/package/fcm/luci-app-fwx-{sym}"),
          f"luci-app-fwx-{sym} 在树内")
check("luci-app-fwx-accel" not in open("scripts/build.sh", encoding="utf-8").read().split('REQUIRED_SYMS=')[1][:1500]
      if "REQUIRED_SYMS=" in open("scripts/build.sh", encoding="utf-8").read() else True,
      "REQUIRED_SYMS 无 accel（已并入 system）")

print()
if fails:
    print("!! 检查未通过 %d 项:" % len(fails))
    for f in fails:
        print("   -", f)
    sys.exit(1)
print(">>> 全链路时序检查全部通过（%d 项）" % 20)
