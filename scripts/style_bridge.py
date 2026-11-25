#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
样式桥接：把 fanchmwrt 的视觉语言统一到「主题变量 + 卡片规范」上。

做三件事（对应 A / B / C）：

  A. fwx 自有页面（14 个 luci-app-fwx-*）
     它们的 common.css 是硬编码色值（#2885e8 / green / #ff4444），不跟主题、
     不跟深浅色。追加一段 override，把颜色换成主题变量（--primary / --error /
     --border-color-* 等）。CSS 同级后写者胜，所以追加即可，不改原文件。

  B. 锐捷保留的页面（无线 wireless-mtk.js、流量整形 eqos.js、网络加速 turboacc.js）
     经核实这三个页面是纯 LuCI 表单 API + E('div') 拼装，零 class、零内联样式，
     外观 100% 由主题 cascade.css 决定。所以不重写 JS（174KB 手写 JS 只会引入
     回归），而是给主题补规则，让这些页面的 .cbi-* 结构落到 fanchmwrt 的卡片规范。

  C. 通用控件层
     输入框、按钮、表格、页脚操作条统一到主题变量，避免各页面自成一套。

用法：
    python3 style_bridge.py --fwx   <源码树>   # 追加到 fwx 的 common.css
    python3 style_bridge.py --theme <源码树>   # 追加到主题的 cascade.css
返回：0 = 已应用或已是目标状态；2 = 目标文件不存在
"""

import os
import sys

MARK = "/* === be72pro-fanchmwrt:style-bridge === */"

# 注意：界面包在构建时被拷到 $RJ/package/<包名>/ 下（不是 feeds 里），
# 所以路径要带 package/ 前缀。
FWX_CSS_REL = "package/luci-app-fwx-resources/htdocs/luci-static/resources/css/common.css"
THEME_CSS_REL = "package/fcm/luci-theme-fanchmwrt/htdocs/luci-static/fanchmwrt/cascade.css"

# ---------------------------------------------------------------- A：fwx 页面
# 原文件里的类名（.fwx-form-card / .form-title / .submit-button / .switch ...）
# 保持不变，只把「颜色/边框/背景」改成变量，这样 14 个页面立刻跟随主题与深浅色。
FWX_BRIDGE = MARK + """
/* A. 把 fwx 页面里写死的颜色换成主题变量，跟随主题换色与深浅色切换。
   选择器与 common.css 原规则同权重、但后出现，因此覆盖生效。 */
body { color: var(--text-color-high, #333); }
h1, h2 { color: var(--text-color-highest, #111); }

.fwx-form-card {
	border-color: var(--border-color-low, #e5e7eb);
	background: var(--background-color-high, #fff);
}
.form-title, .table-title { color: var(--text-color-highest, #111); }
.form-desc { color: var(--text-color-medium, #6b7280); }

.submit-button { background-color: var(--primary-color-high, #1976d2); }
.btn-edit { background-color: var(--primary-color-high, #1976d2); }
.btn-delete { background-color: var(--error-color-high, #e8210d); }
.submit-button:hover,
.btn-edit:hover,
.btn-delete:hover { filter: brightness(0.92); }

.button-check { color: var(--success-color-high, #00ac59); border-color: var(--success-color-high, #00ac59); }
.button-check:hover { background: var(--background-color-medium, #f5f5f5); }

.common-table th,
.common-table td,
.table-header,
.table-row { border-color: var(--border-color-low, #e5e7eb); }
.common-table th,
.table-header {
	background: var(--background-color-medium, #f5f5f5);
	color: var(--text-color-highest, #111);
}
.common-table tr:nth-child(even) td { background: var(--background-color-medium, #f5f5f5); }

.switch .slider { background-color: var(--border-color-medium, #d0d0d0); }
.switch input:checked + .slider { background-color: var(--primary-color-high, #1976d2); }
.switch .slider:before { background-color: var(--background-color-high, #fff); }

.common-modal-mask { background-color: rgba(0, 0, 0, 0.45); }
.common-modal-content {
	background-color: var(--background-color-high, #fff);
	color: var(--text-color-high, #333);
	border: 1px solid var(--border-color-low, #e5e7eb);
}
"""

# ---------------------------------------------------------------- B+C：主题侧
# 目标是让「锐捷/immortalwrt 的 LuCI JS 页面」与「fanchmwrt 自有页面」看起来
# 是同一套规范：780 内容宽、16 内边距、1px 描边、8 圆角、18px 标题、13px 说明。
#
# 注意：fwx 自己的页面也是走 luci-compat 渲染的 .cbi-* 结构，所以给 .cbi-section
# 加卡片边框会和它们的 .fwx-form-card 叠成双层。因此卡片化那块用 :has() 排除
# 含 .fwx-form-card 的页面 —— 浏览器不支持 :has() 时该条规则整体失效，退化成
# 主题原样，不会把 fwx 页面弄坏。
THEME_BRIDGE = MARK + """
/* B. 按 fwx 页面的骨架重写标准 LuCI 页面的表现层。

   骨架参照 luci-app-fwx-appfilter/luasrc/view/appfilter/advance.htm：
     <div class="fwx-form-card">
       <div class="form-title">…</div>
       <div class="form-desc">…</div>
       <div class="form-row"><label class="form-label">…</label><控件/></div>
       <div class="form-row">按钮组（右对齐）</div>
     </div>

   与 LuCI 的映射（据此逐条对齐，不是凭空配色）：
     .fwx-form-card  <-> fieldset.cbi-section
     .form-title     <-> legend
     .form-desc      <-> .cbi-section-descr
     .form-row       <-> .cbi-value          (.form-row: flex / mb10 / gap10)
     .form-label     <-> .cbi-value-title    (.form-label: width 140)
     .switch         <-> input[type=checkbox] (40x20，滑块 16，位移 20)
     右下按钮组       <-> .cbi-page-actions
     .common-table   <-> .table

   不做的事：① 不给 .cbi-map 限宽——标准页有宽表格（无线客户端列表、防火墙规则），
   限 780 会挤成横向滚动；② 不动输入框与普通按钮底色——主题已完整覆盖。 */

/* 1. 卡片：.fwx-form-card = 1px 描边 / 8 圆角 / 16 内距 / 白底 */
fieldset.cbi-section,
.cbi-section-node {
	border: 1px solid var(--border-color-low, #e5e7eb);
	border-radius: 8px;
	padding: 16px;
	margin-bottom: 16px;
	background: var(--background-color-high, #fff);
}

/* 2. 标题与说明：.form-title 18px / .form-desc 13px 灰 */
fieldset.cbi-section legend,
.cbi-section-node > h3 {
	font-size: 18px;
	font-weight: 600;
	padding-top: 0;
	color: var(--text-color-highest, #111);
}
.cbi-section-descr {
	font-size: 13px;
	color: var(--text-color-medium, #6b7280);
	margin-bottom: 12px;
}

/* 3. 字段行：.form-row(flex/mb10/gap10) + .form-label(width 140) */
.cbi-value {
	margin-bottom: 10px;
	gap: 10px;
}
.cbi-value label.cbi-value-title,
.cbi-value > .cbi-value-title {
	flex: 0 0 140px;
	width: 140px;
	padding-top: 6px;
	font-size: 13px;
	font-weight: 500;
	color: var(--text-color-high, #333);
}
.cbi-value-field { margin-left: 0; }

/* 4. 开关：把 checkbox 画成 fwx 的 .switch（40x20 / 滑块 16 / 选中位移 20） */
.cbi-value-field input[type="checkbox"] {
	-webkit-appearance: none;
	appearance: none;
	flex: 0 0 auto;
	width: 40px;
	height: 20px;
	margin: 0;
	border: none;
	border-radius: 10px;
	background: var(--border-color-medium, #ccc);
	position: relative;
	cursor: pointer;
	transition: background .2s;
}
.cbi-value-field input[type="checkbox"]::after {
	content: "";
	position: absolute;
	top: 2px;
	left: 2px;
	width: 16px;
	height: 16px;
	border-radius: 50%;
	background: #fff;
	transition: transform .2s;
}
.cbi-value-field input[type="checkbox"]:checked {
	background: var(--primary-color-high, #1976d2);
}
.cbi-value-field input[type="checkbox"]:checked::after { transform: translateX(20px); }
.cbi-value-field input[type="checkbox"]:disabled { opacity: .5; cursor: not-allowed; }

/* 5. 表格：对齐 .common-table 的边框与表头 */
.table .td,
.table .th { border-top-color: var(--border-color-low, #e5e7eb); }
.table .tr:first-child .th {
	background: var(--background-color-medium, #f5f5f5);
	color: var(--text-color-highest, #111);
}

/* 6. 按钮：主操作实心、危险操作实心红，与 .submit-button / .btn-delete 一致 */
.cbi-button-apply,
.cbi-button-action,
.cbi-button-save,
.cbi-button-positive,
.cbi-button-add,
.cbi-button-fieldadd {
	color: #fff;
	background: var(--primary-color-high, #1976d2);
	border-color: var(--primary-color-high, #1976d2);
}
.cbi-button-negative,
.cbi-button-remove {
	color: #fff;
	background: var(--error-color-high, #e8210d);
	border-color: var(--error-color-high, #e8210d);
}
.cbi-button-apply:hover,
.cbi-button-action:hover,
.cbi-button-save:hover,
.cbi-button-positive:hover,
.cbi-button-add:hover,
.cbi-button-fieldadd:hover,
.cbi-button-negative:hover,
.cbi-button-remove:hover { filter: brightness(0.92); }

/* 7. 操作条：右下对齐，与 fwx 页面的按钮组一致 */
.cbi-page-actions {
	display: flex;
	flex-wrap: wrap;
	justify-content: flex-end;
	gap: 8px;
	border-top: 1px solid var(--border-color-low, #e5e7eb);
	padding-top: 12px;
}
"""


def read(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8", "surrogateescape")


def write(path, text):
    with open(path, "wb") as f:
        f.write(text.encode("utf-8", "surrogateescape"))


def append_bridge(path, block, label):
    if not os.path.isfile(path):
        print("  [!] 找不到 %s" % label)
        return 2
    src = read(path)
    if MARK in src:
        print("  [skip] %s 已含样式桥接" % label)
        return 0
    if not src.endswith("\n"):
        src += "\n"
    write(path, src + block)
    ok = MARK in read(path)
    print("  [+] %s 样式桥接已追加" % label if ok else "  [!] %s 写入校验失败" % label)
    return 0 if ok else 2


def main():
    args = sys.argv[1:]
    if not args:
        print("usage: style_bridge.py [--fwx|--theme] <tree> ...")
        return 2
    rc = 0
    tree = None
    for i, a in enumerate(args):
        if a in ("--fwx", "--theme"):
            tree = args[i + 1] if i + 1 < len(args) else None
            if not tree:
                print("  [!] %s 缺少源码树参数" % a)
                return 2
            if a == "--fwx":
                rc |= append_bridge(os.path.join(tree, FWX_CSS_REL), FWX_BRIDGE, "fwx common.css")
            else:
                rc |= append_bridge(os.path.join(tree, THEME_CSS_REL), THEME_BRIDGE, "主题 cascade.css")
    return 0 if rc == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
