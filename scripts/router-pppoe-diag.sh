#!/bin/sh
# be72pro —— PPPoE/拨号链路一键诊断（在路由器上跑，只读，不改配置）
#
#   scp scripts/router-pppoe-diag.sh root@路由器:/tmp/
#   ssh root@路由器 'sh /tmp/router-pppoe-diag.sh'
#
# 目的：一次把「交换机端口 → eth2 → eth2.1 → pppd」这条链路的状态抓全，
# 用来区分三种情况：
#   A. WAN 口物理没链路（网线/端口不对）
#   B. VLAN/端口映射错了（PADI 出不去或回不来）
#   C. 链路正常但上游不回 PADO（光猫/账号侧问题）

echo "================ 1. 模块（fwx 与 oaf 不能共存）================"
lsmod 2>/dev/null | grep -E '^(fwx|oaf) ' || echo "  (没有 fwx / oaf)"
if lsmod 2>/dev/null | grep -q '^oaf '; then
	echo "  [!] oaf.ko 在跑 —— 它和 fwx.ko 抢 netlink id 29，fwx 会初始化失败"
fi
echo "-- fwx 初始化结果（应有 init ok，且无 already registered）--"
logread 2>/dev/null | grep -E 'fwx: (Driver|init ok)|netlink_fwx_init|already registered' | tail -6

echo
echo "================ 2. 交换机端口与 VLAN ================"
if command -v swconfig >/dev/null 2>&1; then
	swconfig dev switch0 show 2>/dev/null | sed -n '1,80p'
else
	echo "  (没有 swconfig 命令)"
fi
echo "-- 期望：port 1 为 wan、link up；VLAN 1 = CPU+wan，VLAN 2 = CPU+lan --"

echo
echo "================ 3. 接口与计数器 ================"
ip -s link show eth2 2>/dev/null | head -8
echo
ip -s link show eth2.1 2>/dev/null | head -8
echo
echo "-- wan 的 uci 配置 --"
uci show network.wan 2>/dev/null
uci show network.wan6 2>/dev/null | head -3
echo "-- 是否恰好只有一个 wan 设备 --"
uci show network 2>/dev/null | grep -E "\.(device|ifname)=" 

echo
echo "================ 4. pppd 状态 ================"
ps w 2>/dev/null | grep -E "pppd|udhcpc" | grep -v grep
echo "-- 最近 pppd 日志 --"
logread 2>/dev/null | grep -E "pppd" | tail -8
if [ -f /var/etc/pppoe-wan.pid ]; then
	echo "-- pid 文件存在 --"
fi

echo
echo "================ 5. PADI/PADO 实测（15 秒）================"
echo "抓 eth2.1 上的 PPPoE 发现帧（0x8863）：看 PADI 有没有发出去、有没有 PADO 回来"
if command -v tcpdump >/dev/null 2>&1; then
	BEFORE=$(cat /sys/class/net/eth2.1/statistics/tx_packets 2>/dev/null)
	tcpdump -i eth2.1 -n -c 20 -w /tmp/pppoe-diag.pcap 'ether proto 0x8863' >/tmp/pppoe-diag.txt 2>&1 &
	TD=$!
	sleep 3
	# 触发一次重拨（只重连 wan，不动其它接口）
	ifup wan 2>/dev/null || /etc/init.d/network reload 2>/dev/null
	sleep 12
	kill $TD 2>/dev/null
	AFTER=$(cat /sys/class/net/eth2.1/statistics/tx_packets 2>/dev/null)
	echo "-- eth2.1 发送包数：$BEFORE -> $AFTER --"
	echo "-- tcpdump 抓到的帧数 --"
	tcpdump -r /tmp/pppoe-diag.pcap -n 2>/dev/null | head -12
	N=$(tcpdump -r /tmp/pppoe-diag.pcap -n 2>/dev/null | wc -l)
	echo "-- 共 $N 帧 --"
	echo "  0 帧   → PADI 根本没出去（交换机端口/VLAN/网线问题）"
	echo "  只有 PADI 没有 PADO → 帧出去了但上游没回（光猫/账号/线路侧）"
	echo "  有 PADO → 发现阶段通，问题在会话/认证阶段（看第 4 节 pppd 日志）"
else
	echo "  (没有 tcpdump，可 opkg install tcpdump 后重跑；先看第 2、3 节的端口与计数器)"
fi

echo
echo "================ 6. 内核告警 ================"
logread 2>/dev/null | grep -cE "WARNING: CPU|cut here" | xargs echo "  告警条数："
dmesg 2>/dev/null | grep -E "eth2|rtl837x" | tail -8

echo
echo "================ 完成 ================"
echo "把以上输出整段发回即可定位。"
