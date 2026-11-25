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
│   ├── 980-nf-contrack-support-fwx-data.patch   # struct nf_fwx_data + nf_conn 成员
│   └── 981-add-fwx-user-ops.patch               # nf_fwx_user 回调 + procfs 输出
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
| fanchmwrt-packages | `59c6b967e611a8c8562f3fd17eaa6aec9977f071` | 14 个界面包（该仓库只有 main） |

---

## 五之二、哪些用上游补丁、哪些用锚点脚本

**用上游补丁（不动上下文）**：`980` / `981`。这两个是 fanchmwrt 为 6.6 写的成品，编号刻意避开
本树已有的 `930`，按文件名排序天然保证 980 先于 981（981 往 980 建的结构体里再加两个成员）。

**用锚点脚本**：hnat 判断、fwx 清表、fwx.init 守卫。这三处本质是「往既有函数里插几行」，
用 unified diff 时上游改一行就整体失效（实测过 `git apply` 能过、GNU patch 在 hunk 中间对不上）。

`scripts/patch_kernel_extras.py` 的落点：

- hnat：在 `skb_to_hnat_info(skb, out, entry, &hw_path);` 之前插入 `ct->fwx_data.action` 判断（该调用点全文件唯一）
- fwx：辅助函数插到最后一个 `#include` 之后；调用点插到 `fwx_add_mac_node(...)` + `mac_filter_write_unlock();` 之后；
  规则修改路径在 `fwx_api_mod_mac_filter_rule` 收尾处插入清表（UI 实际走这条路）
- fwx.init：在 `rule_manager` 的 `procd_open_instance` 之前插入 `fwx.ko` 加载守卫

脚本幂等（重复执行打印 `[skip]`），锚点命中次数不等于 1 时不动文件并返回 2。

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
