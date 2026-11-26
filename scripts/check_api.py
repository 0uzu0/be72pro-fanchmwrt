#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自检：页面真实调用到的 ubus api，fwxd 是否提供。

背景：界面包（luci-app-fwx-*）与守护进程（fwxd）是两个仓库，页面通过
    ubus call fwx common '{"api": "xxx"}'
对接。上游改接口名不会通知，取错版本就会「页面能打开、数据是空的、保存没反应」。

三轮才把口径调准，过程值得记下来：

  v1  把控制器里出现过的 api 全算 → 13 个「缺失」，其中 11 个是假阳性
  v2  加上「视图里出现过」才算 → 反而涨到 21 个。原因：把 LuCI 的**路由名**
      当成了 fwxd 的 **api 名**（路由 get_system_base_info 内部可能调的是
      get_system_info），两者经常不同名。
  v3  沿链路走：entry 注册 → 视图文件 → 视图引用的路由 → 控制器函数 → 函数里
      真正的 .api = "x"。同时**排除未被任何 entry 注册、也没人引用的死视图**
      （例：macfilter/user.htm 里确实调 4 个 *_user 接口，但控制器只注册了
        rules / whitelist / advance，那个视图根本进不去）。

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
    re.compile(r'(?<![A-Za-z0-9_.])api\s*=\s*"([A-Za-z0-9_]+)"'),
    re.compile(r'"api"\s*:\s*"([A-Za-z0-9_]+)"'),
    re.compile(r"'api'\s*:\s*'([A-Za-z0-9_]+)'"),
    # 部分控制器（如 luci-app-fwx-firewall）不写 .api = "x"，
    # 而是先做一层封装：ubus_call("get_firewall", data)。
    # 只匹配字符串字面量；变量形式（ubus_call(api, data)）静态解析不了，忽略。
    re.compile(r'ubus_call\(\s*"([A-Za-z0-9_]+)"'),
)
FUNC_RE = re.compile(r'function\s+([A-Za-z0-9_]+)\s*\(')
# entry({...}, cbi("macfilter/rules") ...) / template("fwx_network/lan") / call("fn")
TARGET_RE = re.compile(r'(?:cbi|template)\(\s*"([A-Za-z0-9_/]+)"')
CALL_RE = re.compile(r'call\(\s*"([A-Za-z0-9_]+)"')
TOKEN_RE = re.compile(r'([a-z][a-z0-9_]{3,})')


def read(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8", "replace")


def app_dirs(root):
    """界面包可能在源码树根，也可能被拷到 <root>/package/ 下（本仓库是后者）。"""
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


def apis_in(text):
    out = set()
    for pat in API_PATTERNS:
        out.update(pat.findall(text))
    return out


def parse_controller(text):
    """返回 {函数名: {该函数里用到的 api}}"""
    funcs = {}
    marks = [(m.group(1), m.end()) for m in FUNC_RE.finditer(text)]
    for i, (name, body_start) in enumerate(marks):
        body_end = marks[i + 1][1] if i + 1 < len(marks) else len(text)
        funcs[name] = apis_in(text[body_start:body_end])
    return funcs


def ctrl_files(app):
    top = os.path.join(app, "luasrc", "controller")
    if not os.path.isdir(top):
        return []
    return [os.path.join(top, f) for f in sorted(os.listdir(top)) if f.endswith(".lua")]


def view_path(app, target):
    """cbi("macfilter/rules") / template("fwx_network/lan") → 视图文件路径"""
    for ext in (".htm", ".lua"):
        p = os.path.join(app, "luasrc", "view", target + ext)
        if os.path.isfile(p):
            return p
    return None


def analyze(app):
    funcs = {}
    targets = set()
    for cf in ctrl_files(app):
        txt = read(cf)
        funcs.update(parse_controller(txt))
        targets.update(TARGET_RE.findall(txt))
        targets.update(CALL_RE.findall(txt))
        # 控制器里 render("user/user_status") / include("x/y") 这类引用的视图，
        # 也要算可达（它们不是 entry 注册的独立页面，而是某个页面里的子视图）
        for cand in re.findall(r'"([A-Za-z0-9_]+/[A-Za-z0-9_/]+)"', txt):
            if view_path(app, cand):
                targets.add(cand)

    views = [p for p in (view_path(app, t) for t in sorted(targets)) if p]

    # 视图里用 <%+user/user_history%> 引用的子视图也是可达的，跟着展开一层
    extra = []
    for v in views:
        for inc in re.findall(r'<%\+\s*([A-Za-z0-9_/]+)\s*%>', read(v)):
            sub = view_path(app, inc)
            if sub and sub not in views:
                extra.append(sub)
    views += extra

    used = set()
    for v in views:
        txt = read(v)
        used |= apis_in(txt)
        for tok in set(TOKEN_RE.findall(txt)):
            # 视图引用的路由名 → 控制器函数 → 函数里的真实 api
            if tok in funcs:
                used |= funcs[tok]

    declared = set()
    for apis in funcs.values():
        declared |= apis
    return used, declared


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
    apps = app_dirs(root)
    if not apps:
        print("  [!] 没扫到界面包，跳过自检")
        return 0

    total_missing = 0
    print("  接口自检（可达页面 → 控制器 → fwxd 实际 api）：")
    for name, path in apps:
        used, declared = analyze(path)
        if not used:
            continue
        missing = sorted(used - provided)
        if missing:
            total_missing += len(missing)
            print("    [x] %-32s 缺 %2d: %s" % (name, len(missing), ", ".join(missing)))
        else:
            print("    [v] %-32s %d 个接口全部命中" % (name, len(used)))
        idle = sorted(declared - used)
        if idle:
            print("        [i] %d 个接口只在未被注册的视图里出现（不算缺失）：%s"
                  % (len(idle), ", ".join(idle[:5]) + (" ..." if len(idle) > 5 else "")))

    if total_missing:
        print("  !! 共 %d 个接口对不上：对应页面/区块会取不到数据（不是打不开）。" % total_missing)
        print("     换了 PKG_REF / FCM_REF 仍报同样的名字，说明两个版本都没有该 api，"
              "换版本无用，只能补 fwxd 实现或接受缺块。")
        return 1
    print("  ✔ 可达页面用到的接口 fwxd 都提供")
    return 0


if __name__ == "__main__":
    sys.exit(main())
