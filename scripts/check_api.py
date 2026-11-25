#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自检：界面包调用到的 ubus api，fwxd 是否真的提供。

背景：界面包（luci-app-fwx-*）与守护进程（fwxd）是两个仓库，界面通过
    ubus call fwx common '{"api": "xxx"}'
对接。上游改接口名不会通知，取错版本就会「页面能打开、数据是空的、保存没反应」。

实测过一次：界面包取 main@HEAD 配 24.10.4 的 fwxd，macfilter 缺 9 个 api、
dashboard 缺 2 个、user 缺 2 个。这个脚本把那次排查自动化掉。

用法：  python3 check_api.py <源码树根>
返回：  0 = 全部命中；1 = 有缺失（调用方决定是告警还是失败）
"""

import os
import re
import sys

APP_PREFIX = "luci-app-fwx-"
FWXD_UBUS = "package/fcm/fwxd/src/fwx_ubus.c"

API_PATTERNS = (
    re.compile(r'\.api\s*=\s*"([A-Za-z0-9_]+)"'),
    re.compile(r'"api"\s*:\s*"([A-Za-z0-9_]+)"'),
    re.compile(r"'api'\s*:\s*'([A-Za-z0-9_]+)'"),
)


def read(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8", "replace")


def collect_used(root):
    used = {}
    for name in sorted(os.listdir(root)):
        if not name.startswith(APP_PREFIX):
            continue
        base = os.path.join(root, name)
        if not os.path.isdir(base):
            continue
        apis = set()
        for dirpath, _, files in os.walk(base):
            for fn in files:
                if not fn.endswith((".lua", ".htm", ".js")):
                    continue
                try:
                    txt = read(os.path.join(dirpath, fn))
                except OSError:
                    continue
                for pat in API_PATTERNS:
                    apis.update(pat.findall(txt))
        if apis:
            used[name] = apis
    return used


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
    used = collect_used(root)
    if not used:
        print("  [!] 没扫到界面包的 api 调用，跳过自检")
        return 0

    total_missing = 0
    print("  接口自检（界面包 → fwxd）：")
    for app in sorted(used):
        missing = sorted(a for a in used[app] if a not in provided)
        if missing:
            total_missing += len(missing)
            print("    [x] %-32s 缺 %2d: %s" % (app, len(missing), ", ".join(missing)))
        else:
            print("    [v] %-32s %d 个接口全部命中" % (app, len(used[app])))

    if total_missing:
        print("  !! 共 %d 个接口对不上：多半是界面包与 fwxd 版本不同期，"
              "检查 build.sh 里的 PKG_REF / FCM_REF 是否同一天。" % total_missing)
        return 1

    print("  ✔ 所有界面包与 fwxd 接口一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
