# BE72Pro + FanchmWrt fwx

给 **锐捷 RG-BE72 Pro（MT7988D）** 构建带 FanchmWrt 界面与 DPI 管控的固件。

底座用 `RuijieNetworksCommunity/MT798X-6.6-24.10`（保留 RTL8372N 双驱动、MT7992 私有 WiFi、HNAT），
**整套 fwx 技术栈取 `fanchmwrt-24.10.4`**（与底座同代，kernel 6.6 原生）：

| 能力 | 组成 |
|---|---|
| 界面 | `luci-theme-fanchmwrt`（ucode/JS 主题，接管侧边栏与登录页）+ 14 个 `luci-app-fwx-*` |
| 后端 | `fwxd`（ubus 方法 `fwx.common`）+ `rule_manager` |
| DPI | `fwx.ko`（netfilter hook @ conntrack+1）+ `feature.cfg` 特征库 |
| 审计/管控 | 应用过滤、上网记录、MAC 黑白名单、终端权限 |

> **为什么必须锁 24.10.4**：25.12.x 的 `struct nf_fwx_data` 里 `host` 是数组，24.10.4 的 `host` 是**指针**
> （见上游 `980-nf-contrack-support-fwx-data.patch`）。两版混用会让 `fwx.ko` 把内核指针当字符串解引用，
> 直接崩内核。换版本必须整包换，不能只换 UI。

---

## 一、一次性准备（约 5 分钟）

```bash
# 1. 在 GitHub 上新建空仓库：0uzu0/be72pro-fanchmwrt（不要勾 README）

# 2. 本地推上去
git init
git add .
git commit -m "init: BE72 Pro + FanchmWrt fwx"
git branch -M main
git remote add origin https://github.com/0uzu0/be72pro-fanchmwrt.git
git push -u origin main
```

推上去后 `push` 会自动触发一次构建；也可以到 **Actions → Build BE72Pro + FanchmWrt fwx → Run workflow** 手动触发。

> 需要 `Settings → Actions → General → Workflow permissions` 选 **Read and write permissions**，
> 否则最后一步「发 Release」会失败（固件仍然能在 Artifacts 里下载）。

---

## 二、构建产物

跑完后有两个地方能拿到：

1. **Actions → 本次 run → Artifacts**：`be72pro-fwx-<run号>.zip`
2. **Releases → build-<run号>**：直接单文件下载

产物：

```
immortalwrt-mediatek-filogic-ruijie_rg-be72-pro-mod-squashfs-sysupgrade.bin        ← 232.5M 布局（推荐）
immortalwrt-mediatek-filogic-ruijie_rg-be72-pro-dsa-mod-squashfs-sysupgrade.bin    ← DSA 交换机版
sha256sums
```

文件名里的 `-mod` 表示 **232.5 MiB 分区布局**，和社区刷机教程里「分区选 232.5」对应。

---

## 三、刷机

### 刷前必查

```bash
cat /proc/mtd | grep ubi
#   必须是 0e880000（232.5 MiB）；若是 07080000（112.5 MiB）先不要刷
cat /tmp/sysinfo/board_name
#   决定你该用 -mod 还是 -dsa-mod 那个文件
```

### 刷入（保留分区与 U-Boot，只换 ubi 内容）

```bash
scp immortalwrt-mediatek-filogic-ruijie_rg-be72-pro-mod-squashfs-sysupgrade.bin \
    root@192.168.1.1:/tmp/fw.bin

ssh root@192.168.1.1
sysupgrade -n -v /tmp/fw.bin      # -n 必须加：mtwifi/网络配置不兼容
```

### 刷坏了怎么回

- 能 SSH：`sysupgrade -n` 刷回原固件
- 不能 SSH：断电 → 按住 reset 上电 → 指示灯变红松手 → 网线接任意口 →
  `192.168.110.1` → 分区选 **232.5** → 上传原固件

---

## 三之二、刷完后先验 WAN / PPPoE

这台设备的 WAN 不是一张独立网卡，而是交换机里的一个 VLAN：

- `-mod`（swconfig）：WAN = `eth2.1`（mt7530 VLAN 1），LAN = `eth2.2`
- `-dsa-mod`（DSA）：WAN = `wan`

所以「拨不上号」多数不是 pppd 的问题，而是 WAN 设备本身没起来。按顺序查：

```sh
# 1) 刷的是哪个变体、WAN 设备叫什么
cat /etc/board.json | grep -A3 '"wan"'
ubus call network.interface.wan status | head -20

# 2) 设备在不在、有没有 carrier
ip -br link show eth2.1     # -mod
ip -br link show wan        # -dsa-mod

# 3) -mod 的 VLAN 靠 swconfig 下发，确认工具和 VLAN
swconfig dev switch0 show | head -30

# 4) pppd 侧
logread -e pppd | tail -30
ls /usr/lib/pppd/*/pppoe.so     # 缺失 = 没装 ppp-mod-pppoe
ps | grep -c pppd
```

| 现象 | 原因 |
|---|---|
| `status` 里 `up:false` 且没有 `device` | 设备名对不上：刷了另一个变体的镜像 |
| `swconfig: not found` | 用 `-dsa-mod` 的镜像跑了 `-mod` 的配置（或反之） |
| `plugin pppoe.so not found` | 缺 `ppp-mod-pppoe` |
| `Timeout waiting for PADO` | 线路/光猫桥接问题，不是固件 |
| 拨上就断 | MTU 用了 1500（PPPoE 需 1492），或 HNAT 与 PPPoE 打架，可试<br>`echo 0 > /sys/kernel/debug/hnat/hook_toggle` |

**可以先排除的一项**：`fwx.ko` 只在 IPv4/IPv6 的 `PRE_ROUTING`（`conntrack+1`）挂钩。PPPoE 发现帧是
EtherType `0x8863`、LCP/PAP 是 PPP 协议，都不经过 IP 钩子，所以 fwx 拦不掉拨号；默认动作也是放行
（`ct->fwx_data.action == 0`）。真要验证：`rmmod fwx && /etc/init.d/network restart`，如果还是拨不上，就与它无关。

---

## 三之三、UI 样式：全站统一

目标是把「fanchmwrt 自有页面」和「锐捷保留的页面」拉成同一套视觉。做法分三层：

| 层 | 做法 | 默认 |
|---|---|---|
| **A. fwx 页面跟随主题变量** | fwx 的 `common.css` 里是硬编码色值（`#2885e8` 蓝按钮、`green` 描边、`#ff4444` 删除键、白底卡片），不跟主题、深色模式下突兀。构建时追加 override 换成主题变量（`--primary-color-high` / `--error-color-high` / `--border-color-low` …，均带兜底），14 个页面立刻跟随换色与深浅色 | **开** |
| **B. 标准页按 fwx 骨架重写** | 卡片（1px/8px/16）、标题 18px、字段行 `mb10+gap10`、标签宽 140、checkbox 画成 40×20 滑块、表格边框与表头、主按钮实心、操作条右下对齐 | **开**（`STYLE_CARDS=0` 关） |
| **C. 重写锐捷页面** | 不需要 | — |

### B 层具体改了什么，以及刻意没改什么

改（都是为了让标准页和 fwx 页面一致）：

参照物是 fwx 的真实页面骨架（`luci-app-fwx-appfilter/.../advance.htm`）：

```
<div class="fwx-form-card">
  <div class="form-title">…</div>
  <div class="form-desc">…</div>
  <div class="form-row"><label class="form-label">…</label><控件/></div>
  <div class="form-row">按钮组（右对齐）</div>
</div>
```

映射到 LuCI 后逐项对齐：

| 元素 | fwx 骨架 | 主题原样 | 统一后 |
|---|---|---|---|
| 区块 | `.fwx-form-card` | `fieldset` 只有 margin/padding | 1px 描边 + 8 圆角 + 16 内距白卡 |
| 标题 | `.form-title` 18px | `legend` 19.5px | 18px |
| 字段行 | `.form-row` `mb10/gap10` | `.cbi-value` `mb18` | `mb10 + gap10` |
| 标签宽 | `.form-label` 140px | `.cbi-value-title` 180px | 140px |
| 开关 | `.switch` 40×20 滑块 | 原生 checkbox | **CSS 画成 40×20 滑块**（滑块 16，位移 20） |
| 表格 | `.common-table` | `border-top: --border-color-medium` | `--border-color-low` + 表头 `--background-color-medium` |
| 主按钮 | `.submit-button` 实心 | 描边风 | 实心填充 + 白字 |
| 危险按钮 | `.btn-delete` 实心红 | 描边风 | 实心红 |
| 操作条 | 卡片内右下 | 底部通栏 | 右下对齐 + 8px 间距 |

**刻意不改**：

1. **不给 `.cbi-map` 限宽**。fwx 页面全是窄表单，限 780 好看；但标准页有宽表格
   （无线客户端列表、防火墙规则），限宽会挤成横向滚动。
2. **不动输入框、普通按钮、reset 按钮的底色**。主题已完整覆盖（`fieldset` 562 行、
   `input/button` 252 行、`.cbi-value` 588 行、`.cbi-button` 2598 行），再覆盖一遍就是回归。
3. **不重写锐捷的三个私有页面**。见下。

### 为什么不重写锐捷的页面

三个页面经核实是**纯 LuCI 表单 API**：

| 页面 | 实现 | `E('div')` | `class=` | 内联 `style=` |
|---|---|---|---|---|
| `eqos.js` (3KB) | 全 `form.Map` / `TableSection` | 0 | 0 | 0 |
| `turboacc.js` (78KB) | form API + E() 拼装 | 95 | **0** | 0 |
| `wireless-mtk.js` (96KB) | form API + E() 拼装 | 13 | **2** | 0 |

外观 100% 由主题 `cascade.css` 决定，主题对标准控件已有完整样式。
手写重排这 174 KB JS 不会带来任何视觉收益，只会引入回归。

### 想回到 fanchmwrt 原样

触发构建时把 `style_cards` 选 `no`，或本地 `STYLE_CARDS=0`。改动只在一段 CSS，可随时切回。

## 四、构建失败时的降级开关

`fwx.ko` 要打一个改动 `struct nf_conn` 的内核补丁，6.6 上存在失败可能。仓库已做两层兜底：

1. **内核补丁打不上** → 自动移除补丁、撤销 HNAT 补丁、解除界面包对 `kmod-fwx` 的依赖，重新 prepare 内核后继续出固件
   （此时有完整界面 + 后端 + 审计记录，但没有 DPI 拦截）
2. **模块编译不过** → 手动触发时把 `with_kmod` 选 **no**，直接出「无内核模块」版

降级路径会额外做两件事：`fwx.init` 里加守卫，`fwx.ko` 没加载时不拉起 `rule_manager`（否则它每 10 秒轮询一次必然失败、刷满日志）；`.config` 里关掉 `CONFIG_PACKAGE_kmod-fwx`。

构建日志在失败的 run 里以 `build-log-<run号>` artifact 上传（取消的 run 也上传），`kernel-prepare.log` 会直接写明哪个 hunk 没打上；同时失败时会把 `error:` / `failed to build` 的关键行直接打到 Actions 控制台，不必先下 artifact。

**配置校验是硬失败**：19 个必须选中的符号（`luci-compat`、`luci-theme-fanchmwrt`、`zh_Hans` 语言包、`libfwx_common`、`fwxd`、14 个界面包，kmod 模式下再加 `kmod-fwx`）缺任何一个都会直接终止并列出缺的符号，不会静默产出缺功能的固件。

---

## 五、仓库结构

```
.
├── .github/workflows/build.yml     # Actions：拉源码 → 打补丁 → 编译 → 发 Release
├── scripts/build.sh                # 全部构建逻辑（本地也能跑）
├── defconfig/be72-fwx.config       # 上游 defconfig + fanchmwrt 24.10.4 叠加层
├── patches/                        # 上游 fanchmwrt-24.10.4 自带，原样使用
│   └── 980-nf-contrack-support-fwx-data.patch   # struct nf_fwx_data + nf_conn 成员
├── scripts/
│   ├── build.sh
│   └── patch_kernel_extras.py                   # 锚点式插入 hnat / fwx / fwx.init 改动
└── README.md
```

版本全部 pin 到 commit：

| 来源 | commit | 说明 |
|---|---|---|
| MT798X-6.6-24.10 | `401e2ba1682314e48aabaa1af28d0e161aa58554` | 底座（RTL8372N / mt_wifi7 / HNAT） |
| fanchmwrt | `9640f14b256065fd621de479b8d93762141504d3` | **24.10.4 分支**，fwx + fwxd + 主题 |
| fanchmwrt-packages | `3c6ae7117843b46aa95b57d615874101d2db8700` | 14 个界面包，**与 fwxd 同期（2026-10-19）** |

---

## 五之二、哪些用上游补丁、哪些用锚点脚本

**用上游补丁（不动上下文）**：`980`。这是 fanchmwrt 为 6.6 写的成品，编号刻意避开本树已有的 `930`。

上游同目录下还有 `981-add-fwx-user-ops.patch`，**本仓库不用**，因为它在这套组合里是死代码：
`fwx.ko` 全模块没有一处引用 `nf_fwx_user_*` / `user_ptr` / `session_node`，而 `fwxd` 走 netlink 上报、
不解析 `/proc/net/nf_conntrack`。多贴它只会多 4 个 hunk（含最易随内核小版本漂移的
`nf_conntrack_standalone.c`），一旦命中失败整条内核路径就降级，所以宁可不要。
将来若上游真用起了这套回调，把文件放回 `patches/` 并把 `build.sh` 第 3.3 步的 cp 改回循环即可。

**用锚点脚本**：hnat 判断、fwx 清表、fwx.init 守卫。这三处本质是「往既有函数里插几行」，
用 unified diff 时上游改一行就整体失效（实测过 `git apply` 能过、GNU patch 在 hunk 中间对不上）。

`scripts/patch_kernel_extras.py` 的落点：

- hnat：在 `skb_to_hnat_info(skb, out, entry, &hw_path);` 之前插入 `ct->fwx_data.action` 判断（该调用点全文件唯一）
- fwx：辅助函数插到最后一个 `#include` 之后；调用点插到 `fwx_add_mac_node(...)` + `mac_filter_write_unlock();` 之后；
  规则修改路径在 `fwx_api_mod_mac_filter_rule` 收尾处插入清表（UI 实际走这条路）
- fwx.init：在 `rule_manager` 的 `procd_open_instance` 之前插入 `fwx.ko` 加载守卫

脚本幂等（重复执行打印 `[skip]`），锚点命中次数不等于 1 时不动文件并返回 2。

### 界面包必须与 fwxd 同期

`fanchmwrt-packages` 只有 `main` 一条分支，但**不能直接取 HEAD**：界面包是通过 ubus
`{"api": "..."}` 与 `fwxd` 对接的，新版会把接口改名/替换。实测拿 HEAD 配 24.10.4 的 `fwxd`：

| 界面包 | 缺失接口 | 表现 |
|---|---|---|
| `luci-app-fwx-macfilter` | 9 个（`get_mac_filter_base/time/user`、`set_*`、`add/del_mac_filter_user`、`get_mf_status`） | 页面空白、保存无效 |
| `luci-app-fwx-dashboard` | 2 个（`get_init_status` / `set_init_status`） | 首页状态异常 |
| `luci-app-fwx-user` | 2 个（`get_parental_control_detail` / `get_system_base_info`） | 家长控制详情打不开 |

所以 `PKG_REF` 锁到 `3c6ae71178`（2026-10-19，与 24.10.4 分支同一天）。核对方式：
从各包 `luasrc/controller/*.lua` 提取 `.api = "xxx"`，与 `fwxd/src/fwx_ubus.c` 里出现的字符串取差集。
改版本后建议重跑一次这个比对。

## 六、本地构建（可选）

需要 Ubuntu 22.04 / WSL2，≥ 8 核 16G、60G 磁盘：

```bash
sudo apt-get install -y build-essential git python3 rsync unzip wget \
  libncurses5-dev zlib1g-dev gawk flex bison quilt libssl-dev xsltproc \
  device-tree-compiler libelf-dev bc rsync

WORKDIR=$HOME/fw-build WITH_KMOD=1 JOBS=$(nproc) bash scripts/build.sh
```

产物在 `$WORKDIR/MT798X-6.6-24.10/bin/targets/mediatek/filogic/`。

---

## 七、已知限制

| 项 | 说明 |
|---|---|
| 无线/网络设置页 | 未包含 `luci-app-fwx-wireless/-network/-system/-firewall`，它们按 fanchmwrt 的 schema 写配置，会与 DSA/mtwifi 冲突。这几项仍是 immortalwrt 原生页面，外观由 fanchmwrt 主题外壳统一（`cascade.css`），内容逻辑保持原样 |
| 侧边栏 | `menu-fanchmwrt.js` 接管整个菜单渲染，未在图标白名单里的条目回落到 `default` 图标；顺序按 LuCI 的 `menu.d` 排 |
| fastnat | 依赖 `kmod-shortcut-fe`，与 HNAT 互斥，未包含 |
| 特征库升级 | 需要 fanchmwrt 云端账号；离线用自带 `feature.cfg` |
| 识别粒度 | 域名/URL/端口/特征串，对 ECH、加密 SNI 退化 |
| 合规 | `fwx`/`fwxd`/`luci-app-fwx-*` 为 GPL-2.0（源自 destan19 的 OpenAppFilter 体系），二次分发请保留声明 |

---

## 免责

刷机有变砖风险，请先备份 mtd0~mtd6 并确认能进 U-Boot 恢复页。本仓库只做打包，不对硬件损坏负责。
