#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自检：页面真实调用到的 ubus api，fwxd 是否提供。

背景：界面包（luci-app-fwx-*）与守护进程（fwxd）是两个仓库，页面通过
    ubus call fwx common '{"api": "xxx"}'
对接。上游改接口名不会通知，取错版本就会「页面能打开、数据是空的、保存没反应」。

第一版把控制器里声明过的 api 全算进去，结果大量假阳性：控制器里常留着一堆
没被任何页面调用的路由（比如 macfilter 的 get_mac_filter_base / _time / _user
在 24.10.4 的 fwxd 里确实没有，但三个页签 rules/whitelist/advance 根本不用它们，
只用 get_mac_filter_rules / _whitelist / _adv）。
故改为两级：
  declared   —— 控制器声明过的（call("x") 或 .api="x"）
  referenced —— 视图（.htm/.js）里真的出现过的
只有 referenced ∩ declared 才算「页面会用到的接口」，拿它去和 fwxd 比。
控制器独有的那些单独列为 [i]，只提示、不算失败。

用法：  python3 check_api.py <源码树根>
返回：  0 = 全部命中；1 = 有缺失（调用方决定是告警还是失败）
"""

import os
import re
import sys

APP_PREFIX = "luci-app-fwx-"
FWXD_UBUS = "package/fcm/fwxd/src/fwx_ubus.c"

API_PATTERNS = (
    # req_obj.api = "x" / .api = "x"
    re.compile(r'\.api\s*=\s*"([A-Za-z0-9_]+)"'),
    # { api = "x" }（不带点号，也别忘了前面不能是别的标识符尾巴）
    re.compile(r'(?<![A-Za-z0-9_.])api\s*=\s*"([A-Za-z0-9_]+)"'),
    re.compile(r'"api"\s*:\s*"([A-Za-z0-9_]+)"'),
    re.compile(r"'api'\s*:\s*'([A-Za-z0-9_]+)'"),
)
CALL_PATTERN = re.compile(r'call\(\s*"([A-Za-z0-9_]+)"\s*\)')
TOKEN_PATTERN = re.compile(r'([a-z][a-z0-9_]{3,})')

VIEW_DIRS = ("luasrc/view", "htdocs", os.path.join("root", "usr", "share"))
CTRL_DIR = os.path.join("luasrc", "controller")


def read(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8", "replace")


def app_dirs(root):
    """界面包可能在源码树根，也可能被拷到 <root>/package/ 下（本仓库是后者）。
    两处都扫，否则会静默输出「没扫到界面包」而跳过整个自检 —— 这个坑真踩过。"""
    found = []
    for base_dir in (root, os.path.join(root, "package")):
        if not os.path.isdir(base_dir):
            continue
        for name in sorted(os.listdir(base_dir)):
            if not name.startswith(APP_PREFIX):
                continue
            path = os.path.join(base_dir, name)
            if os.path.isdir(path):
                found.append((name, path))
    return found


def walk_text(base, subdirs, exts):
    for sub in subdirs:
        top = os.path.join(base, sub)
        if not os.path.isdir(top):
            continue
        for dirpath, _, files in os.walk(top):
            for fn in files:
                if fn.endswith(exts):
                    try:
                        yield read(os.path.join(dirpath, fn))
                    except OSError:
                        continue


def collect(root):
    """返回 {app: {"declared": set, "referenced": set}}"""
    out = {}
    for name, base in app_dirs(root):
        declared = set()
        for txt in walk_text(base, (CTRL_DIR,), (".lua",)):
            for pat in API_PATTERNS:
                declared.update(pat.findall(txt))
            declared.update(CALL_PATTERN.findall(txt))

        referenced = set()
        for txt in walk_text(base, VIEW_DIRS, (".htm", ".js", ".lua")):
            for pat in API_PATTERNS:
                referenced.update(pat.findall(txt))
            # 路由路径（如 admin/dashboard_api/get_init_status）里的最后一段
            referenced.update(
                m.group(1)
                for m in re.finditer(r'/[A-Za-z0-9_]+/([a-z][a-z0-9_]{3,})', txt)
            )
            referenced.update(TOKEN_PATTERN.findall(txt))

        if declared or referenced:
            out[name] = {"declared": declared, "referenced": referenced}
    return out


def main():
    if len(sys.argv) < 2:
        print("usage: check_api.py <tree>")
        return 2
    root = sys.argv[1]
    ubus = os.path.join(root, FWXD_UBUS)
    if not os.path.isfile(ubus):
        print("  [!] 找不到 %s，跳过接口自检" % FWXD_UBUS)
        return 0

    provided = set(re.findall(r'"([a-z][a-z0-9_]{2,})"', read(ubus)))
    data = collect(root)
    if not data:
        print("  [!] 没扫到界面包，跳过自检")
        return 0

    total_missing = 0
    print("  接口自检（页面实际调用 → fwxd）：")
    for app in sorted(data):
        declared = data[app]["declared"]
        # 页面会用到 = 视图里出现过、且控制器确实声明过的
        used = data[app]["referenced"] & declared
        if not used:
            continue
        missing = sorted(used - provided)
        if missing:
            total_missing += len(missing)
            print("    [x] %-32s 缺 %2d: %s" % (app, len(missing), ", ".join(missing)))
        else:
            print("    [v] %-32s %d 个接口全部命中" % (app, len(used)))
        idle = sorted(x for x in declared - used if not x.startswith("api_"))
        if idle:
            print("        [i] 另有 %d 个控制器路由未被页面引用（不算缺失）：%s"
                  % (len(idle), ", ".join(idle[:6]) + (" ..." if len(idle) > 6 else "")))

    if total_missing:
        print("  !! 共 %d 个接口对不上：页面会取不到数据（不是「页面打不开」）。"
              % total_missing)
        print("     多半是界面包与 fwxd 版本不同期，检查 build.sh 里的 PKG_REF / FCM_REF。")
        return 1
    print("  ✔ 页面用到的接口 fwxd 都提供")
    return 0


if __name__ == "__main__":
    sys.exit(main())
