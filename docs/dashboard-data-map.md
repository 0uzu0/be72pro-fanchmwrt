# 仪表盘 1:1 复刻 —— 数据源映射表

> 设计基准：`docs/ui-redesign.html` 的 `#v-dash`（g4 指标卡 + 接口流量/网络状态 + 活跃应用/活跃主机）
> 原则：**每个数字都必须来自真实 API 或真实系统文件，不允许占位/假数据**
> 实测环境：设备 ubus `get_dashboard_common` 真实返回（2026-11-28）

## 一、指标卡（g4）

| 设计稿 | 设计稿字段 | 真实数据源 | 实现状态 |
|---|---|---|---|
| CPU 占用 | `cpu` | `system_status.cpu` | ✅ **已修**：原实现在 busybox 下恒 0（`top -n 1 \| grep 'CPU:'` 取不到值），改为 `/proc/stat` 两次采样，页面轮询 5s 后为真实值 |
| 内存占用 | `mem` | `system_status.total_mem` / `used_mem`（KB） | ✅ 真实（实测 980MB 总 / 305MB 用） |
| 在线终端 | `device_count` | `system_status.client_num` | ✅ 真实（实测 17 台） |
| 运行时长 | `uptime` | `system_status.uptime`（秒） | ✅ 真实（实测 43573s ≈ 12 小时） |

补充可用的真实指标：`connections`（641 条连接）、`cpu_temp`(51℃)/`wifi_temp`(39℃)、`storage.tmp/root`、`flow.today_up/down`、`model`、`fwx_version`、`kernel_version`。

## 二、接口流量（bars）

| 设计稿 | 设计稿字段 | 真实数据源 | 实现方案 |
|---|---|---|---|
| 逐接口 RX/TX 速率条 | `interface_traffic[].rx/tx` | **get_dashboard_common 无此字段** | ⚠️ 用 LuCI ubus `network.device status`（`rx_bytes`/`tx_bytes`）两次采样算 MB/s；接口取 `wan`(pppoe-wan/eth2.1)、`br-lan`、`eth1` |

速率条宽度 = 当前速率 / 该接口链路速率（port_status.speed，真实值）。

## 三、网络状态（kv 行）

| 设计稿 | 设计稿字段 | 真实数据源 | 状态 |
|---|---|---|---|
| WAN IP | `wan_ip` | `network_status.wan.ip` | ✅ 真实（实测 116.147.108.213） |
| WAN 状态 | `wan_status` | `network_status.internet`（0/1）+ `wan.ip` 非空 | ✅ 真实 |
| 连接方式 | — | `uci network.wan.proto`（pppoe/dhcp/static）| ✅ 需页面侧读 uci（LuCI 模板可直接输出） |
| DNS | `dns` | `network_status.wan.dns[]` | ✅ 真实（221.6.4.66 / 58.240.57.33） |
| 工作模式 | — | `network_status.work_mode`（0 网关 / 1 旁路） | ✅ 真实 |

端口状态：`network_status.port_status[]`（carrier 判定，已修：只列 lan1-4 + eth1 + WAN，过滤 eth2.2 内部 trunk）。

## 四、活跃应用（dev-list）

| 设计稿 | 设计稿字段 | 真实数据源 | 实现方案 |
|---|---|---|---|
| 应用名 + 累计时长 + 速率 | `active_app[].name/total_time/up/down` | `active_app.list[]` 只有 `name/mac/domain`（实时连接，**无时长/流量**） | ⚠️ 改用 `get_daily_top_apps`（今日 top 应用，含时长与流量）——本 API 已在 fwxd 分派表，需页面侧新增一个 fetch |

真实可显示：应用名（抖音/哔哩哔哩/微信…）、所属设备 MAC、当前域名。**时长/流量必须走 `get_daily_top_apps`，不得用连接时间冒充累计时长**。

## 五、活跃主机（dev-list）

| 设计稿 | 设计稿字段 | 真实数据源 | 实现方案 |
|---|---|---|---|
| 主机名 + IP + 速率 | `active_host[].hostname/ip/up/down` | `active_host.list[]` 有 `name/hostname/nickname/url`（**无 ip/速率**） | ⚠️ 与 `get_all_users`（flag=3，含 `ip`/`up_rate`/`down_rate`/`today_up_bytes`）按 MAC 合并 |

显示优先级：nickname > hostname > name > MAC（与终端列表一致）。

## 六、设计稿有、当前固件无数据源的模块

| 模块 | 结论 |
|---|---|
| 「依赖能力」（v-dashset） | 可真实：`feature.cfg #version`、`/proc/sys/fwx/feature_count`、`lsmod fwx`、`kernel_version` |
| 「今日流量（逐小时）」 | 可真实：`get_global_traffic_stats` / `get_hourly_top_apps` |
| 「历史流量（近 30 天）」 | 可真实：`get_history_traffic_stats` |
| 「会话曲线（24h）」 | 可真实：`get_user_records` / 会话统计接口 |

## 七、验收标准（做完检查用的清单）

1. 指标卡 4 个数字与 `ubus call fwx common '{"api":"get_dashboard_common","data":{}}'` 的对应字段**完全一致**；
2. 接口速率与 `cat /proc/net/dev` 两次采样结果一致（误差 < 10%）；
3. WAN IP / DNS 与 `ip addr show` / `cat /etc/resolv.conf` 一致；
4. 活跃应用/主机数量与 API 的 `total` 一致，名字不出现占位文本（"应用1"、"设备A"等）；
5. 页面每 5 秒刷新后数字有变化（速率类），不恒为 0；
6. 断网场景：拔掉 WAN 后网络状态显示"未连接"，不得仍显示旧 IP。
