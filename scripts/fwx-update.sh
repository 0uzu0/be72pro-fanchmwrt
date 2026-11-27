#!/bin/sh
# be72pro-fanchmwrt —— 在线升级（路由器上运行）
#
#   fwx-update              # 查最新整合包 → 下载 → 校验 → 安装
#   fwx-update --check      # 只检查有没有新版本，不装
#   fwx-update --url <URL>  # 直接指定整合包直链（走自建镜像时用这个）
#   fwx-update --force      # 跳过内核版本校验（不建议）
#
# 只升级「组件」（fwxd / fwx.ko / LuCI 页面 / 主题 / 特征库），不动内核。
# 内核补丁、DTS、底座版本变化仍必须刷整机镜像。
#
# 装到路由器上（只需一次）：
#   scp scripts/fwx-update.sh root@路由器:/usr/sbin/fwx-update && chmod +x /usr/sbin/fwx-update

set -u

REPO="${FWX_REPO:-0uzu0/be72pro-fanchmwrt}"
API="https://api.github.com/repos/$REPO/releases"
TMP="/tmp/fwx-update"
MARKER="/etc/fwx_pkg_build"

CHECK_ONLY=0
FORCE=""
URL=""

while [ $# -gt 0 ]; do
	case "$1" in
		--check|-c) CHECK_ONLY=1 ;;
		--force|-f) FORCE="--force" ;;
		--url) shift; URL="${1:-}" ;;
		-h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
		*) echo "未知参数：$1（--help 看用法）"; exit 2 ;;
	esac
	shift
done

log() { echo "[fwx-update] $*"; }
die() { echo "[fwx-update][FATAL] $*" >&2; exit 1; }

# ---------- 下载工具（OpenWrt 可能是 uclient-fetch，也可能只有 busybox wget） ----------
have() { command -v "$1" >/dev/null 2>&1; }

fetch_stdout() {
	if have uclient-fetch; then uclient-fetch -q -O - "$1" 2>/dev/null
	elif have wget; then wget -q -O - "$1" 2>/dev/null
	elif have curl; then curl -fsSL "$1" 2>/dev/null
	else return 1
	fi
}

fetch_file() { # url 输出文件
	if have uclient-fetch; then uclient-fetch -q -O "$2" "$1" 2>/dev/null
	elif have wget; then wget -q -O "$2" "$1" 2>/dev/null
	elif have curl; then curl -fsSL -o "$2" "$1" 2>/dev/null
	else return 1
	fi
}

have uclient-fetch || have wget || have curl \
	|| die "没有可用的下载工具（uclient-fetch / wget / curl 都没有）"

# ---------- 1. 确定要装的地址 ----------
if [ -z "$URL" ]; then
	log "查询最新发布：$API"
	JSON=$(fetch_stdout "$API") || die "拉取发布列表失败 —— 检查路由器的 DNS/外网；或改用 --url 指定直链"
	[ -n "${JSON:-}" ] || die "发布列表为空（仓库私有或网络被拦）；可用 --url 指定直链"

	URL=$(echo "$JSON" | tr ',' '\n' \
		| grep '"browser_download_url"' \
		| grep 'be72pro-fwx-pkg-' \
		| head -1 \
		| sed 's/.*"\(https[^"]*\)".*/\1/')
	[ -n "${URL:-}" ] || die "最新发布里没有整合包（可能这次构建没产出）；可用 --url 指定直链"
fi

NEW_BUILD=$(echo "$URL" | sed -n 's/.*be72pro-fwx-pkg-\([0-9][0-9]*\)\.tar\.gz.*/\1/p')
CUR_BUILD=$(cat "$MARKER" 2>/dev/null || echo "未知")

log "最新整合包：${URL##*/}"
log "当前组件版本：$CUR_BUILD"

if [ "$CHECK_ONLY" = "1" ]; then
	if [ -n "$NEW_BUILD" ] && [ "$NEW_BUILD" = "$CUR_BUILD" ]; then
		echo "已是最新（$CUR_BUILD）。"
	else
		echo "有新版本：$NEW_BUILD（当前 $CUR_BUILD）"
	fi
	exit 0
fi

# ---------- 2. 下载 ----------
rm -rf "$TMP"
mkdir -p "$TMP"
TGZ="$TMP/${URL##*/}"

log "下载中…（${URL##*/}）"
fetch_file "$URL" "$TGZ" || die "下载失败：$URL"
[ -s "$TGZ" ] || die "下载到的文件是空的"

# 整包校验和（CI 与整合包一起发布）；拿不到就靠包内 sha256sums 兜底
fetch_file "$URL.sha256" "$TGZ.sha256" || true
if [ -s "$TGZ.sha256" ] && have sha256sum; then
	EXPECT=$(awk '{print $1}' "$TGZ.sha256" | head -1)
	ACTUAL=$(sha256sum "$TGZ" | awk '{print $1}')
	if [ "$EXPECT" != "$ACTUAL" ]; then
		die "整包校验失败（期望 $EXPECT / 实际 $ACTUAL）—— 重下或换镜像"
	fi
	log "整包校验通过"
fi

# ---------- 3. 解包并交给 install.sh ----------
DIR="$TMP/pkg"
mkdir -p "$DIR"
tar -xzf "$TGZ" -C "$DIR" || die "解压失败（文件可能不完整）"
INSTALL=$(ls "$DIR"/*/install.sh 2>/dev/null | head -1)
[ -n "${INSTALL:-}" ] || die "包里没有 install.sh"

log "开始安装…"
sh "$INSTALL" ${FORCE:+"$FORCE"}
RC=$?
if [ $RC -eq 0 ]; then
	log "完成。版本已记为 $(cat "$MARKER" 2>/dev/null || echo '?')"
else
	echo "[fwx-update] 安装返回 $RC —— 看上面的 [upgrade] 输出定位"
fi
exit $RC
