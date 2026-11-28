-- be72: 四类网络加速（游戏/视频/下载/办公）
-- uci fwx.accel.{game,video,download,office} -> rule_manager 周期同步到
-- /proc/sys/fwx/accel_ids（内核位图），命中的应用跳过 DPI 深度解析与审计记录。
module("luci.controller.fwx_accel", package.seeall)

function index()
	if not nixio.fs.access("/etc/config/fwx") then
		return
	end
	-- 统一 UI（v2.1.0）：页面提升为顶级菜单节点（原注册在 admin/fwx 匿名 API
	-- 节点下，侧边栏没有入口，只能手输 URL）。旧地址留 alias 兼容。
	entry({"admin", "fwx_accel"}, template("fwx_accel/accel"), _("Network Acceleration"), 26).dependent = true
	entry({"admin", "fwx", "fwx_accel"}, alias("admin", "fwx_accel"), nil).leaf = true
	entry({"admin", "fwx_accel", "status"}, call("api_status"), nil).leaf = true
	entry({"admin", "fwx_accel", "set"}, call("api_set"), nil).leaf = true
end

local function hook_state()
	local f = io.open("/sys/kernel/debug/hnat/hook_toggle", "r")
	if not f then
		return "unknown"
	end
	local v = (f:read("*l") or ""):gsub("%s+", "")
	f:close()
	return v
end

local function accel_state()
	local uci = require "luci.model.uci"
	local x = uci.cursor()
	local function on(name)
		return tonumber(x:get("fwx", "accel", name) or 0) or 0
	end
	return {
		game = on("game"),
		video = on("video"),
		download = on("download"),
		office = on("office"),
		hook = hook_state()
	}
end

function api_status()
	luci.http.prepare_content("application/json")
	local json = require "luci.jsonc"
	luci.http.write(json.stringify(accel_state()))
end

function api_set()
	luci.http.prepare_content("application/json")
	local json = require "luci.jsonc"
	local uci = require "luci.model.uci"
	local x = uci.cursor()

	local function v(name)
		return tonumber(luci.http.formvalue(name) or 0) or 0
	end

	if not x:get("fwx", "accel") then
		x:set("fwx", "accel", "accel")
	end
	x:set("fwx", "accel", "game", tostring(v("game")))
	x:set("fwx", "accel", "video", tostring(v("video")))
	x:set("fwx", "accel", "download", tostring(v("download")))
	x:set("fwx", "accel", "office", tostring(v("office")))
	x:save("fwx")
	x:commit("fwx")

	-- rule_manager 每 10 秒把开启类别的 app_id 同步到 /proc/sys/fwx/accel_ids
	luci.http.write(json.stringify({ code = 2000, data = accel_state() }))
end
