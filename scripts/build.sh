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
	echo "  $(basename "$dir") @ $(git -C "$dir" rev-parse --short HEAD)"
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

# apply_patch <工作目录> <补丁> <描述>
#   统一用 --batch 避免 CI 里弹交互提示；失败只告警，不中断整次构建
apply_patch() {
	local dir="$1" pf="$2" desc="$3"
	if ( cd "$dir" && patch -p1 -N --batch --forward < "$pf" ) >/dev/null 2>&1; then
		echo "  [+] $desc"
		return 0
	fi
	warn "$desc 应用失败（跳过，功能降级）"
	return 1
}

# 3.1 HNAT 方案三：fwx 拉黑即清 PPE 表项（补丁路径相对 package/fcm/fwx）
if grep -q "ppe_del_entry_by_mac" package/fcm/fwx/src/fwx_mac_filter.c 2>/dev/null; then
	echo "  [skip] 0003 已应用"
else
	apply_patch "package/fcm/fwx" "$ROOT/patches/0003-fwx-flush-hnat-entries.patch" \
		"0003 拉黑即清 PPE 表项" || true
fi

if [ "$WITH_KMOD" = "1" ]; then
	# 3.2 HNAT 方案二：被判 drop 的流不进 PPE
	if grep -q "fwx_data" target/linux/mediatek/files-6.6/drivers/net/ethernet/mediatek/mtk_hnat/hnat_nf_hook.c; then
		echo "  [skip] 0002 已应用"
	else
		apply_patch "target/linux/mediatek/files-6.6" \
			"$ROOT/patches/0002-hnat-skip-fwx-dropped-flows.patch" \
			"0002 DPI 判死的流不进 PPE" || warn "  继续构建，但被拦连接可能仍被硬件加速"
	fi

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
[ -x ./scripts/feeds ] && { ./scripts/feeds update -a >/dev/null; ./scripts/feeds install -a >/dev/null; } \
	|| { echo "  !! 没有 scripts/feeds"; exit 1; }

# ---------------------------------------------------------------- 5. 配置
log "5/8 配置"
cp -f "$ROOT/defconfig/be72-fwx.config" .config
if [ "$WITH_KMOD" = "0" ]; then
	sed -i 's/^CONFIG_PACKAGE_kmod-fwx=y/# CONFIG_PACKAGE_kmod-fwx is not set/' .config
fi
make defconfig >/dev/null

check_cfg() {
	grep -q "^$1=y" .config && echo "  ✔ $1" || warn "缺少 $1（依赖未满足，构建可能缺功能）"
}
check_cfg CONFIG_PACKAGE_luci-compat
check_cfg CONFIG_PACKAGE_luci-app-fwx-appfilter
check_cfg CONFIG_PACKAGE_luci-app-fwx-user
check_cfg CONFIG_PACKAGE_fwxd
if [ "$WITH_KMOD" = "1" ]; then check_cfg CONFIG_PACKAGE_kmod-fwx; fi

# ---------------------------------------------------------------- 6. 内核
log "6/8 内核准备（含 0001 应用，失败自动降级）"
if ! make target/linux/prepare V=s > "$WORKDIR/kernel-prepare.log" 2>&1; then
	if [ "$WITH_KMOD" = "1" ]; then
		warn "内核补丁未能应用 —— 降级为「无 DPI 内核模块」，继续出固件"
		tail -30 "$WORKDIR/kernel-prepare.log" || true
		rm -f target/linux/generic/hack-6.6/950-fwx-ct-fields.patch
		# 0002 依赖 0001 的字段，必须一起撤销
		( cd target/linux/mediatek/files-6.6 && \
		  patch -p1 -R --forward < "$ROOT/patches/0002-hnat-skip-fwx-dropped-flows.patch" ) || true
		sed -i 's/+kmod-fwx //g' package/luci-app-fwx-*/Makefile || true
		sed -i 's/^CONFIG_PACKAGE_kmod-fwx=y/# CONFIG_PACKAGE_kmod-fwx is not set/' .config
		make target/linux/clean >/dev/null 2>&1 || true
		make defconfig >/dev/null
		make target/linux/prepare V=s
		WITH_KMOD=0
	else
		tail -50 "$WORKDIR/kernel-prepare.log"; exit 1
	fi
fi

# ---------------------------------------------------------------- 7. 编译
log "7/8 下载 + 编译（-j$JOBS，这一步最久）"
make -j"$JOBS" download >/dev/null 2>&1 \
	|| warn "download 阶段有失败项，继续编译（编译时会重试）"
make -j"$JOBS" V=s 2>&1 | tee "$WORKDIR/build.log"

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
