#!/usr/bin/env bash
#
# 一键构建：锐捷 RG-BE72 Pro + FanchmWrt fwx（UI / DPI / 审计 / 终端管控）
#
# 环境变量：
#   WORKDIR      构建工作目录（默认 $PWD/work）
#   WITH_KMOD    1 = 编译 fwx.ko（默认），0 = 只出 UI+后端（保证能出固件）
#   STYLE_CARDS  1 = 把标准页对齐 fwx 规范（默认 1）；0 = 保持主题原样
#   STYLE_FWX    1 = fwx 页面改用主题变量（默认 0，保持官方硬编码色 = 1:1）
#   JOBS         并行数（默认 nproc）
#
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

WORKDIR="${WORKDIR:-$PWD/work}"
WITH_KMOD="${WITH_KMOD:-1}"
STYLE_CARDS="${STYLE_CARDS:-1}"
STYLE_FWX="${STYLE_FWX:-1}"
JOBS="${JOBS:-$(nproc)}"

RJ_REPO="https://github.com/RuijieNetworksCommunity/MT798X-6.6-24.10.git"
RJ_REF="401e2ba1682314e48aabaa1af28d0e161aa58554"

FCM_REPO="https://github.com/fanchmwrt/fanchmwrt.git"
# 24.10.4：与底座同代（OpenWrt 24.10 / kernel 6.6），自带 980 这个 6.6 内核补丁。
# 不要换回 25.12.x —— 那一版是给 6.12 写的，struct nf_fwx_data 布局也不同（host 是数组而非指针）。
FCM_REF="9640f14b256065fd621de479b8d93762141504d3"

PKG_REPO="https://github.com/fanchmwrt/fanchmwrt-packages.git"
# 必须与 fwxd 同期：2026-10-19（与 24.10.4 分支同一天）。
# 用 main 的最新版会碰上版本错配 —— 实测 macfilter 缺 9 个 api、dashboard 缺 2 个、
# user 缺 2 个（新版把它们换成了别的接口），表现为页面空白/点了没反应。
PKG_REF="3c6ae7117843b46aa95b57d615874101d2db8700"

# 三个交换机/PHY 驱动仓库：锐捷自己的 CI 在编译前会 git clone 到 package/ 下。
# 少了它们，设备定义里的 DEVICE_PACKAGES（kmod-rtl837x_gsw / kmod-rtl8372n_dsa）
# 指向的包根本不存在，OpenWrt 只会静默忽略 —— 结果是镜像里没有交换机驱动，
# 5 个 2.5G 口（含 WAN）全部不通，而构建毫无报错。
# 包名对应关系：rtl837x_gsw -> kmod-rtl837x_gsw，rtl8372n_dsa -> kmod-rtl8372n_dsa
DRV_GSW_REPO="https://github.com/RuijieNetworksCommunity/rtl837x-gsw-driver.git"
DRV_GSW_REF="a1650c6ac6e658b7ee7594ec8283d94205a001fe"
DRV_DSA_REPO="https://github.com/RuijieNetworksCommunity/rtl837x-dsa-driver.git"
DRV_DSA_REF="b14546dbeabf500c287a6ff7ce847919a02d4a26"
# rtl8261d 不克隆：树内已自带 package/kernel/rtl8261d（PKG_NAME=rtl8261d，
# 源码即 rtl8261-phy-driver，pin 在 19fa6fa）。再克隆会撞同名包。

RJ="$WORKDIR/MT798X-6.6-24.10"
FCM="$WORKDIR/fanchmwrt"
PKG="$WORKDIR/fanchmwrt-packages"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\n\033[1;33m[WARN] %s\033[0m\n' "$*"; }

mkdir -p "$WORKDIR"

# ---------------------------------------------------------------- 1. 源码
log "1/8 取源码（全部 pin 到 commit）"
clone_at() {
	local dir="$1" url="$2" ref="$3"
	if [ -d "$dir/.git" ]; then
		echo "  已存在：$dir"
		return
	fi
	git init -q "$dir"
	git -C "$dir" remote add origin "$url"
	if ! git -C "$dir" fetch -q --depth 1 origin "$ref" 2>/dev/null; then
		warn "$(basename "$dir") 无法按 SHA 拉取，退回分支 HEAD（可能与 pin 不一致）"
		git -C "$dir" fetch -q --depth 1 origin
	fi
	git -C "$dir" checkout -q FETCH_HEAD
	# 明确核对是否落在 pin 上：补丁锚点是按固定 commit 验过的，
	# 静默漂到别的 commit 会让锚点失效（表现得像「补丁写错了」）。
	local got
	got=$(git -C "$dir" rev-parse HEAD)
	if [ ${#ref} -eq 40 ] && [ "$got" != "$ref" ]; then
		warn "$(basename "$dir") 未落在 pin 上：期望 ${ref:0:7}，实际 ${got:0:7}"
	fi
	echo "  $(basename "$dir") @ ${got:0:7}"
}
clone_at "$RJ"  "$RJ_REPO"  "$RJ_REF"
clone_at "$FCM" "$FCM_REPO" "$FCM_REF"
clone_at "$PKG" "$PKG_REPO" "$PKG_REF"

# 三个驱动：先落到 $WORKDIR/drivers/<name>，再整目录拷进 $RJ/package/
# （直接 clone 到 $RJ/package 里会因为 $RJ 自身是 git 仓库而产生嵌套仓库告警）
log "1b/8 取交换机/PHY 驱动（上游 CI 同款）"
clone_at "$WORKDIR/drivers/rtl837x-gsw-driver" "$DRV_GSW_REPO" "$DRV_GSW_REF"
clone_at "$WORKDIR/drivers/rtl837x-dsa-driver" "$DRV_DSA_REPO" "$DRV_DSA_REF"
for d in rtl837x-gsw-driver rtl837x-dsa-driver; do
	rm -rf "$RJ/package/$d"
	mkdir -p "$RJ/package/$d"
	cp -r "$WORKDIR/drivers/$d/." "$RJ/package/$d/"
	rm -rf "$RJ/package/$d/.git"
done

# 树内 package/kernel/rtl837x-gsw 是同一个驱动的旧打包（PKG_NAME=switch-rtl837x，
# FILES 同样是 rtl837x_gsw.ko）。留着它会和刚克隆的 rtl837x_gsw 产出同名 .ko，
# 一旦两者都被安装就是文件冲突；而且它多做一遍编译。删掉，只保留克隆版。
rm -rf "$RJ/package/kernel/rtl837x-gsw"
echo "  已放入 2 个驱动包（gsw / dsa），并移除树内重复的 rtl837x-gsw"

# ---------------------------------------------------------------- 2. 搬包
log "2/8 搬 fwx 包进源码树"
# 界面：17 个。
# 不装 wireless —— 按 fanchmwrt 自家无线 schema 写，本机是 mt_wifi7 私有驱动（mt7992），
# 配置项对不上，装了也只会写出一堆驱动不认的项。
# 装 network / system / firewall —— 已逐个核实它们只写标准 uci
# （network.lan|wan、system、firewall.@defaults[0]），不碰锐捷私有拓扑。
# firewall 的后端 api 由下面 2b 的 patch_fwxd_ext.py 补，否则页面打不开数据。
# 不装 app-center / luci-app-fwx-network —— 用户要求普通模式直接删掉
#   （网络设置页在网关模式下无用；fwxd 的 network API 保留，不影响其他页）。
# 不装 fastnat —— 依赖 SFE 快速转发，锐捷这棵树用的是 HNAT，没有 SFE。
APPS="luci-app-fwx-resources luci-app-fwx-dashboard luci-app-fwx-dashboard-setting \
luci-app-fwx-user luci-app-fwx-user-record luci-app-fwx-appfilter luci-app-fwx-record \
luci-app-fwx-record-whitelist luci-app-fwx-macfilter luci-app-fwx-mac-blacklist \
luci-app-fwx-session-stat luci-app-fwx-traffic-stat luci-app-fwx-feature \
luci-app-fwx-system luci-app-fwx-firewall"

mkdir -p "$RJ/package/fcm"

# 2a-3. 四类网络加速页（普通模式新增；仓库内维护，独立拷贝。
#       注意：accel 不在 fanchmwrt-packages 里，【绝不能】加进 APPS 变量
#       —— APPS 循环从 $PKG 逐个拷贝，会把 build-41/43/44 的 cp 失败重演）

for m in libfwx_common fwxd fwx luci-theme-fanchmwrt; do
	rm -rf "$RJ/package/fcm/$m"
	cp -r "$FCM/package/fcm/$m" "$RJ/package/fcm/$m"
done

for a in $APPS; do
	rm -rf "$RJ/package/$a"
	cp -r "$PKG/$a" "$RJ/package/$a"
done
echo "  已放入 $(echo $APPS | wc -w) 个界面 + 3 个后端包 + 1 个主题"

# 2a. FullCone 的 nft 内核模块
#     锐捷树自带 firewall4 / nftables / libnftnl 三处 fullcone 补丁，但**没有** nft_fullcone.ko，
#     只装了 kmod-ipt-fullconenat（xtables 版）—— fw4 是 nftables，根本不会加载它。
#     所以 firewall.@defaults[0].fullcone=1 在本树里一直是个死开关。
#     补齐方式：搬 fanchmwrt 25.12 用的 nft-fullcone 包（源码 2023-05，对 6.6 无需额外修改；
#     自带 010 patch 的条件是 6.6.113+ 或 6.12+，对 6.6.94 走 else 分支，等于空操作）。
# 目录名与 PKG_NAME（fullconenat-nft）保持一致，避免打包宏按目录推导时出偏差。
mkdir -p "$RJ/package/kernel/fullconenat-nft"
cp -r "$ROOT/patches/fullconenat-nft/." "$RJ/package/kernel/fullconenat-nft/"
echo "  已放入 nft-fullcone（fw4 的 FullCone 表达式内核模块）"

# 2a-3. 四类网络加速页（普通模式新增）
#   位置约束【血的教训，三连败】：必须在 APPS 循环之后执行——
#   accel 的文件并入 luci-app-fwx-system 包（system 由 APPS 循环拷入），
#   提前执行时目标目录还不存在（build-49）。
#   不做成独立包：build-45/46 实测独立包在 package metadata 里被静默跳过
#   （tmp/.config-package.in 无其符号段 -> Kconfig 关成 n）。
# 注意路径：APPS 循环把页面包拷到 $RJ/package/<name>（顶层），只有
# libfwx_common/fwxd/fwx/主题 在 $RJ/package/fcm/ 下 —— 之前写成 fcm/ 前缀，
# mkdir 出了一个没有 Makefile 的空目录，加速页永远进不了固件（build-55 实测）。
mkdir -p "$RJ/package/luci-app-fwx-system/luasrc/controller"
mkdir -p "$RJ/package/luci-app-fwx-system/luasrc/view/fwx_accel"
cp -f "$ROOT/patches/accel/fwx_accel.lua" "$RJ/package/luci-app-fwx-system/luasrc/controller/fwx_accel.lua"
cp -f "$ROOT/patches/accel/accel.htm" "$RJ/package/luci-app-fwx-system/luasrc/view/fwx_accel/accel.htm"
echo "  加速页已并入 luci-app-fwx-system（游戏/视频/下载/办公加速）"

# 2a-4. 加速页进普通模式菜单：主题 JS 的 basicMenus 按 9 个菜单名过滤，
#       把 fwx_accel 加进列表（否则普通模式侧边栏看不到加速入口）
grep -q "'fwx_accel'," "$RJ/package/fcm/luci-theme-fanchmwrt/htdocs/luci-static/resources/menu-fanchmwrt.js" || \
	sed -i "s/'fwx_user_record',/'fwx_user_record',\n\t\t\t'fwx_accel',/" \
	"$RJ/package/fcm/luci-theme-fanchmwrt/htdocs/luci-static/resources/menu-fanchmwrt.js"
echo "  加速页已加入普通模式菜单"

# 2a-5. 仪表盘 1:1（设计基准 docs/ui-redesign.html #v-dash）
#   数据全部真实：get_dashboard_common / get_daily_top_users / get_active_users
#   + network.device status（接口速率采样）；映射表 docs/dashboard-data-map.md
cp -f "$ROOT/patches/dashboard/dashboard.htm" \
	"$RJ/package/luci-app-fwx-dashboard/luasrc/view/fwx_dashboard/dashboard.htm"
echo "  仪表盘已替换为 1:1 版（真实数据源）"

# 2a-6. 上网审计页（设计稿 #v-records）+ 硬件加速联动状态（真实读 debugfs）
cp -f "$ROOT/patches/audit/base_setting.htm" \
	"$RJ/package/luci-app-fwx-record/luasrc/view/fwx_record/base_setting.htm"
echo "  审计页已替换（含审计↔HNAT 联动状态）"

# 2a-7. 终端列表 + 流量/会话统计（设计稿 #v-users / #v-traffic / #v-session）
cp -f "$ROOT/patches/users/user_status.htm" \
	"$RJ/package/luci-app-fwx-user/luasrc/view/user/user_status.htm"
cp -f "$ROOT/patches/traffic/traffic_stat.htm" \
	"$RJ/package/luci-app-fwx-traffic-stat/luasrc/view/fwx_traffic_stat/traffic_stat.htm"
echo "  终端列表 + 流量/会话统计已替换（真实数据源）"

# 2a-8. 特征库 + 记录白名单（设计稿 #v-feature / #v-rwhitelist）
cp -f "$ROOT/patches/feature/feature.htm" \
	"$RJ/package/luci-app-fwx-feature/luasrc/view/feature.htm"
cp -f "$ROOT/patches/whitelist/index.htm" \
	"$RJ/package/luci-app-fwx-record-whitelist/luasrc/view/index.htm"
echo "  特征库 + 记录白名单已替换（真实数据源）"


# 2a-1. kmod-oaf 必须排除 —— 它会和 fwx.ko 死磕
#   oaf.ko 由树里的 open-app-filter 包提供（build 目录 open-app-filter-*/oaf/src），
#   并且自带 /etc/modules.d/oaf，开机会自动加载。
#   它与 fwx.ko 共用 netlink id 29（fwx.h 里 FWX_NETLINK_ID 与 OAF_NETLINK_ID 都是 29，
#   fwx 本来就是它的替代实现），还抢同样的 /proc/net/af_conn 与 /proc/net/af_client。
#   两个同时在镜像里，先加载的占位、后加载的初始化失败。build-30 实测：
#       proc_dir_entry 'net/af_conn' already registered
#       netlink_fwx_init 1897 init fwx netlink failed, id=29
#       fwxd[5272]: Command failed: Not found
#   → fwxd 拿不到内核 netlink，DPI / 审计 / 黑名单 / 流量统计全部静默失效。
#   处理方式：defconfig 里显式置 not set（make defconfig 会保留显式关闭），
#   再在下面 make defconfig 之后做一次硬校验。不删 open-app-filter 源码 ——
#   它同时提供用户态包，删源码有牵动依赖的风险。

# 2a-2. mtkhnat 保留加载，钩子跟随「审计开关」联动（见 rule_manager.lua 补丁）
#   实测：offload 开着时，大流量下载的流被 PPE 硬件接管，后续包绕过 netfilter，
#   fwx 只能数到开头几百字节 → 流量统计/App 时长/审计"基本没变化"。
#   用户不想卸载 mtkhnat，所以不改 modules.d；改为运行时写 debugfs 的
#   /sys/kernel/debug/hnat/hook_toggle：
#       审计开（record_enable=1）→ 0（disabled）＝包进内核，统计准、过滤生效
#       审计关（record_enable=0）→ 1（enabled） ＝硬件加速，满速转发
#   模块始终加载，不卸载、不改文件，由 rule_manager 每个周期对齐。

# 2b. 给 fwxd 补 24.10.4 缺的两个接口（必须在下面的接口自检之前跑，
#     否则自检会把「补丁才提供的 api」误报成缺口）：
#       get/set_firewall      —— 防火墙页（FullCone + 基础策略）
#       get/set_init_status   —— 仪表板首次引导浮层
#     只往分派表插 4 行 + 拷 3 个新源文件，不碰任何既有函数体。
python3 "$ROOT/scripts/patch_fwxd_ext.py" "$RJ" \
	|| warn "fwxd 扩展接口未插入 —— 防火墙页会打开但保存无响应"

# 界面包与 fwxd 是两套仓库，靠 ubus {"api":"..."} 对接；取错版本会「页面能打开、
# 数据是空的、保存没反应」。这一步在编译前 2 分钟内把版本错配暴露出来。
python3 "$ROOT/scripts/check_api.py" "$RJ" \
	|| warn "界面包与 fwxd 接口对不上 —— 相关页面可能空白（见上表）"

# 2c. 扩展源码机械审查（本机无交叉编译器时的替代手段）
#     专查会被 -Werror 判死、但肉眼很难看出来的三类问题：
#       implicit-function-declaration（调用早于定义/声明）
#       宏/标识符未定义（按行号改写大文件时误删段落）
#       花括号不平衡（插入截断、段删除越界）
#     build-23、build-26 两次失败都是这类 —— 各白跑一趟 80 分钟。
#     这一步 1 秒出结果，宁在这里失败，不要拖到编译阶段。
python3 "$ROOT/scripts/check_c_sanity.py" \
	"$RJ/package/fcm/fwxd/src/fwx_ext_compat.c" \
	"$RJ/package/fcm/fwxd/src/fwx_firewall.c" \
	|| { printf '\n\033[1;31m[FATAL] 扩展源码机械审查未通过 —— 上面列出的问题会直接让编译失败\033[0m\n'; exit 1; }

# 样式桥接 A（默认关闭）：把 fwx 页面的硬编码色换成主题变量。
# 关的原因：官网 UI 就是这套硬编码色（#10b981 保存 / #007bff 编辑 / #ff4444 删除 /
# #2196F3 开关 / #e5e7eb 描边 / 卡片 transparent），换成主题变量反而偏离 1:1。
# 只有想让 fwx 页面随主题换色时才 STYLE_FWX=1。
if [ "$STYLE_FWX" = "1" ]; then
	python3 "$ROOT/scripts/style_bridge.py" --fwx "$RJ" \
		|| warn "fwx 样式桥接未应用"
else
	echo "  [skip] fwx 页面配色（STYLE_FWX=0，保持官方 1:1）"
fi

# 样式桥接 B（默认开启）：按 fwx 页面骨架重写标准页的表现层。
# 骨架取自 fwx 真实页面（.fwx-form-card / .form-title / .form-row / .switch），
# 逐条映射到 LuCI 的 .cbi-section / legend / .cbi-value / checkbox：
#   卡片(1px/8px/16)、标题 18px、字段行 mb10+gap10、标签宽 140、
#   checkbox 画成 40x20 滑块、表格边框与表头、主按钮实心、操作条右下对齐。
# 不做的事：① 不给 .cbi-map 限宽（标准页有宽表格，限 780 会挤成横向滚动）；
#           ② 不动输入框与普通按钮底色（主题已完整覆盖，改了是回归）。
# 锐捷保留的页面（wireless-mtk / turboacc / eqos）本就是纯 LuCI 表单 API +
# 零 class，靠这一层即可统一，无需重写 JS。
if [ "$STYLE_CARDS" = "1" ]; then
	python3 "$ROOT/scripts/style_bridge.py" --theme "$RJ" \
		|| warn "全站视觉统一未应用 —— 视觉仍为主题默认"
	python3 "$ROOT/scripts/style_bridge.py" --design "$RJ" \
		|| warn "设计系统层未应用 —— 视觉仍为旧样式（阶段一）"
else
	echo "  [skip] 全站视觉统一（STYLE_CARDS=0，保持 fanchmwrt 原样）"
fi

# ---------------------------------------------------------------- 3. 打补丁
log "3/8 应用补丁"
cd "$RJ"
[ -f ./scripts/feeds ] || { echo "!! $RJ 不是有效源码树（缺 scripts/feeds）"; exit 1; }

# 两类改动、两种打法：
#   内核侧 980 —— 上游写好的补丁，原样丢进 hack-6.6，绝不自己改上下文
#   hnat / fwx 侧 —— 「往既有函数里插几行」，用锚点脚本，不用 unified diff
#                    （diff 只要上游改一行就失效，实测过 git apply 能过、
#                      GNU patch 在 hunk 中间对不上）
# 锚点脚本幂等：已插入会打印 [skip]，锚点找不到返回非 0 但不改文件。

# 3.1 fwx 侧：拉黑即清 PPE 表项 + init 守卫（与内核无关，总是尝试）
python3 "$ROOT/scripts/patch_kernel_extras.py" --fwx "$RJ" \
	|| warn "fwx 侧改动未全部插入 —— 只影响「拉黑后立即断开」与降级版的 init 行为"

if [ "$WITH_KMOD" = "1" ]; then
	# 3.2 hnat 侧：被 DPI 判死的流不进 PPE
	#     若失败只是失去该优化，不影响编译（hnat 保持原样）
	python3 "$ROOT/scripts/patch_kernel_extras.py" --hnat "$RJ" \
		|| warn "hnat 补丁未应用 —— 被拦连接可能仍会被硬件加速"

	# 3.3 内核：只用上游 24.10.4 的 980
	#     980 = struct nf_fwx_data（host 是指针）+ nf_conn 成员 + 分配时逐字段置零
	#     上游还有 981（nf_fwx_user 回调 + procfs 输出），经核实是死代码：
	#       · 24.10.4 的 fwx.ko 全模块没有一处引用 nf_fwx_user_* / user_ptr / session_node
	#       · fwxd 走 netlink 上报，不解析 /proc/net/nf_conntrack
	#     多贴 981 只会多 4 个 hunk（含最易漂移的 nf_conntrack_standalone.c），
	#     命中失败就整体降级，所以不带。
	mkdir -p target/linux/generic/hack-6.6
	cp "$ROOT/patches/980-nf-contrack-support-fwx-data.patch" \
	   target/linux/generic/hack-6.6/980-nf-contrack-support-fwx-data.patch
	echo "  [+] 980 内核补丁（进 hack-6.6）"
else
	warn "WITH_KMOD=0：跳过内核补丁，并解除界面包对 kmod-fwx 的依赖"
	sed -i 's/+kmod-fwx //g' package/luci-app-fwx-*/Makefile || true
fi

# ---------------------------------------------------------------- 4. feeds
log "4/8 feeds"
./scripts/feeds update -a >/dev/null
./scripts/feeds install -a >/dev/null
echo "  feeds ok"

# ---------------------------------------------------------------- 5. 配置
log "5/8 配置"
cp -f "$ROOT/defconfig/be72-fwx.config" .config
if [ "$WITH_KMOD" = "0" ]; then
	sed -i 's/^CONFIG_PACKAGE_kmod-fwx=y/# CONFIG_PACKAGE_kmod-fwx is not set/' .config
fi
# ALL_KMODS=0：不再"编译树里所有内核模块"，只编被依赖选中的那些。
# ALL_KMODS=y（默认）时 OpenWrt 会把整棵树的 kmod 全编一遍，是 80 分钟里
# 仅次于工具链/内核的大头；关掉后构建明显变快、镜像更小。
# 代价：某些"没人显式依赖但你可能要用"的 kmod 不会出现在包里 —— 所以默认仍是 y，
# 需要提速时用 ALL_KMODS=0 显式开启，并对 manifests 做一次差异比对确认没少东西。
if [ "${ALL_KMODS:-1}" = "0" ]; then
	sed -i 's/^CONFIG_ALL_KMODS=y/# CONFIG_ALL_KMODS is not set/' .config
	echo "  ALL_KMODS=0：只编被选中的内核模块（构建更快，镜像更小）"
fi
make defconfig >/dev/null

# kmod-oaf 硬校验：万一被 ALL_KMODS 或依赖重新拉回来，这里拦住。
# 放行它会直接让 fwx.ko 初始化失败（见 2a-1 注释），属于"装了但功能全废"的静默故障。
if grep -q '^CONFIG_PACKAGE_kmod-oaf=y' .config; then
	sed -i 's/^CONFIG_PACKAGE_kmod-oaf=y/# CONFIG_PACKAGE_kmod-oaf is not set/' .config
	echo "  [+] 强制关闭 kmod-oaf（与 fwx.ko 争 netlink id 29）"
fi
if grep -q '^CONFIG_PACKAGE_kmod-oaf=y' .config; then
	echo "!! kmod-oaf 仍被选中：它会和 fwx.ko 抢 netlink id 29，导致 fwx 初始化失败"
	exit 1
fi

# 这些包缺任何一个都意味着「界面/后端缺一块」，属于静默功能缺失。
# 上一版只 warn 就继续，等于把缺功能的固件当成功产物发出去——改为直接失败。
# 注意：LuCI 的语言/翻译包是 tristate，一旦被选成 m 就等于「只编译不装」，
# 而主题的 +@LUCI_LANG_zh_Hans 会把主题也拖成 m —— 所以这里只认 y。
# 读一个符号的真实状态：y / m / n / MISSING（Kconfig 里根本没这个符号）
cfg_state() {
	if grep -qx "$1=y" .config; then echo y
	elif grep -qx "$1=m" .config; then echo m
	elif grep -qx "# $1 is not set" .config; then echo "n（被 Kconfig 关掉）"
	else echo "MISSING（Kconfig 里没有这个符号）"; fi
}

require_cfg() {
	local missing=()
	local sym val
	for sym in "$@"; do
		val=$(cfg_state "$sym")
		[ "$val" = "y" ] || missing+=("$sym  [$val]")
	done
	if [ ${#missing[@]} -gt 0 ]; then
		echo "!! 以下符号未被 make defconfig 选成 y："
		printf '     %s\n' "${missing[@]}"
		echo "   排查：看该包 Makefile 的 DEPENDS；=m 表示只编译不装进镜像，需要显式 =y。"
		exit 1
	fi
	echo "  ✔ 必需符号全部为 y（共 $# 项）"
}

REQUIRED_SYMS=(
	CONFIG_LUCI_LANG_zh_Hans
	CONFIG_PACKAGE_luci-i18n-base-zh-cn
	CONFIG_PACKAGE_luci-compat
	CONFIG_PACKAGE_luci-mod-admin-full
	CONFIG_PACKAGE_luci-app-firewall
	CONFIG_PACKAGE_luci-proto-ppp
	CONFIG_PACKAGE_uhttpd
	CONFIG_PACKAGE_ppp
	CONFIG_PACKAGE_ppp-mod-pppoe
	CONFIG_PACKAGE_luci-theme-fanchmwrt
	CONFIG_PACKAGE_libfwx_common
	CONFIG_PACKAGE_fwxd
	CONFIG_PACKAGE_luci-app-fwx-resources
	CONFIG_PACKAGE_luci-app-fwx-dashboard
	CONFIG_PACKAGE_luci-app-fwx-dashboard-setting
	CONFIG_PACKAGE_luci-app-fwx-user
	CONFIG_PACKAGE_luci-app-fwx-user-record
	CONFIG_PACKAGE_luci-app-fwx-appfilter
	CONFIG_PACKAGE_luci-app-fwx-record
	CONFIG_PACKAGE_luci-app-fwx-record-whitelist
	CONFIG_PACKAGE_luci-app-fwx-macfilter
	CONFIG_PACKAGE_luci-app-fwx-mac-blacklist
	CONFIG_PACKAGE_luci-app-fwx-session-stat
	CONFIG_PACKAGE_luci-app-fwx-traffic-stat
	CONFIG_PACKAGE_luci-app-fwx-feature
	CONFIG_PACKAGE_luci-app-fwx-system
	CONFIG_PACKAGE_luci-app-fwx-firewall
	CONFIG_PACKAGE_kmod-nft-fullcone
)
if [ "$WITH_KMOD" = "1" ]; then
	REQUIRED_SYMS+=(CONFIG_PACKAGE_kmod-fwx)
fi
# 诊断：把 fwx 相关符号的真实状态与 Kconfig 依赖段落写进日志。
# 这一步是为了「符号被静默丢弃」时能直接看到原因，不必再猜。
log "5b/8 配置诊断"
{
	echo "--- .config 中 luci-app-fwx / 依赖 的符号状态 ---"
	grep -E "^(# )?CONFIG_(PACKAGE_luci-app-fwx|PACKAGE_luci-lib-jsonc|PACKAGE_luci-compat|LUCI_LANG_zh_Hans)" .config \
		| sed 's/^/    /' || true
	echo "--- tmp/.config-package.in 里 luci-app-fwx-network 的依赖 ---"
	awk '/^config PACKAGE_luci-app-fwx-network$/{f=1} f&&/^(config|menu)/&&!/luci-app-fwx-network/{f=0} f' tmp/.config-package.in 2>/dev/null | head -14 | sed 's/^/    /' || true
	echo "--- tmp/.config-package.in 里 luci-lib-jsonc 是否存在 ---"
	grep -c "^config PACKAGE_luci-lib-jsonc$" tmp/.config-package.in 2>/dev/null | sed 's/^/    count=/' || true
	echo "--- tmp/.config-package.in 里 luci-app-fwx-accel 的依赖段 ---"
	awk '/^config PACKAGE_luci-app-fwx-accel$/{f=1} f&&/^(config|menu)/&&!/luci-app-fwx-accel/{f=0} f' tmp/.config-package.in 2>/dev/null | head -20 | sed 's/^/    /' || true
} > "$WORKDIR/config-diag.log" 2>&1
tail -30 "$WORKDIR/config-diag.log" | sed 's/^/  /'

require_cfg "${REQUIRED_SYMS[@]}"

# ---------------------------------------------------------------- 6. 内核
log "6/8 内核准备（含 980 应用校验）"

# 判定「内核补丁是否真的生效」：prepare 成功不等于补丁贴上，必须回查源码里有没有成员。
# 上一版就是「patch 报成功、成员没加上」，直到编译 HNAT 才炸，白跑一小时。
KLOG="$WORKDIR/kernel-prepare.log"

# 只负责把内核源码解压 + 打补丁到 build_dir（失败返回非 0）
kernel_prepare() {
	make target/linux/prepare V=s > "$KLOG" 2>&1
}

# 只负责判定 980 是否真的应用（不碰 prepare）
kernel_has_fwx() {
	local kdir
	kdir=$(ls -d build_dir/target-*/linux-mediatek_filogic/linux-6.6* 2>/dev/null | head -1)
	[ -n "$kdir" ] || { echo "  找不到内核源码目录"; return 1; }
	# 980 的两个 hunk 都要命中：结构体成员 + 分配处逐字段置零
	grep -q "struct nf_fwx_data fwx_data;" "$kdir/include/net/netfilter/nf_conntrack.h" \
		|| { echo "  980 未生效：nf_conntrack.h 里没有 fwx_data 成员"; return 1; }
	grep -q "ct->fwx_data.host = NULL;" "$kdir/net/netfilter/nf_conntrack_core.c" \
		|| { echo "  980 未生效：core.c 里没有 fwx_data 初始化"; return 1; }
	return 0
}

if [ "$WITH_KMOD" = "1" ]; then
	if kernel_prepare && kernel_has_fwx; then
		echo "  ✔ 内核已就绪：nf_conn.fwx_data 存在"
	else
		warn "内核补丁未生效 —— 降级为「无 DPI 内核模块」，继续出固件"
		tail -20 "$KLOG" 2>/dev/null || true
		rm -f target/linux/generic/hack-6.6/980-nf-contrack-support-fwx-data.patch
		# hnat 判断依赖 980 的字段，补丁撤了它也必须还原
		git checkout -- \
			target/linux/mediatek/files-6.6/drivers/net/ethernet/mediatek/mtk_hnat/hnat_nf_hook.c \
			|| warn "hnat 还原失败，需手工检查"
		sed -i 's/+kmod-fwx //g' package/luci-app-fwx-*/Makefile || true
		sed -i 's/^CONFIG_PACKAGE_kmod-fwx=y/# CONFIG_PACKAGE_kmod-fwx is not set/' .config
		make target/linux/clean >/dev/null 2>&1 || true
		make defconfig >/dev/null
		# 降级后只能要求「内核还能正常准备」，绝不能再跑 kernel_has_fwx——
		# 补丁已删，它必然为假，会把降级路径自己否掉（上一版的 bug）。
		if ! kernel_prepare; then
			echo "!! 去掉 fwx 补丁后内核仍无法准备，终止"
			tail -40 "$KLOG"
			exit 1
		fi
		echo "  ✔ 已回退内核补丁，内核可正常准备"
		WITH_KMOD=0
	fi
else
	echo "  WITH_KMOD=0，只做内核准备"
	if ! kernel_prepare; then
		echo "!! 内核准备失败，终止"
		tail -40 "$KLOG"
		exit 1
	fi
fi

# ---------------------------------------------------------------- 7. 编译
log "7/8 下载 + 编译（-j$JOBS，这一步最久）"
make -j"$JOBS" download >/dev/null 2>&1 \
	|| warn "download 阶段有失败项，继续编译（编译时会重试）"

# 失败时把日志里的关键错误直接打到 CI 控制台，省得每次都要下载 artifact 才能定位。
set +e
make -j"$JOBS" V=s 2>&1 | tee "$WORKDIR/build.log"
BUILD_RC=${PIPESTATUS[0]}
set -e
if [ "$BUILD_RC" -ne 0 ]; then
	echo
	warn "编译失败（make 退出码 $BUILD_RC），日志关键行："
	PAT='error:|Error [0-9]+|failed to build|\*\*\* |No rule to make'
	if grep -qE "$PAT" "$WORKDIR/build.log"; then
		grep -nE "$PAT" "$WORKDIR/build.log" | tail -30
	else
		tail -40 "$WORKDIR/build.log"
	fi
	exit "$BUILD_RC"
fi

# ---------------------------------------------------------------- 7b. 关键模块校验
# 设备定义里的 DEVICE_PACKAGES 如果指向不存在的包，OpenWrt 只是静默忽略，
# 构建照样成功、镜像照样产出 —— 但 5 个 2.5G 口（含 WAN）全不通，且没有任何报错。
# 2026-11-26 那次出包就是这样：kmod-rtl837x_gsw / kmod-rtl8372n_dsa 都没进镜像。
# 所以这里直接查「安装进 rootfs」的日志行，缺了就判构建失败，不许发布坏固件。
log "7b/8 关键模块校验（交换机驱动 / DPI）"
VERIFY_MISSING=()
need_installed() {
	local pkg="$1" desc="$2"
	if grep -qE "^(.*Installing|Installing) ${pkg} \(" "$WORKDIR/build.log"; then
		echo "  [v] ${pkg} 已装入镜像（${desc}）"
	else
		echo "  [x] ${pkg} 未装入镜像（${desc}）"
		VERIFY_MISSING+=("${pkg}")
	fi
}
need_installed kmod-rtl837x_gsw "RTL8372N swconfig 驱动，-mod 变体的 5 个 2.5G 口"
need_installed kmod-rtl8372n_dsa "RTL8372N DSA 驱动，-dsa-mod 变体"
need_installed kmod-nft-fullcone "fw4 的 FullCone 表达式（防火墙页开关真正生效的前提）"
if [ "$WITH_KMOD" = "1" ]; then
	need_installed kmod-fwx "DPI 内核模块"
	# kmod-oaf 绝不能和 kmod-fwx 同框：两者抢 netlink id 29 与 /proc/net/af_* 节点，
# 先加载的占位、后加载的 fwx 初始化失败 → fwxd 所有功能静默失效（见 2a-1 注释）。
	if grep -qE "^Installing kmod-oaf \(" "$WORKDIR/build.log"; then
		echo "  [x] kmod-oaf 被装进了镜像 —— 会和 fwx.ko 冲突，fwx 会初始化失败"
		VERIFY_MISSING+=("kmod-oaf-冲突")
	fi
fi

if [ ${#VERIFY_MISSING[@]} -gt 0 ]; then
	echo
	warn "以下模块未进入镜像：${VERIFY_MISSING[*]}"
	echo "   最常见原因：DEVICE_PACKAGES 里的包名与实际 PKG_NAME 不一致（会被静默忽略），"
	echo "   或驱动仓库没被拷进 package/。检查第 1b/8 步与 package/*drv* 目录。"
	echo "   已中止，避免发布「网络不通」的固件。"
	exit 1
fi

# ---------------------------------------------------------------- 8. 产物
log "8/8 产物"
BIN="bin/targets/mediatek/filogic"
ls -lh "$BIN"/*be72* 2>/dev/null || { warn "没有找到 be72 镜像"; exit 1; }

echo
for f in "$BIN"/*be72*sysupgrade.bin; do
	[ -f "$f" ] || continue
	echo "---- $(basename "$f")"
	tar -xOf "$f" CONTROL 2>/dev/null | grep -E "supported_devices|version" || true
	sha256sum "$f"
done

echo
echo "WITH_KMOD=$WITH_KMOD  (0 表示本次固件不含 DPI 内核模块)"
