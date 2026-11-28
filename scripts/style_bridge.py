#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
样式桥接：把「锐捷保留的 LuCI 页面」对齐到 fanchmwrt 自己的视觉规范。

依据不是猜的，全部取自官方源码与官网截图实测：

  common.css（fwx 页面样式）
    .fwx-form-card  max-width 780 / padding 16 / 1px #e5e7eb / radius 8 / **transparent**
    .form-row       flex / margin-bottom 10 / gap 10
    .form-label     width 140
    .switch         40x20 / radius 20 / 滑块 16 / 未选 #ccc / 选中 #2196F3
    .btn            实心 #10b981 + 白字 + radius 4 + 600
    .btn-edit       #007bff      .btn-delete  #ff4444
  官网截图实测（lan.png，1804x893）
    卡片 780x544；输入框 357x30；开关 37x19（含抗锯齿即 40x20）；
    保存 #10b981；标题 18px；页面/卡片同底色、仅描边分界。

两个可选层（构建时用 STYLE_FWX / STYLE_CARDS 控制）：

  A. --fwx   fwx 自有页面（14+2 个 luci-app-fwx-*）
     它们的 common.css 是硬编码色值。默认**不启用**：官方 UI 就是这个色值，
     换成主题变量反而偏离 1:1，且深色模式下官方卡片是 transparent + 浅描边，
     本身已经协调。只有想让它们随主题换色时才开。

  B. --theme 锐捷保留的页面（无线 wireless-mtk.js / 流量整形 eqos.js /
     网络加速 turboacc.js）
     经核实这三页是纯 LuCI 表单 API + E('div')，零 class、零内联样式，
     外观 100% 由主题 cascade.css 决定。所以不重写 JS，只补 CSS，
     让 .cbi-* 结构落到 fwx 的卡片规范。默认启用。

用法：
    python3 style_bridge.py --fwx   <源码树>   # 追加到 fwx 的 common.css（A，可选）
    python3 style_bridge.py --theme <源码树>   # 追加到主题的 cascade.css（B，默认）
返回：0 = 已应用或已是目标状态；2 = 目标文件不存在
"""

import os
import sys

MARK = "/* === be72pro-fanchmwrt:style-bridge === */"

# 注意：界面包在构建时被拷到 $RJ/package/<包名>/ 下（不是 feeds 里），
# 所以路径要带 package/ 前缀。
FWX_CSS_REL = "package/luci-app-fwx-resources/htdocs/luci-static/resources/css/common.css"
THEME_CSS_REL = "package/fcm/luci-theme-fanchmwrt/htdocs/luci-static/fanchmwrt/cascade.css"

# 设计系统覆盖层（阶段一视觉统一）：独立文件，构建时追加到 cascade.css 末尾。
# 全部使用主题自身变量（--background-color-* / --primary-color-* 等），
# 深色模式（data-darkmode）自动跟随。
DESIGN_MARK = "be72pro-fanchmwrt:design-system"   # 判重子串（CSS 首行是块注释开头，不含 */）
DESIGN_CSS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                          "patches", "theme", "design-system.css")

# 模式 CSS 剥离标记与选择器黑名单（统一 UI：删除简单/高级双模式的样式残留）。
# vendor cascade.css 里这些规则配合 menu-fanchmwrt.js 的 getMenuCategory 使用；
# 仓库版菜单 JS 不再产出这些类，规则成了死代码 —— 应用设计层时顺手剥离。
STRIP_MARK = "be72pro-fanchmwrt:strip-mode-css"
STRIP_SELECTORS = (
    ".menu-item-basic",
    ".menu-item-advanced",
    ".mode-item",
    ".mode-check",
    ".mode-title",
    ".top-mode-",
    ".top-more-button",
    ".top-more-icon",
    "#modemenu",
)

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

/* 1. 卡片：.fwx-form-card = max-width 780 / 1px 描边 / 8 圆角 / 16 内距 / 透明底
   注意背景必须是 transparent，不是白底：官方 common.css 第 91 行就是
   `background: transparent`，深色模式下卡片与页面同色、仅靠描边分界。
   早期写成 var(--background-color-high,#fff) 会在深色下出现白块。 */
fieldset.cbi-section,
.cbi-section-node {
	border: 1px solid #e5e7eb;
	border-radius: 8px;
	padding: 16px;
	margin-bottom: 16px;
	background: transparent;
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

/* 4. 开关：把 checkbox 画成 fwx 的 .switch
   （原样照抄 common.css 第 40-80 行：40x20 / 圆角 20 / 滑块 16 / 未选 #ccc /
   选中 #2196F3 / 位移 20px。截图实测 37x19，含抗锯齿边界即 40x20。） */
.cbi-value-field input[type="checkbox"] {
	-webkit-appearance: none;
	appearance: none;
	flex: 0 0 auto;
	width: 40px;
	height: 20px;
	margin: 0;
	border: none;
	border-radius: 20px;
	background: #ccc;
	position: relative;
	cursor: pointer;
	transition: background .4s;
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
	transition: transform .4s;
}
.cbi-value-field input[type="checkbox"]:checked { background: #2196f3; }
.cbi-value-field input[type="checkbox"]:checked::after { transform: translateX(20px); }
.cbi-value-field input[type="checkbox"]:disabled { opacity: .5; cursor: not-allowed; }

/* 5. 表格：对齐 .common-table（第 139-156 行）
   官方表头**没有底色**，只有加粗 + 1px 描边；早期我给表头填了
   var(--background-color-medium)，深色下会多出一条灰带，与官方不符，已去掉。 */
.table .td,
.table .th { border-color: #e5e7eb; }
.table .tr:first-child .th {
	background: transparent;
	font-weight: bold;
	color: inherit;
}

/* 6. 按钮：照抄 common.css 的调色板（第 181-236 行）
     .btn        实心 #10b981 + 白字 + radius4 + 600  → 保存类（apply/save/positive）
     .btn-edit   实心 #007bff                          → 编辑类（action/add）
     .btn-delete 实心 #ff4444                          → 删除类（negative/remove）
     .btn-secondary 透明底 + #10b981 描边字             → reset 保持主题描边风，不改
   截图核对：LAN 页「保存」= #10b981、应用过滤「新增规则/编辑」= #007bff、
   「删除」= #ff4444，与本节一一对应。 */
.cbi-button-apply,
.cbi-button-save,
.cbi-button-positive {
	color: #fff;
	background: #10b981;
	border: 1px solid #10b981;
	border-radius: 4px;
	font-weight: 600;
}
.cbi-button-action,
.cbi-button-add,
.cbi-button-fieldadd {
	color: #fff;
	background: #007bff;
	border: 1px solid #007bff;
	border-radius: 4px;
	font-weight: 600;
}
.cbi-button-negative,
.cbi-button-remove {
	color: #fff;
	background: #ff4444;
	border: 1px solid #ff4444;
	border-radius: 4px;
}
.cbi-button-apply:hover,
.cbi-button-save:hover,
.cbi-button-positive:hover,
.cbi-button-action:hover,
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


def strip_mode_css(path):
    """从 cascade.css 里剥掉简单/高级模式相关规则（幂等）。

    解析方式：逐字符扫描，在每个规则（含 @media 等包裹块）的预lude 里找
    黑名单选择器；命中就整块丢弃。@media 块本身不命中，但其内部子规则会
    被同样检查，剩余子规则保留。只删不增，跑多少遍结果一致。
    返回 0 = 已剥离或本来就干净；2 = 文件不存在。
    """
    if not os.path.isfile(path):
        print("  [x] 找不到 %s" % path)
        return 2
    src = read(path)

    out = []
    i = 0
    n = len(src)
    dropped = 0
    while i < n:
        brace = src.find("{", i)
        if brace == -1:
            out.append(src[i:])
            break
        prelude = src[i:brace]
        # 找与本块匹配的右括号（允许嵌套，@media 内含完整规则）
        depth = 1
        j = brace + 1
        while j < n and depth > 0:
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
            j += 1
        body = src[brace + 1 : j - 1] if depth == 0 else src[brace + 1 : j]

        if "@" in prelude and prelude.lstrip().startswith("@"):
            # 包裹块（@media/@supports）：递归处理内部规则
            inner = _strip_rules(body)
            if inner.strip():
                out.append(prelude + "{" + inner + "}")
            dropped += 1 if inner != body else 0
        elif any(sel in prelude for sel in STRIP_SELECTORS):
            dropped += 1  # 整块丢弃
        else:
            out.append(prelude + "{" + body + "}")
        i = j if depth == 0 else n

    result = "".join(out)
    if STRIP_MARK not in result:
        result = ("/* === " + STRIP_MARK + "：简单/高级模式 CSS 已剥离 === */\n") + result.lstrip()
    if result != src:
        write(path, result)
        print("  [+] 模式 CSS 已剥离（移除 %d 条规则）" % dropped)
    else:
        print("  [skip] 模式 CSS 无残留")
    return 0


def _strip_rules(body):
    """对块内部再跑一遍同样的剥离逻辑（供 @media 递归），返回剩余内容。"""
    out = []
    i = 0
    n = len(body)
    while i < n:
        brace = body.find("{", i)
        if brace == -1:
            # 尾部没有左括号的多余片段（不应出现，保险保留）
            tail = body[i:]
            if tail.strip():
                out.append(tail)
            break
        prelude = body[i:brace]
        depth = 1
        j = brace + 1
        while j < n and depth > 0:
            if body[j] == "{":
                depth += 1
            elif body[j] == "}":
                depth -= 1
            j += 1
        inner = body[brace + 1 : j - 1] if depth == 0 else body[brace + 1 : j]
        if "@" in prelude and prelude.lstrip().startswith("@"):
            inner2 = _strip_rules(inner)
            if inner2.strip():
                out.append(prelude + "{" + inner2 + "}")
        elif any(sel in prelude for sel in STRIP_SELECTORS):
            pass  # 丢弃
        else:
            out.append(prelude + "{" + inner + "}")
        i = j if depth == 0 else n
    # 保留块尾空白（可能是最后一个规则的尾随换行），保证重复运行输出逐字节一致
    if i < n:
        out.append(body[i:])
    return "".join(out)


def append_design(path, label):
    """把 patches/theme/design-system.css 追加到目标 CSS（幂等，按 DESIGN_MARK 判重）。"""
    if not os.path.isfile(path):
        print("  [x] 找不到 %s" % path)
        return 2
    src = read(path)
    if DESIGN_MARK in src:
        print("  [skip] %s 已含设计系统层" % label)
        return 0
    if not os.path.isfile(DESIGN_CSS):
        print("  [x] 仓库里缺 patches/theme/design-system.css")
        return 2
    with open(DESIGN_CSS, "r", encoding="utf-8") as f:
        block = f.read()
    write(path, src.rstrip("\n") + "\n\n" + block.strip("\n") + "\n")
    ok = DESIGN_MARK in read(path)
    print("  [+] %s：设计系统层已应用" % label if ok else "  [!] %s 写入校验失败" % label)
    return 0 if ok else 2


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
        print("usage: style_bridge.py [--fwx|--theme|--design] <tree> ...")
        return 2
    rc = 0
    tree = None
    for i, a in enumerate(args):
        if a in ("--fwx", "--theme", "--design"):
            tree = args[i + 1] if i + 1 < len(args) else None
            if not tree:
                print("  [!] %s 缺少源码树参数" % a)
                return 2
            if a == "--fwx":
                rc |= append_bridge(os.path.join(tree, FWX_CSS_REL), FWX_BRIDGE, "fwx common.css")
            elif a == "--design":
                rc |= strip_mode_css(os.path.join(tree, THEME_CSS_REL))
                rc |= append_design(os.path.join(tree, THEME_CSS_REL), "主题 cascade.css")
            else:
                rc |= append_bridge(os.path.join(tree, THEME_CSS_REL), THEME_BRIDGE, "主题 cascade.css")
    return 0 if rc == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
