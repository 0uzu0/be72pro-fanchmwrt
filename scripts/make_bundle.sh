#!/bin/bash
# 把一个构建产出的 ipk 目录打成【整合包】——路由器上一条命令就能装
#
# 用法：make_bundle.sh <ipk目录> <输出目录> [构建号] [源码树路径]
#   make_bundle.sh out/ipk out 31 /mnt/build/work/MT798X-6.6-24.10
#
# 产物：<输出目录>/be72pro-fwx-pkg-<构建号>.tar.gz
# 包内结构：
#   be72pro-fwx-pkg-31/
#     install.sh        ← scripts/router-upgrade.sh（带内核/完整性校验）
#     manifest          ← 构建号、commit、目标内核版本
#     sha256sums        ← 每个 ipk 的校验和
#     ipk/*.ipk
set -euo pipefail

IPKDIR=${1:?用法: make_bundle.sh <ipk目录> <输出目录> [构建号] [源码树]}
OUTDIR=${2:?缺少输出目录}
BUILD=${3:-0}
RJ=${4:-}

mkdir -p "$OUTDIR"

shopt -s nullglob
IPKS=("$IPKDIR"/*.ipk)
shopt -u nullglob
[ ${#IPKS[@]} -gt 0 ] || { echo "[bundle][x] $IPKDIR 里没有 .ipk"; exit 1; }

# ---------- 目标内核版本（决定这份包能装到哪个固件上） ----------
# 优先读构建树里内核的 release 字符串；读不到就退回 kmod ipk 的 Depends 声明。
KREL=""
if [ -n "$RJ" ]; then
	KREL=$(cat "$RJ"/build_dir/target-*/linux-mediatek_filogic/linux-*/include/config/kernel.release 2>/dev/null | head -1 || true)
fi
if [ -z "$KREL" ]; then
	for p in "${IPKS[@]}"; do
		case "$(basename "$p")" in
			kmod-fwx*)
				# control 里的 Depends: kernel (=6.6.94~xxxx)
				KREL=$(tar -xOf "$p" ./CONTROL/control 2>/dev/null \
					| sed -n 's/^Depends:.*kernel (=\([^~)]*\).*/\1/p' | head -1 || true)
				;;
		esac
		[ -n "$KREL" ] && break
	done
fi
if [ -z "$KREL" ]; then
	KREL="unknown"
	echo "[bundle][!] 读不到目标内核版本（内核 release 文件缺失、kmod ipk 也没有）——"
	echo "[bundle][!] 整合包会跳过内核校验，安装器只能靠 opkg 的依赖检查兜底。"
fi

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
COMMIT=$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)
# 版本号来自仓库根的 VERSION（每次推送改动都要 bump，整合包名与清单里都带上，
# 升级时一眼能看出装的是哪一版）
VER=$(head -1 "$REPO_ROOT/VERSION" 2>/dev/null | tr -d ' \r\n')
[ -n "$VER" ] || VER="0.0.0"
NAME="be72pro-fwx-pkg-${VER}-b${BUILD}"
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
ROOT="$STAGE/$NAME"
mkdir -p "$ROOT/ipk"

cp "${IPKS[@]}" "$ROOT/ipk/"
cp "$(dirname "$0")/router-upgrade.sh" "$ROOT/install.sh"
# 升级命令随包发布：安装器会把它装到 /usr/sbin/fwx-update（用户可直接 fwx update）
cp "$(dirname "$0")/fwx-update.sh" "$ROOT/fwx-update.sh"
chmod +x "$ROOT/install.sh"

PKG_LIST=$(cd "$ROOT/ipk" && ls *.ipk | sed 's/\.ipk$//' | tr '\n' ',' | sed 's/,$//')
cat > "$ROOT/manifest" <<EOF
bundle_format=1
version=$VER
build=$BUILD
commit=$COMMIT
kernel=$KREL
packages=${#IPKS[@]}
built=$(date -u +%Y-%m-%dT%H:%M:%SZ)
pkg_list=$PKG_LIST
EOF

# 校验和按“包内相对路径”生成，安装器 cd 到包根目录后 -c 校验
( cd "$ROOT" && sha256sum ipk/*.ipk > sha256sums )

TGZ="$OUTDIR/$NAME.tar.gz"
tar -czf "$TGZ" -C "$STAGE" "$NAME"
# 单独一份整包校验和，供路由器端下载后先验完整再解压
( cd "$OUTDIR" && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256" )

SIZE=$(du -h "$TGZ" | cut -f1)
echo "[bundle] 整合包：$TGZ  ($SIZE)"
echo "[bundle]   版本=$VER  构建号=$BUILD  内核=$KREL  commit=$COMMIT  包数=${#IPKS[@]}"
echo "[bundle]   内容："
tar -tzf "$TGZ" | sed 's/^/      /'
