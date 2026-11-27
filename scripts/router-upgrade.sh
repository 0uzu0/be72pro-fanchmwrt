#!/bin/sh
# be72pro-fanchmwrt —— 免刷机升级：在路由器上安装同一次构建产出的 .ipk
#
# 用法（先在电脑上把 ipk 传上去）：
#   scp -r out/ipk root@192.168.1.1:/tmp/ipk
#   ssh root@192.168.1.1 'sh /tmp/ipk/router-upgrade.sh'
#
# 也可以只装某一个包：
#   ssh root@192.168.1.1 'sh /tmp/ipk/router-upgrade.sh /tmp/ipk/fwxd_*.ipk'
#
# 能覆盖什么：fwxd（接口/规则脚本/配置/特征库）、fwx.ko、LuCI 页面、主题样式。
# 不能覆盖什么：内核补丁（980 nf_conn）、DTS、defconfig、底座版本切换 —— 那些必须刷整机。
#
# 安全约束：ipk 必须与本机固件【同一次构建】产出。opkg 会校验内核版本，
# 不匹配会直接拒绝（拒绝是好事，说明混用了不同构建的包）。

set -u

DIR="/tmp/ipk"
[ $# -gt 0 ] && DIR="$1"

log() { echo "[upgrade] $*"; }
die() { echo "[upgrade][FATAL] $*" >&2; exit 1; }

# ---------- 0. 定位要装的包 ----------
if [ -d "$DIR" ]; then
	PKGS=$(ls "$DIR"/*.ipk 2>/dev/null)
else
	PKGS=$(ls $DIR 2>/dev/null)
fi
[ -n "${PKGS:-}" ] || die "没找到 .ipk（传入的路径：$DIR）"

# 安装顺序很重要：库 → 主程序 → 内核模块 → 界面 → 主题
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

# 回滚提示：把旧 ipk 留着，出问题用同一条命令装回去
log "建议先备份当前包（出问题可原样装回）："
echo "    opkg list-installed | grep -E 'fwxd|kmod-fwx' > /tmp/before-upgrade.txt"

# ---------- 1. 停服务 ----------
echo
if [ -x /etc/init.d/fwx ]; then
	log "停止 fwx 服务"
	/etc/init.d/fwx stop 2>/dev/null || true
fi

# ---------- 2. 卸内核模块（只有带 kmod-fwx 时才需要） ----------
NEED_RMMOD=0
for p in $ORDERED; do
	case "$(basename "$p")" in
		kmod-fwx*) NEED_RMMOD=1 ;;
	esac
done
if [ "$NEED_RMMOD" = "1" ] && lsmod 2>/dev/null | grep -q '^fwx '; then
	log "卸载 fwx.ko（新模块必须重新 insmod 才生效）"
	rmmod fwx 2>/dev/null || log "  rmmod 失败（可能仍被占用），将尝试继续"
fi

# ---------- 3. 安装 ----------
echo
FAIL=0
for p in $ORDERED; do
	log "安装 $(basename "$p")"
	# 注意：不能用 `opkg ... | sed` 去判断成败 —— 管道的退出码是 sed 的，
	# 装包失败会被误报成功。先落盘再按 opkg 自己的退出码判断。
	if opkg install --force-reinstall "$p" >/tmp/.fwx-upgrade-opkg.log 2>&1; then
		sed 's/^/    /' /tmp/.fwx-upgrade-opkg.log
	else
		FAIL=1
		sed 's/^/    /' /tmp/.fwx-upgrade-opkg.log
		echo "    ^^ 这一个失败了，继续装其余包"
	fi
done
rm -f /tmp/.fwx-upgrade-opkg.log

# ---------- 4. 起服务 ----------
echo
if [ -x /etc/init.d/fwx ]; then
	log "启动 fwx 服务"
	/etc/init.d/fwx start 2>/dev/null || true
fi
# LuCI 页面是 Lua，改完要清编译缓存，否则界面还是旧的
rm -f /tmp/luci-indexcache* /tmp/luci-modulecache/* 2>/dev/null || true

# ---------- 5. 验证 ----------
echo
log "验证："
if lsmod 2>/dev/null | grep -q '^fwx '; then
	echo "    [v] fwx.ko 已加载"
else
	echo "    [x] fwx.ko 未加载 —— 若是 WITH_KMOD=0 的包属正常；否则看 logread"
fi
if [ -x /usr/sbin/fwxd ]; then
	echo "    [v] fwxd 存在：$(ls -l /usr/sbin/fwxd | awk '{print $5" bytes  "$6" "$7" "$8}')"
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
	echo "  1) ipk 与固件不是同一次构建 → 内核版本校验不过（这是保护，换同批包）"
	echo "  2) 磁盘空间不足 → df -h /overlay"
	echo "  3) 依赖缺失 → opkg install --force-depends <包>（谨慎）"
	exit 1
fi
echo "[upgrade] 完成。界面刷新一下（CSS/JS 有浏览器缓存，必要时 Ctrl+F5）。"
