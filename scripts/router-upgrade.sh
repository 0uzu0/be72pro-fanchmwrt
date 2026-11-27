#!/bin/sh
# be72pro-fanchmwrt —— 整合包安装器（也可直接装一个 ipk 目录）
#
# 两种用法：
#   1) 整合包（推荐）：解包后执行包内 install.sh，会自动校验内核版本与文件完整性
#        tar -xzf be72pro-fwx-pkg-31.tar.gz -C /tmp
#        sh /tmp/be72pro-fwx-pkg-31/install.sh
#   2) 散装 ipk 目录：
#        scp -r out/ipk root@路由器:/tmp/ipk
#        ssh root@路由器 'sh /tmp/ipk/router-upgrade.sh'
#
# 参数：--force 跳过内核版本校验（不建议；装错内核模块会直接起不来）
#
# 能覆盖：fwxd（接口/规则脚本/配置/特征库）、fwx.ko、LuCI 页面、主题样式。
# 不能覆盖：内核补丁（980 nf_conn）、DTS、defconfig、底座版本切换 —— 那些必须刷整机。

set -u

FORCE=0
ARG=""
for a in "$@"; do
	case "$a" in
		--force|-f) FORCE=1 ;;
		*) ARG="$a" ;;
	esac
done

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
MANIFEST=""
[ -f "$SCRIPT_DIR/manifest" ] && MANIFEST="$SCRIPT_DIR/manifest"

log() { echo "[upgrade] $*"; }
die() { echo "[upgrade][FATAL] $*" >&2; exit 1; }

# ---------- 0. 定位 ipk 目录 ----------
if [ -n "$ARG" ]; then
	DIR="$ARG"
elif [ -d "$SCRIPT_DIR/ipk" ]; then
	DIR="$SCRIPT_DIR/ipk"
else
	DIR="/tmp/ipk"
fi

if [ -d "$DIR" ]; then
	PKGS=$(ls "$DIR"/*.ipk 2>/dev/null)
else
	PKGS=$(ls $DIR 2>/dev/null)
fi
[ -n "${PKGS:-}" ] || die "没找到 .ipk（路径：$DIR）"

# ---------- 1. 整合包校验 ----------
m_build=""
m_kernel=""
if [ -n "$MANIFEST" ]; then
	m_build=$(sed -n 's/^build=//p' "$MANIFEST" | head -1)
	m_kernel=$(sed -n 's/^kernel=//p' "$MANIFEST" | head -1)
	m_commit=$(sed -n 's/^commit=//p' "$MANIFEST" | head -1)
	cur_kernel=$(uname -r)

	log "整合包信息：build=$m_build  commit=$m_commit  目标内核=$m_kernel"

	if [ -f "$SCRIPT_DIR/sha256sums" ] && command -v sha256sum >/dev/null 2>&1; then
		if ( cd "$SCRIPT_DIR" && sha256sum -c sha256sums >/dev/null 2>&1 ); then
			log "完整性校验通过（sha256）"
		else
			die "整合包校验失败 —— 下载不完整或被改过，请重新下载"
		fi
	fi

	# 内核版本未知时不做拦截（只警告）：说明打包时没读到内核 release，
	# 这种情况下 opkg 自身的 kernel 依赖校验仍是最后一道防线。
	if [ -n "$m_kernel" ] && [ "$m_kernel" = "unknown" ]; then
		log "[警告] 整合包没记录目标内核版本，跳过版本校验（opkg 仍会校验依赖）"
	fi
	if [ -n "$m_kernel" ] && [ "$m_kernel" != "unknown" ] && [ "$m_kernel" != "$cur_kernel" ]; then
		if [ "$FORCE" = "1" ]; then
			log "[警告] 内核版本不匹配（包 $m_kernel / 本机 $cur_kernel），已按 --force 继续"
		else
			die "内核版本不匹配：包是 $m_kernel，本机是 $cur_kernel。
     这个包不是给当前固件构建的 —— fwx.ko 装上去也加载不了。
     正确做法：用与本机固件同批次的整合包；确要强装加 --force。"
		fi
	fi
fi

# ---------- 2. 安装顺序：库 → 主程序 → 内核模块 → 界面 → 主题 ----------
ORDER="libfwx_common fwxd kmod-fwx luci-app-fwx luci-theme-fanchmwrt"
ORDERED=""
for key in $ORDER; do
	for p in $PKGS; do
		case "$(basename "$p")" in
			$key*) ORDERED="$ORDERED $p" ;;
		esac
	done
done
# 其余包（i18n 等）补在后面
for p in $PKGS; do
	case " $ORDERED " in
		*" $p "*) ;;
		*) ORDERED="$ORDERED $p" ;;
	esac
done
ORDERED=$(echo "$ORDERED" | tr ' ' '\n' | sed '/^$/d')
[ -n "$ORDERED" ] || die "没解析出可安装的包"

log "将安装以下包："
echo "$ORDERED" | sed 's/^/    /'

echo
log "升级前版本："
opkg list-installed 2>/dev/null | grep -E '^(fwxd|libfwx_common|kmod-fwx|luci-theme-fanchmwrt) ' | sed 's/^/    /' || true

# ---------- 3. 停服务 ----------
echo
if [ -x /etc/init.d/fwx ]; then
	log "停止 fwx 服务"
	/etc/init.d/fwx stop 2>/dev/null || true
fi

# ---------- 4. 卸内核模块（只有带 kmod-fwx 时才需要） ----------
NEED_RMMOD=0
for p in $ORDERED; do
	case "$(basename "$p")" in
		kmod-fwx*) NEED_RMMOD=1 ;;
	esac
done
if [ "$NEED_RMMOD" = "1" ] && lsmod 2>/dev/null | grep -q '^fwx '; then
	log "卸载 fwx.ko（新模块必须重新 insmod 才生效）"
	rmmod fwx 2>/dev/null || log "  rmmod 失败（可能仍被占用），继续尝试"
fi

# ---------- 5. 安装 ----------
echo
FAIL=0
for p in $ORDERED; do
	log "安装 $(basename "$p")"
	# 不能用 `opkg ... | sed` 判断成败：管道退出码是 sed 的
	if opkg install --force-reinstall "$p" >/tmp/.fwx-upgrade-opkg.log 2>&1; then
		sed 's/^/    /' /tmp/.fwx-upgrade-opkg.log
	else
		FAIL=1
		sed 's/^/    /' /tmp/.fwx-upgrade-opkg.log
		echo "    ^^ 这一个失败了，继续装其余包"
	fi
done
rm -f /tmp/.fwx-upgrade-opkg.log

# ---------- 6. 起服务 ----------
echo
if [ -x /etc/init.d/fwx ]; then
	log "启动 fwx 服务"
	/etc/init.d/fwx start 2>/dev/null || true
fi
# LuCI 页面是 Lua，改完要清编译缓存，否则界面还是旧的
rm -f /tmp/luci-indexcache* /tmp/luci-modulecache/* 2>/dev/null || true

# ---------- 7. 验证 ----------
echo
log "验证："
if lsmod 2>/dev/null | grep -q '^fwx '; then
	echo "    [v] fwx.ko 已加载"
else
	echo "    [x] fwx.ko 未加载 —— 若是未编译内核模块的包属正常；否则看 logread | grep fwx"
fi
if [ -x /usr/sbin/fwxd ]; then
	echo "    [v] fwxd 时间戳：$(ls -l /usr/sbin/fwxd | awk '{print $6" "$7" "$8"  "$5" bytes"}')"
else
	echo "    [x] 没有 /usr/sbin/fwxd"
fi
R=$(ubus call fwx common '{"api":"get_init_status","data":{}}' 2>/dev/null)
if echo "$R" | grep -q '"code"'; then
	echo "    [v] fwxd 应答正常：$(echo "$R" | head -c 120)"
else
	echo "    [x] fwxd 无应答 —— 看 logread | grep fwxd"
fi
echo "    特征库条目：$(grep -c '^[0-9]' /etc/fwxd/feature.cfg 2>/dev/null || echo '?')"
echo "    黑名单规则日志：$(grep -c 'blacklist rule' /tmp/log/rule_manager.log 2>/dev/null || echo '?') 条"

echo
if [ "$FAIL" = "1" ]; then
	echo "[upgrade] 有包安装失败。常见原因："
	echo "  1) 整合包与本机固件不是同一版本 → 内核校验不过（这是保护，换正确的包）"
	echo "  2) 磁盘空间不足 → df -h /overlay"
	echo "  3) 依赖缺失 → opkg install --force-depends <包>（谨慎）"
	exit 1
fi

# 装成功才记录版本，供 fwx-update --check 比对
if [ -n "$MANIFEST" ] && [ -n "$m_build" ]; then
	echo "$m_build" > /etc/fwx_pkg_build 2>/dev/null || true
	log "已记录组件版本：$m_build（/etc/fwx_pkg_build）"
fi

echo "[upgrade] 完成。界面刷新一下（CSS/JS 有浏览器缓存，必要时 Ctrl+F5）。"
