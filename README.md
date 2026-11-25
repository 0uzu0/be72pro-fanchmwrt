# BE72Pro + FanchmWrt fwx

给 **锐捷 RG-BE72 Pro（MT7988D）** 构建带 FanchmWrt 界面与 DPI 管控的固件。

底座用 `RuijieNetworksCommunity/MT798X-6.6-24.10`（保留 RTL8372N 双驱动、MT7992 私有 WiFi、HNAT），
只叠加 fanchmwrt 的 `fwx` 技术栈：

| 能力 | 组成 |
|---|---|
| 界面 | `luci-theme-fanchmwrt` 风格页面（本仓库取 13 个 `luci-app-fwx-*`） |
| 后端 | `fwxd`（ubus 方法 `fwx.common`）+ `rule_manager.lua` |
| DPI | `fwx.ko`（netfilter hook @ conntrack+1）+ `feature.cfg` 特征库 |
| 审计/管控 | 应用过滤、上网记录、MAC 黑白名单、终端权限 |

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

## 四、构建失败时的降级开关

`fwx.ko` 要打一个改动 `struct nf_conn` 的内核补丁，6.6 上存在失败可能。仓库已做两层兜底：

1. **内核补丁打不上** → 自动移除补丁、撤销 HNAT 补丁、解除界面包对 `kmod-fwx` 的依赖，重新 prepare 内核后继续出固件
   （此时有完整界面 + 后端 + 审计记录，但没有 DPI 拦截）
2. **模块编译不过** → 手动触发时把 `with_kmod` 选 **no**，直接出「无内核模块」版

降级路径会额外做两件事：`fwx.init` 里加守卫，`fwx.ko` 没加载时不拉起 `rule_manager`（否则它每 10 秒轮询一次必然失败、刷满日志）；`.config` 里关掉 `CONFIG_PACKAGE_kmod-fwx`。

构建日志在失败的 run 里以 `build-log-<run号>` artifact 上传（取消的 run 也上传），`kernel-prepare.log` 会直接写明哪个 hunk 没打上；同时失败时会把 `error:` / `failed to build` 的关键行直接打到 Actions 控制台，不必先下 artifact。

**配置校验是硬失败**：16 个必需包（`luci-compat`、`libfwx_common`、`fwxd`、13 个界面包，加 `kmod-fwx`）缺任何一个都会直接终止并列出缺的符号，不会静默产出缺功能的固件。

---

## 五、仓库结构

```
.
├── .github/workflows/build.yml     # Actions：拉源码 → 打补丁 → 编译 → 发 Release
├── scripts/build.sh                # 全部构建逻辑（本地也能跑）
├── defconfig/be72-fwx.config       # 上游 defconfig + fwx 叠加层
├── patches/
│   └── 0001-net-netfilter-nf-conn-fwx-fields.patch   # struct nf_conn 加 fwx_data
├── scripts/
│   ├── build.sh
│   └── patch_kernel_extras.py                        # 锚点式插入 hnat / fwx / fwx.init 改动
└── README.md
```

版本全部 pin 到 commit：

| 来源 | commit |
|---|---|
| MT798X-6.6-24.10 | `401e2ba1682314e48aabaa1af28d0e161aa58554` |
| fanchmwrt | `11b65ca6a58dc7fad27eef196b391789195393b8` |
| fanchmwrt-packages | `59c6b967e611a8c8562f3fd17eaa6aec9977f071` |

---

## 五之二、为什么 0002/0003 不用 patch 文件

这两处改动本质是「往既有函数里插几行」。用 unified diff 时，上游把函数体改一行（加日志、调锁、改缩进）就会 hunk 失败；实测还出现过 `git apply` 能过、GNU patch 在 hunk 中间对不上的情况。

所以改成 `scripts/patch_kernel_extras.py` 做锚点插入：

- hnat：在 `skb_to_hnat_info(skb, out, entry, &hw_path);` 之前插入 `ct->fwx_data.action` 判断（该调用点全文件唯一）
- fwx：辅助函数插到最后一个 `#include` 之后；调用点插到 `fwx_add_mac_node(...)` + `mac_filter_write_unlock();` 之后；
  规则修改路径按 `fwx_api_mod_mac_filter_rule` 收尾处插入清表（UI 实际走这条路，`fwx_add_mac_to_rule` 全模块无人调用）
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
| 无线/网络设置页 | 未包含 `luci-app-fwx-wireless/-network/-system/-firewall`，它们按 fanchmwrt 的 schema 写配置，会与 DSA/mtwifi 冲突 |
| fastnat | 依赖 `kmod-shortcut-fe`，与 HNAT 互斥，未包含 |
| 特征库升级 | 需要 fanchmwrt 云端账号；离线用自带 `feature.cfg` |
| 识别粒度 | 域名/URL/端口/特征串，对 ECH、加密 SNI 退化 |
| 合规 | `fwx`/`fwxd`/`luci-app-fwx-*` 为 GPL-2.0（源自 destan19 的 OpenAppFilter 体系），二次分发请保留声明 |

---

## 免责

刷机有变砖风险，请先备份 mtd0~mtd6 并确认能进 U-Boot 恢复页。本仓库只做打包，不对硬件损坏负责。
