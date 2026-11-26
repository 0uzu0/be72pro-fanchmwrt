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

## 二之二、最容易漏的一步：三个交换机/PHY 驱动仓库

锐捷的 `target/linux/mediatek/image/filogic.mk` 给 BE72 的设备定义里写：

```make
DEVICE_PACKAGES += ... swconfig kmod-rtl837x_gsw          # -mod
DEVICE_PACKAGES += kmod-rtl8372n_dsa                       # -dsa-mod
```

但这两个包**不在源码树里**——它们在三个独立仓库，锐捷自己的 CI 会在编译前 clone：

```
RuijieNetworksCommunity/rtl837x-gsw-driver   -> PKG_NAME := rtl837x_gsw    (kmod-rtl837x_gsw)
RuijieNetworksCommunity/rtl837x-dsa-driver   -> PKG_NAME := rtl8372n_dsa   (kmod-rtl8372n_dsa)
```

第三个 `rtl8261-phy-driver` **不要克隆**：树内已有 `package/kernel/rtl8261d`
（`PKG_NAME=rtl8261d`，源码就是那个仓库，pin 在 `19fa6fa`），再克隆会撞同名包。

另外树内还有个 `package/kernel/rtl837x-gsw`，是同一驱动的旧打包
（`PKG_NAME=switch-rtl837x`，`FILES` 同样是 `rtl837x_gsw.ko`）。它和克隆版
名字不同但产出同一个 `.ko`，两者都装会文件冲突，所以脚本里把它删掉，只留克隆版。

**漏了会怎样：什么都不会发生。** 构建成功、镜像照发，`DEVICE_PACKAGES` 里不存在的包
被 OpenWrt **静默忽略**，结果是镜像里没有交换机驱动 → 5 个 2.5G 口（**含 WAN**）全不通，
PPPoE 自然拨不上。这一个问题就解释了「刷完拨号失败」的全部现象。

现在 `build.sh` 第 1b/8 步会 clone 并拷进 `$RJ/package/`，第 7b/8 步再回头校验
`build.log` 里有没有 `Installing kmod-rtl837x_gsw` / `Installing kmod-rtl8372n_dsa`，
**缺任何一个直接判构建失败**，不再把网络不通的固件当成功产物发出去。

顺带两个容易误判的点：

- `kmod-mt7992-firmware` / `kmod-mt7992-23-firmware` 同样"缺失"，但**无害**：
  本机用私有驱动 `mt_wifi7`，固件文件直接打在 `package/mt/drivers/mt_hwifi/files/` 里，
  不依赖这两个主线 mt76 的固件包。
- `kmod-swconfig` 不会出现在包清单里，因为 `kmod-rtl837x_gsw` 的 `DEPENDS` 会把它带进来，
  并连带把内核 `CONFIG_SWCONFIG` 从 `n` 变成 `m`。这一步会让内核重新配置一次，
  首次构建时间会变长，属正常。

## 三之三、UI 样式：按官网截图逐项校准

依据来自两处硬证据，不再凭印象配色：

1. **官网真实截图** `fanchmwrt.com/images/web/fcm/*.png`（9 张，含仪表板 / 应用过滤 /
   LAN 设置 / 终端列表 / 终端记录 / 特征库）。像素实测：卡片 `780x544`、输入框
   `357x30`、开关 `37x19`（含抗锯齿即 40x20）、保存键 `#10b981`、编辑键 `#007bff`、
   删除键 `#ff4444`、开关选中 `#2196F3`、描边 `#e5e7eb`、卡片与页面**同底色**。
2. **上游样式表** `luci-app-fwx-resources/.../common.css`：`.fwx-form-card`
   （`max-width 780 / padding 16 / 1px #e5e7eb / radius 8 / **transparent**`）、
   `.form-row`（`flex / mb10 / gap10`）、`.form-label`（140）、`.switch`（40x20，
   未选 `#ccc`、选中 `#2196F3`）、`.btn`（`#10b981`）、`.btn-edit`（`#007bff`）、
   `.btn-delete`（`#ff4444`）。

### 两个开关

| 开关 | 作用 | 默认 | 为什么 |
|---|---|---|---|
| `STYLE_FWX` | fwx 自有页面改用主题变量 | **0（关）** | 官网 UI 就是那套硬编码色值，换成主题变量反而**偏离 1:1**；且深色模式下官方卡片是 `transparent` + 浅描边，本身已协调 |
| `STYLE_CARDS` | 标准页对齐 fwx 规范 | **1（开）** | 锐捷保留的页面是纯 LuCI 表单，不吃这套规范就会和 fwx 页面"两个系统" |

### 标准页对齐了什么（`STYLE_CARDS=1`）

| 元素 | fwx 规范 | 主题原样 | 对齐后 |
|---|---|---|---|
| 区块 | `.fwx-form-card` | `fieldset` 只有 margin/padding | 1px `#e5e7eb` + 8 圆角 + 16 内距 + **transparent** |
| 标题 | `.form-title` 18px | `legend` 19.5px | 18px |
| 字段行 | `.form-row` `mb10/gap10` | `.cbi-value` `mb18` | `mb10 + gap10` |
| 标签宽 | `.form-label` 140px | `180px` | 140px |
| 开关 | `.switch` 40x20，选中 `#2196F3` | 原生 checkbox | CSS 画成滑块（滑块 16 / 位移 20） |
| 表格 | `.common-table` 表头**无底色** | 表头有底色 | 去掉底色，只留加粗 + `#e5e7eb` 描边 |
| 保存类 | `.btn` `#10b981` | 描边风 | 实心绿 + 白字 + radius 4 |
| 编辑/新增 | `.btn-edit` `#007bff` | 描边风 | 实心蓝 + 白字 |
| 删除类 | `.btn-delete` `#ff4444` | 描边风 | 实心红 + 白字 |
| 操作条 | 卡片内右下 | 底部通栏 | 右下对齐 + 8px 间距 |

**两处早期错误已改**：① 卡片曾写成白底（`var(--background-color-high,#fff)`），
深色模式下会出现白块，官方是 `transparent`；② 表头曾填
`var(--background-color-medium)`，官方其实是透明。这两处都是拿像素实测对出来的。

**刻意不改**：

1. **不给 `.cbi-map` 限宽 780**。fwx 的 780 是加在 `.fwx-form-card` 上的；LuCI 的
   `.cbi-map` 同时包住宽表格（无线客户端列表、防火墙规则），限 780 会挤成横向滚动。
2. **不动输入框、reset 按钮**。主题已完整覆盖（`fieldset` 562 行、`input/button`
   252 行、`.cbi-value` 588 行、`.cbi-button` 2598 行），再覆盖一遍就是回归。
3. **不重写锐捷的三个私有页面**。见下。

## 三之四、官网侧栏的 7 个一级菜单，分别由谁提供

官网截图的侧栏是 7 组（可展开）。全部对应到包，缺一个就少一组：

| 侧栏 | 子项 | 提供者 |
|---|---|---|
| 仪表板 | — | `luci-app-fwx-dashboard` |
| 系统监控 | 会话统计 / 终端记录 | `-session-stat` / `-user-record`（父菜单 `fwx_monitor` 由前者的 `firstchild()` 建立） |
| 终端列表 | — | `luci-app-fwx-user` |
| **网络设置** | 工作模式 / LAN / WAN / 防火墙 | **`-network`**（+ `-firewall`） |
| 行为管理 | 应用过滤 / MAC 过滤 | `-appfilter` / `-macfilter` |
| 上网审计 | — | `-record`（+ `-record-whitelist`） |
| 高级设置 | 特征库 / 仪表板配置 / 系统配置 | `-feature` / `-dashboard-setting` / `-system` |

早期漏装 `-network` 和 `-system`，侧栏就会缺「网络设置」整组、「高级设置 → 系统配置」。
现已补上（`16 个界面包`）。

**这两个包的实际风险（已读 `fwxd/src/fwx_network.c` 核实）**：

- `set_lan_info` 只写 `network.lan` 的 `proto / ipaddr / netmask / gateway / dns` 和
  `dhcp` 段，且**显式拒绝在 LAN 上设 PPPoE**；`network.lan` 不存在时才新建 `interface` 段。
- `set_wan_info` **不写死设备名**，只写 `network.wan` 的 `proto / username / password /
  ipaddr / dns`——`device` 绑定仍由 immortalwrt 的默认配置决定，所以不会把 `eth2.1`
  或 `wan` 弄错。这也是它反而能当 PPPoE 配置界面的原因。
- `set_work_mode` **只写 `fwx.network.work_mode`**（它自己的配置），不重写 `network`。

结论：不会自动破坏现有网络；只有你主动点「保存」才会改 LAN 的 IP/DHCP 参数。

### 一个踩过的坑：defconfig 里靠后的行会覆盖靠前的

`be72-fwx.config` 是「上游 defconfig + 叠加层」拼接而成。同一个符号出现两次时，
**后出现的那行生效**，所以叠加层里写 `CONFIG_PACKAGE_x=y`、而文件更靠下还留着
`# CONFIG_PACKAGE_x is not set`，结果就是**被静默关掉**——`make defconfig` 不报错，
只在收尾的 `require_cfg` 里表现为「这个符号不存在」。

补 `-network` / `-system` 时就是这么栽的：`=y` 加在第 1131 行，旧的
`# ... is not set` 在第 1147 行，后者赢。现在的处理是：真冲突的那几个
（`wireless` / `firewall` / `fastnat`）保留 `is not set`，`network` / `system`
一律不再写 `is not set`。改完做过一次全文件扫描，`=y` 与 `is not set` 的交集为 0。

## 三之五、接口自检：`scripts/check_api.py`

界面包（`luci-app-fwx-*`）与守护进程（`fwxd`）是两个仓库，页面通过
`ubus call fwx common '{"api":"xxx"}'` 对接。上游改接口名不会通知，
取错版本就是「页面能打开、数据是空的」。

### 口径改了三次，前两次都是错的

| 版本 | 口径 | 结果 |
|---|---|---|
| v1 | 控制器里出现过的 api 全算 | 报 13 个，其中 11 个假阳性 |
| v2 | 加上「视图里出现过」 | 反涨到 21 个——把 **LuCI 路由名**当成了 **fwxd api 名**（路由 `get_system_base_info` 内部调的其实是 `get_system_info`） |
| v3 | entry 注册 → 视图 → 视图引用的路由 → 控制器函数 → 函数里真正的 `.api="x"`；并排除**未注册的死视图** | 报 8 个，可逐条对到页面 |

v3 还处理了两件容易漏的事：
- 视图用 `<%+user/user_history%>` 引用的子视图要展开
- 未被任何 entry 注册、也没人引用的视图不算（例如 `macfilter/user.htm` 里确实调
  4 个 `*_user` 接口，但控制器只注册了 rules/whitelist/advance，那页进不去）

### 当前实测缺口（`fwxd` 24.10.4 + packages@3c6ae71）

| 页面 | 缺的接口 | 症状 |
|---|---|---|
| 系统监控 › 流量统计 | `get_history_traffic_stats` | 历史流量曲线空 |
| 上网审计 › 用户白名单 | `get/add/del_record_whitelist` | 该页不可用 |
| 终端列表 › 详情 | `get_user_daily_summary`、`get_user_parental_control_rules` | 每日汇总、家长控制规则两块空 |
| 仪表板 | `get_init_status`/`set_init_status` | 只是「模式切换引导」浮层不弹，无功能损失 |

**为什么换版本修不了**：这些名字在 `fwxd` 的 24.10.4 与 25.12 两版里都没有
（24.10.4 的 api 表里一个都没有；25.12 有其中 7 个）。所以不能靠改 `PKG_REF` 解决。

**如果要补齐**：25.12 的 `fwxd`（纯用户态）实现了其中 7 个，且它和 24.10.4 的
`fwx_netlink.c` 协议层完全一致（结构体/属性名逐项对比无差异），理论上可以只换
用户态、保留 24.10.4 的 `fwx.ko`。代价是多 4 个依赖（libsqlite3/libmosquitto/
libcurl/libopenssl）、镜像变大，且新版 `fwxd` 的返回结构可能与旧界面包有细微差异，
需要实机对比。目前**没有做**，默认仍是 24.10.4 那套。

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
| fanchmwrt-packages | `3c6ae7117843b46aa95b57d615874101d2db8700` | 16 个界面包，**与 fwxd 同期（2026-10-19）** |
| rtl837x-gsw-driver | `a1650c6ac6e658b7ee7594ec8283d94205a001fe` | RTL8372N swconfig 驱动（-mod 变体必需） |
| rtl837x-dsa-driver | `b14546dbeabf500c287a6ff7ce847919a02d4a26` | RTL8372N DSA 驱动（-dsa-mod 变体必需） |

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
