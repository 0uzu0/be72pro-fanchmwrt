#!/usr/bin/env bash
#
# 一键构建：锐捷 RG-BE72 Pro + FanchmWrt fwx（UI / DPI / 审计 / 终端管控）
#
# 环境变量：
#   WORKDIR      构建工作目录（默认 $PWD/work）
#   WITH_KMOD    1 = 编译 fwx.ko（默认），0 = 只出 UI+后端（保证能出固件）
#   JOBS         并行数（默认 nproc）
#
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

WORKDIR="${WORKDIR:-$PWD/work}"
WITH_KMOD="${WITH_KMOD:-1}"
JOBS="${JOBS:-$(nproc)}"

RJ_REPO="https://github.com/RuijieNetworksCommunity/MT798X-6.6-24.10.git"
RJ_REF="401e2ba1682314e48aabaa1af28d0e161aa58554"

FCM_REPO="https://github.com/fanchmwrt/fanchmwrt.git"
FCM_REF="11b65ca6a58dc7fad27eef196b391789195393b8"

PKG_REPO="https://github.com/fanchmwrt/fanchmwrt-packages.git"
PKG_REF="59c6b967e611a8c8562f3fd17eaa6aec9977f071"

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

# ---------------------------------------------------------------- 2. 搬包
log "2/8 搬 fwx 包进源码树"
APPS="luci-app-fwx-resources luci-app-fwx-dashboard luci-app-fwx-dashboard-setting \
luci-app-fwx-user luci-app-fwx-user-record luci-app-fwx-appfilter luci-app-fwx-record \
luci-app-fwx-record-whitelist luci-app-fwx-macfilter luci-app-fwx-mac-blacklist \
luci-app-fwx-session-stat luci-app-fwx-traffic-stat luci-app-fwx-feature"

mkdir -p "$RJ/package/fcm"
for m in libfwx_common fwxd fwx; do
	rm -rf "$RJ/package/fcm/$m"
	cp -r "$FCM/package/fcm/$m" "$RJ/package/fcm/$m"
done
for a in $APPS; do
	rm -rf "$RJ/package/$a"
	cp -r "$PKG/$a" "$RJ/package/$a"
done
echo "  已放入 $(echo $APPS | wc -w) 个界面 + 3 个后端包"

# ---------------------------------------------------------------- 3. 打补丁
log "3/8 应用补丁"
cd "$RJ"
[ -f ./scripts/feeds ] || { echo "!! $RJ 不是有效源码树（缺 scripts/feeds）"; exit 1; }

# 说明：0002 / 0003 不用 unified diff，改用锚点插入。
# 原因：这两处都是「在某条确定语句后插几行」，用 diff 时上游改一行就整体失效
# （实测出现过 git apply 能过、GNU patch 在 hunk 中间对不上的情况）。
# 脚本幂等：已插入会打印 [skip]，锚点找不到返回非 0 但不改文件。

# 3.1 fwx 侧：拉黑即清 PPE 表项 + init 守卫（与内核无关，总是尝试）
python3 "$ROOT/scripts/patch_kernel_extras.py" --fwx "$RJ" \
	|| warn "fwx 侧改动未全部插入 —— 只影响「拉黑后立即断开」与降级版的 init 行为"

if [ "$WITH_KMOD" = "1" ]; then
	# 3.2 hnat 侧：被 DPI 判死的流不进 PPE
	#     若失败只是失去该优化，不影响编译（hnat 保持原样）
	python3 "$ROOT/scripts/patch_kernel_extras.py" --hnat "$RJ" \
		|| warn "hnat 补丁未应用 —— 被拦连接可能仍会被硬件加速"

	# 3.3 内核：struct nf_conn 增加 fwx_data
	mkdir -p target/linux/generic/hack-6.6
	cp "$ROOT/patches/0001-net-netfilter-nf-conn-fwx-fields.patch" \
	   target/linux/generic/hack-6.6/950-fwx-ct-fields.patch
	echo "  [+] 0001 nf_conn 字段（进 hack-6.6）"
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
make defconfig >/dev/null

# 这些包缺任何一个都意味着「界面/后端缺一块」，属于静默功能缺失。
# 上一版只 warn 就继续，等于把缺功能的固件当成功产物发出去——改为直接失败。
require_cfg() {
	local missing=()
	local sym
	for sym in "$@"; do
		grep -q "^${sym}=y" .config || missing+=("$sym")
	done
	if [ ${#missing[@]} -gt 0 ]; then
		echo "!! 以下软件包未被 make defconfig 选中（依赖不满足，已被静默丢弃）："
		printf '     %s\n' "${missing[@]}"
		echo "   排查：看该包 Makefile 的 DEPENDS；在本树里缺少某个 kmod/库时就会这样。"
		exit 1
	fi
	echo "  ✔ 必需软件包全部选中（共 $# 项）"
}

REQUIRED_SYMS=(
	CONFIG_PACKAGE_luci-compat
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
)
if [ "$WITH_KMOD" = "1" ]; then
	REQUIRED_SYMS+=(CONFIG_PACKAGE_kmod-fwx)
fi
require_cfg "${REQUIRED_SYMS[@]}"

# ---------------------------------------------------------------- 6. 内核
log "6/8 内核准备（含 0001 应用校验）"

# 判定「内核补丁是否真的生效」：prepare 成功不等于补丁贴上，必须回查源码里有没有成员。
# 上一版就是「patch 报成功、成员没加上」，直到编译 HNAT 才炸，白跑一小时。
KLOG="$WORKDIR/kernel-prepare.log"

# 只负责把内核源码解压 + 打补丁到 build_dir（失败返回非 0）
kernel_prepare() {
	make target/linux/prepare V=s > "$KLOG" 2>&1
}

# 只负责判定 0001 是否生效（不碰 prepare）
kernel_has_fwx() {
	local kdir
	kdir=$(ls -d build_dir/target-*/linux-mediatek_filogic/linux-6.6* 2>/dev/null | head -1)
	[ -n "$kdir" ] || { echo "  找不到内核源码目录"; return 1; }
	grep -q "struct nf_fwx_data fwx_data;" "$kdir/include/net/netfilter/nf_conntrack.h" \
		|| { echo "  nf_conntrack.h 里没有 fwx_data 成员"; return 1; }
	grep -q "memset(&ct->fwx_data" "$kdir/net/netfilter/nf_conntrack_core.c" \
		|| { echo "  nf_conntrack_core.c 里没有清零语句"; return 1; }
	return 0
}

if [ "$WITH_KMOD" = "1" ]; then
	if kernel_prepare && kernel_has_fwx; then
		echo "  ✔ 内核已就绪：nf_conn.fwx_data 存在"
	else
		warn "内核补丁未生效 —— 降级为「无 DPI 内核模块」，继续出固件"
		tail -20 "$KLOG" 2>/dev/null || true
		rm -f target/linux/generic/hack-6.6/950-fwx-ct-fields.patch
		# 0002 依赖 0001 的字段，必须一起撤销
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
