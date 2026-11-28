#!/bin/sh
# be72pro —— 「拨号成功但上不了网 / 连接被重置」分流诊断（在路由器上跑）
#
#   scp scripts/router-net-diag.sh root@路由器:/tmp/
#   ssh root@路由器 'sh /tmp/router-net-diag.sh'
#   ssh root@路由器 'sh /tmp/router-net-diag.sh --try-without-fwx'   # 关键对照实验
#
# 默认只读。带 --try-without-fwx 时会临时停 fwx + 卸载 fwx.ko 做对照，
# 测完自动装回去（会重置一次 DPI 会话，不影响配置）。

set -u
TRY_NO_FWX=0
[ "${1:-}" = "--try-without-fwx" ] && TRY_NO_FWX=1

hr() { echo "------------------------------------------------------------"; }
sec() { echo; echo "================ $* ================"; }

sec "1. WAN 口与路由"
ip -4 addr show pppoe-wan 2>/dev/null | grep -E "inet |mtu" || echo "  [!] 没有 pppoe-wan"
echo "-- MTU（PPPoE 应为 1492）--"
ip link show pppoe-wan 2>/dev/null | sed -n '1p'
ip link show br-lan 2>/dev/null | sed -n '1p'
echo "-- 默认路由 --"
ip route show default
echo "-- 从路由器自己 ping（验证 WAN 侧通不通）--"
for t in 223.5.5.5 114.114.114.114; do
	printf "  ping %-16s " "$t"
	if ping -c 2 -W 2 "$t" >/dev/null 2>&1; then echo "通"; else echo "不通"; fi
done
printf "  DNS 解析 baidu.com   "
if nslookup baidu.com >/dev/null 2>&1; then echo "通"; else echo "失败"; fi

sec "2. MTU / MSS（大包能不能过）"
echo "  小包 100 字节、大包 1400、超 MTU 1500（应失败）"
for sz in 100 1400 1500; do
	printf "  ping -s %-5s " "$sz"
	# -M do 禁止分片；1472+28=1500 超过 1492 就一定失败
	if ping -c 1 -W 2 -M do -s $sz 223.5.5.5 >/dev/null 2>&1; then echo "通"; else echo "不通"; fi
done
echo "  若 100/1400 通、1500 不通 → 正常（MTU 1492）"
echo "  若 1400 不通 → MTU/MSS 或 NAT 有问题"

sec "3. NAT 与防火墙（fw4 / nftables）"
if command -v nft >/dev/null 2>&1; then
	echo "-- 各链 policy 与关键规则 --"
	nft -a list ruleset 2>/dev/null | grep -E "chain (forward|input|output|srcnat|dstnat|.*postrouting)|type nat hook|masquerade|fullcone|policy (drop|accept)" | head -24
	echo "-- flowtable / offload --"
	nft list flowtables 2>/dev/null | head -12
else
	echo "  (没有 nft 命令)"
fi
echo "-- conntrack --"
printf "  条目数："; cat /proc/sys/net/netfilter/nf_conntrack_count 2>/dev/null || echo "?"
echo "  抽样（前 5 条）:"; head -5 /proc/net/nf_conntrack 2>/dev/null | cut -c1-140

sec "4. fwx 内核状态"
for k in appfilter_enable macfilter_enable record_enable tcp_rst by_pass_accl; do
	printf "  %-18s = " "$k"; cat "/proc/sys/fwx/$k" 2>/dev/null || echo "(不存在)"
done
echo "-- fwxd 与内核是否在建链 --"
ps w 2>/dev/null | grep -E "[f]wxd" | head -2
logread 2>/dev/null | grep -E "fwx: (Driver|init ok)|already registered|netlink_fwx_init" | tail -4
echo "-- fwx 是否在丢包（有则说明命中了过滤规则）--"
logread 2>/dev/null | grep -E "##Drop (App|MAC) filter rule|drop appid|drop mac" | tail -5
echo "  （以上为空 = fwx 没丢过包）"

sec "5. TCP 复位/丢弃计数"
if command -v nstat >/dev/null 2>&1; then
	nstat -az 2>/dev/null | grep -iE "OutRsts|Retrans|InErrs|ListenDrops" | head -6
else
	awk '/TcpExt/ && NR<80 {print}' /proc/net/netstat | head -2 >/dev/null
	echo "  (无 nstat；跳过)"
fi

sec "6. 从 LAN 侧发起的连通性（需要手动确认）"
LANIP=$(ip -4 addr show br-lan 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1 | head -1)
echo "  路由器 LAN 地址：${LANIP:-未知}"
echo "  请在电脑上执行：  ping 223.5.5.5"
echo "                    curl -v http://www.baidu.com --max-time 8"
echo "  并在本脚本跑完后把出错信息一起发回"

if [ "$TRY_NO_FWX" = "1" ]; then
	sec "7. 对照实验：停掉 fwx 后能否上网"
	echo "  （会临时卸载 fwx.ko，测完自动装回）"
	/etc/init.d/fwx stop 2>/dev/null || true
	sleep 1
	if lsmod 2>/dev/null | grep -q '^fwx '; then
		rmmod fwx 2>/dev/null || echo "  [!] rmmod 失败（模块仍被占用）"
	fi
	echo "  现在从路由器 ping："
	for t in 223.5.5.5; do
		printf "    ping %-16s " "$t"
		if ping -c 2 -W 2 "$t" >/dev/null 2>&1; then echo "通"; else echo "不通"; fi
	done
	echo ""
	echo "  >>> 请在这 30 秒内用电脑试一下上网 <<<"
	echo "      - 能上  → 问题在 fwx.ko（钩子/规则），不是网络本身"
	echo "      - 还不能上 → 问题在 NAT / MTU / 上游，与 fwx 无关"
	sleep 30
	/etc/init.d/fwx start 2>/dev/null || insmod /lib/modules/$(uname -r)/fwx.ko 2>/dev/null || true
	sleep 1
	lsmod 2>/dev/null | grep -q '^fwx ' && echo "  fwx 已重新加载" || echo "  [!] fwx 未能自动装回，手动执行 /etc/init.d/fwx start"
fi

sec "完成"
echo "把以上整段输出发回；若做了对照实验，请一并说明「停 fwx 后能否上网」。"
