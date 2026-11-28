// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * be72 24.10.4 fwxd 扩展接口集（自行开发，不移植 25.12）。
 *
 * 覆盖 README「当前实测缺口」的全部条目，数据全部来自 24.10.4
 * fwxd 自身已有的落盘体系（/tmp/fwx/client_data 或
 * fwx.record.history_data_path 指定的目录）：
 *
 *   get/set_init_status            —— /etc/fwx_init_status（仪表板引导浮层）
 *   get_history_traffic_stats      —— client_data/global/stats/traffic_<date>.json
 *                                     （fwx_user.c 的 save_global_traffic_stats_to_file
 *                                      一直在写，只是从来没人读它）
 *   get/add/del_record_whitelist   —— uci fwx_record.@whitelist[0].whitelist，
 *                                     key 与 25.12 完全一致（将来换版本不迁移数据）
 *   get_user_daily_summary         —— client_data/<mac>/stats/hourly_<date>.json
 *                                     + top_apps_<date>.json + 今天实时聚合
 *   get_user_parental_control_rules—— macfilter.@rule[i]（时段规则）
 *                                     + appfilter/macfilter 白名单命中标志
 *
 * 设计原则：
 *   1. 只新增函数，不修改 fwxd 既有逻辑；对既有文件的改动全部是
 *      「锚点插入 2-3 行过滤」，由 scripts/patch_fwxd_ext.py 完成。
 *   2. 返回结构逐字段对照界面包（packages@3c6ae71）里的消费代码写，
 *      见各函数头注释里的出处。
 *   3. 不假装有数据：文件缺失/为空的日子返回 0 值条目而不是编造。
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <time.h>
#include <fcntl.h>
#include <unistd.h>
#include <json-c/json.h>
#include <uci.h>

#include "fwx.h"
#include "fwx_uci.h"
#include "fwx_user.h"

/* fwx_user.c 里是非 static 定义，但没在头文件里声明 */
extern const char *get_client_data_base_dir(void);
/* fwx_config.c 的应用名表（fwx_user.h 不含此声明） */
extern char *get_app_name_by_id(int id);

/* ---- 前向声明：部分接口按文件顺序定义在后部，而调用点在前部 ---- */
static int ext_mbl_hit(const char *mac);
static void ext_collect_macfilter_rules(struct json_object *arr, const char *norm,
                                        int *matched);

#ifndef EXT_SECONDS_PER_DAY
#define EXT_SECONDS_PER_DAY 86400
#endif
#define EXT_MAC_LEN 32
#define EXT_PATH_LEN 512
#define EXT_DAYS_MAX 365
#define EXT_SUMMARY_DAYS 30
#define EXT_TOP_APPS_NUM 10

/* ---------------------------------------------------------------- 基础工具 */

static unsigned int ext_today_start(void)
{
    time_t t = time(NULL);
    struct tm *tm_info = localtime(&t);
    if (!tm_info) {
        return 0;
    }
    tm_info->tm_hour = 0;
    tm_info->tm_min = 0;
    tm_info->tm_sec = 0;
    return (unsigned int)mktime(tm_info);
}

static void ext_date_string(unsigned int timestamp, char *buf, size_t len)
{
    time_t t = (time_t)timestamp;
    struct tm *tm_info = localtime(&t);
    if (!buf || len == 0) {
        return;
    }
    if (!tm_info) {
        buf[0] = '\0';
        return;
    }
    strftime(buf, len, "%Y-%m-%d", tm_info);
}

/* 与 fwx_user.c 的 mac_to_dirname 完全一致：冒号换下划线 */
static void ext_mac_dirname(const char *mac, char *out, size_t len)
{
    size_t i;
    if (!mac || !out || len == 0) {
        return;
    }
    snprintf(out, len, "%s", mac);
    for (i = 0; out[i] != '\0'; i++) {
        if (out[i] == ':') {
            out[i] = '_';
        }
    }
}

/* MAC 规范化：大写 + 冒号分隔（与界面 normalize_mac 一致） */
static void ext_norm_mac(const char *in, char *out, size_t len)
{
    size_t i;
    size_t o = 0;
    if (!in || !out || len == 0) {
        return;
    }
    for (i = 0; in[i] != '\0' && o + 1 < len; i++) {
        unsigned char c = (unsigned char)in[i];
        if (c == '-' || c == '.') {
            c = ':';
        }
        if (c == ':') {
            if (o > 0 && out[o - 1] == ':') {
                continue;
            }
        } else if (!isxdigit(c)) {
            continue;
        } else {
            c = (unsigned char)toupper(c);
        }
        out[o++] = (char)c;
    }
    out[o] = '\0';
}

/* MAC 归一后是否等值（大小写与分隔符差异都算同一路设备） */
static int ext_mac_equal(const char *a, const char *b)
{
    char na[EXT_MAC_LEN] = {0};
    char nb[EXT_MAC_LEN] = {0};

    if (!a || !b) {
        return 0;
    }
    ext_norm_mac(a, na, sizeof(na));
    ext_norm_mac(b, nb, sizeof(nb));
    if (na[0] == '\0' || nb[0] == '\0') {
        return 0;
    }
    return strcmp(na, nb) == 0;
}

static client_node_t *ext_client_by_mac(const char *mac)
{
    client_node_t *node = NULL;

    if (!mac || mac[0] == '\0') {
        return NULL;
    }
    list_for_each_entry(node, &client_list, client) {
        if (ext_mac_equal(node->mac, mac)) {
            return node;
        }
    }
    return NULL;
}

/* ------------------------------------------------------- 仪表板引导状态 */

#define FWX_INIT_STATUS_FILE "/etc/fwx_init_status"

static int fwx_get_init_status(void)
{
    FILE *fp = fopen(FWX_INIT_STATUS_FILE, "r");
    char buf[16] = {0};
    int init_status = 1;

    if (!fp) {
        /* 文件缺失 = 从未记录过，按「已引导」处理，避免每次进仪表板都弹 */
        return 1;
    }
    if (fgets(buf, sizeof(buf), fp) && atoi(buf) == 0) {
        init_status = 0;
    }
    fclose(fp);
    return init_status;
}

static int fwx_set_init_status(int init_status)
{
    char buf[8] = {0};
    int len = 0;
    int fd = -1;

    if (init_status != 0 && init_status != 1) {
        return -1;
    }

    fd = open(FWX_INIT_STATUS_FILE, O_WRONLY | O_TRUNC | O_CREAT, 0644);
    if (fd < 0) {
        LOG_ERROR("open %s failed\n", FWX_INIT_STATUS_FILE);
        return -1;
    }
    len = snprintf(buf, sizeof(buf), "%d\n", init_status);
    if (len <= 0 || write(fd, buf, len) != len) {
        close(fd);
        LOG_ERROR("write %s failed\n", FWX_INIT_STATUS_FILE);
        return -1;
    }
    close(fd);
    return 0;
}

struct json_object *fwx_api_get_init_status(struct json_object *req_obj)
{
    struct json_object *data_obj = json_object_new_object();

    (void)req_obj;
    if (!data_obj) {
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }
    json_object_object_add(data_obj, "init_status",
                           json_object_new_int(fwx_get_init_status()));
    return fwx_gen_api_response_data(API_CODE_SUCCESS, data_obj);
}

struct json_object *fwx_api_set_init_status(struct json_object *req_obj)
{
    struct json_object *data_obj = json_object_new_object();
    struct json_object *status_obj = NULL;
    int init_status = 1;

    if (!data_obj) {
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }
    if (req_obj) {
        status_obj = json_object_object_get(req_obj, "init_status");
        if (status_obj) {
            init_status = json_object_get_int(status_obj);
        }
    }
    if (init_status != 0 && init_status != 1) {
        json_object_put(data_obj);
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }
    if (fwx_set_init_status(init_status) != 0) {
        json_object_put(data_obj);
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }

    json_object_object_add(data_obj, "init_status",
                           json_object_new_int(init_status));
    return fwx_gen_api_response_data(API_CODE_SUCCESS, data_obj);
}

/* ---------------------------------------------------------------- 记录白名单 */

#define EXT_RW_UCI_KEY "fwx_record.@whitelist[0].whitelist"
#define EXT_RW_BUF_LEN 8192
#define EXT_RW_MAX 256

static char g_rw_macs[EXT_RW_MAX][EXT_MAC_LEN];
static int g_rw_count = 0;
static time_t g_rw_load_ts = 0;

static void ext_rw_split(char *buf)
{
    char *p = NULL;
    char *save = NULL;

    g_rw_count = 0;
    for (p = strtok_r(buf, " \t", &save); p && g_rw_count < EXT_RW_MAX;
         p = strtok_r(NULL, " \t", &save)) {
        ext_norm_mac(p, g_rw_macs[g_rw_count], EXT_MAC_LEN);
        if (g_rw_macs[g_rw_count][0] != '\0') {
            g_rw_count++;
        }
    }
}

static void ext_rw_reload(void)
{
    struct uci_context *ctx = uci_alloc_context();
    char buf[EXT_RW_BUF_LEN] = {0};

    g_rw_count = 0;
    g_rw_load_ts = time(NULL);
    if (!ctx) {
        return;
    }
    if (fwx_uci_get_list_value(ctx, EXT_RW_UCI_KEY, buf, sizeof(buf), " ") == 0 &&
        buf[0] != '\0') {
        ext_rw_split(buf);
    }
    uci_free_context(ctx);
}

int fwx_ext_record_whitelisted(const char *mac)
{
    char norm[EXT_MAC_LEN] = {0};
    int i;

    if (!mac || mac[0] == '\0') {
        return 0;
    }
    /* 缓存 30 秒：增删接口即时失效；页面高频查询不必每次开 uci */
    if (g_rw_load_ts == 0 || difftime(time(NULL), g_rw_load_ts) > 30.0) {
        ext_rw_reload();
    }
    ext_norm_mac(mac, norm, sizeof(norm));
    if (norm[0] == '\0') {
        return 0;
    }
    for (i = 0; i < g_rw_count; i++) {
        if (strcmp(g_rw_macs[i], norm) == 0) {
            return 1;
        }
    }
    return 0;
}

void fwx_ext_whitelist_invalidate(void)
{
    g_rw_load_ts = 0;
}

/* 确保 fwx_record 里存在 whitelist section；老数据（@record[0].whitelist）迁移过去 */
static int ext_rw_ensure_section(struct uci_context *ctx)
{
    char legacy[EXT_RW_BUF_LEN] = {0};
    int num = 0;
    int legacy_num = 0;
    int has_legacy = 0;
    char *p = NULL;
    char *save = NULL;

    num = fwx_uci_get_list_num(ctx, "fwx_record", "whitelist");
    if (num > 0) {
        return 0;
    }

    legacy_num = fwx_uci_get_list_num(ctx, "fwx_record", "record");
    if (legacy_num > 0 &&
        fwx_uci_get_list_value(ctx, "fwx_record.@record[0].whitelist",
                               legacy, sizeof(legacy), " ") == 0 &&
        legacy[0] != '\0') {
        has_legacy = 1;
    }

    if (fwx_uci_add_section(ctx, "fwx_record", "whitelist") != UCI_OK) {
        LOG_ERROR("record whitelist: add section failed\n");
        return -1;
    }
    if (has_legacy) {
        for (p = strtok_r(legacy, " \t", &save); p; p = strtok_r(NULL, " \t", &save)) {
            char norm[EXT_MAC_LEN] = {0};
            ext_norm_mac(p, norm, sizeof(norm));
            if (norm[0] != '\0') {
                fwx_uci_add_list(ctx, EXT_RW_UCI_KEY, norm);
            }
        }
    }
    fwx_uci_commit(ctx, "fwx_record");
    return 0;
}

/*
 * get_record_whitelist —— luci-app-fwx-record-whitelist index.htm / controller：
 *   data: { total_num, total_page, page, page_size,
 *           list: [{mac, nickname, hostname}] }
 */
struct json_object *fwx_api_get_record_whitelist(struct json_object *req_obj)
{
    struct json_object *data_obj = NULL;
    struct json_object *list_obj = NULL;
    struct json_object *page_obj = NULL;
    struct json_object *page_size_obj = NULL;
    struct uci_context *ctx = NULL;
    char buf[EXT_RW_BUF_LEN] = {0};
    char *macs[EXT_RW_MAX];
    int mac_count = 0;
    char *p = NULL;
    char *save = NULL;
    int total_num = 0;
    int total_page = 1;
    int page = 1;
    int page_size = 15;
    int start_idx = 0;
    int end_idx = 0;
    int i;

    if (req_obj) {
        page_obj = json_object_object_get(req_obj, "page");
        page_size_obj = json_object_object_get(req_obj, "page_size");
        if (page_obj) {
            page = json_object_get_int(page_obj);
            if (page < 1) {
                page = 1;
            }
        }
        if (page_size_obj) {
            page_size = json_object_get_int(page_size_obj);
            if (page_size < 1) {
                page_size = 15;
            }
            if (page_size > 200) {
                page_size = 200;
            }
        }
    }

    data_obj = json_object_new_object();
    list_obj = json_object_new_array();
    if (!data_obj || !list_obj) {
        if (data_obj) {
            json_object_put(data_obj);
        }
        if (list_obj) {
            json_object_put(list_obj);
        }
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }

    ctx = uci_alloc_context();
    if (!ctx) {
        json_object_put(data_obj);
        json_object_put(list_obj);
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }
    ext_rw_ensure_section(ctx);
    if (fwx_uci_get_list_value(ctx, EXT_RW_UCI_KEY, buf, sizeof(buf), " ") == 0 &&
        buf[0] != '\0') {
        char norm[EXT_MAC_LEN] = {0};
        for (p = strtok_r(buf, " \t", &save);
             p && mac_count < EXT_RW_MAX; p = strtok_r(NULL, " \t", &save)) {
            int dup = 0;
            int j;
            ext_norm_mac(p, norm, sizeof(norm));
            if (norm[0] == '\0') {
                continue;
            }
            for (j = 0; j < mac_count; j++) {
                if (strcmp(macs[j], norm) == 0) {
                    dup = 1;
                    break;
                }
            }
            if (!dup) {
                macs[mac_count] = strdup(norm);
                if (macs[mac_count]) {
                    mac_count++;
                }
            }
        }
    }
    uci_free_context(ctx);

    total_num = mac_count;
    total_page = (total_num + page_size - 1) / page_size;
    if (total_page < 1) {
        total_page = 1;
    }
    if (page > total_page) {
        page = total_page;
    }
    start_idx = (page - 1) * page_size;
    end_idx = start_idx + page_size;
    if (end_idx > total_num) {
        end_idx = total_num;
    }

    for (i = start_idx; i < end_idx; i++) {
        struct json_object *item_obj = json_object_new_object();
        client_node_t *dev = ext_client_by_mac(macs[i]);

        if (!item_obj) {
            continue;
        }
        json_object_object_add(item_obj, "mac", json_object_new_string(macs[i]));
        if (dev) {
            json_object_object_add(item_obj, "nickname",
                                   json_object_new_string(dev->nickname ? dev->nickname : ""));
            json_object_object_add(item_obj, "hostname",
                                   json_object_new_string(dev->hostname ? dev->hostname : ""));
        } else {
            json_object_object_add(item_obj, "nickname", json_object_new_string(""));
            json_object_object_add(item_obj, "hostname", json_object_new_string(""));
        }
        json_object_array_add(list_obj, item_obj);
    }

    for (i = 0; i < mac_count; i++) {
        free(macs[i]);
    }

    json_object_object_add(data_obj, "total_num", json_object_new_int(total_num));
    json_object_object_add(data_obj, "total_page", json_object_new_int(total_page));
    json_object_object_add(data_obj, "page", json_object_new_int(page));
    json_object_object_add(data_obj, "page_size", json_object_new_int(page_size));
    json_object_object_add(data_obj, "list", list_obj);
    return fwx_gen_api_response_data(API_CODE_SUCCESS, data_obj);
}

/*
 * add_record_whitelist —— controller 传 data: {mac_list: [...]}，只认 code==2000
 */
struct json_object *fwx_api_add_record_whitelist(struct json_object *req_obj)
{
    struct json_object *mac_list_obj = NULL;
    struct uci_context *ctx = NULL;
    char buf[EXT_RW_BUF_LEN] = {0};
    int i;
    int len;
    int added = 0;

    if (!req_obj ||
        !json_object_object_get_ex(req_obj, "mac_list", &mac_list_obj) ||
        !json_object_is_type(mac_list_obj, json_type_array)) {
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }

    ctx = uci_alloc_context();
    if (!ctx) {
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }
    ext_rw_ensure_section(ctx);
    fwx_uci_get_list_value(ctx, EXT_RW_UCI_KEY, buf, sizeof(buf), " ");

    len = json_object_array_length(mac_list_obj);
    for (i = 0; i < len; i++) {
        struct json_object *item = json_object_array_get_idx(mac_list_obj, i);
        const char *raw = NULL;
        char norm[EXT_MAC_LEN] = {0};

        if (!item) {
            continue;
        }
        raw = json_object_get_string(item);
        if (!raw || raw[0] == '\0') {
            continue;
        }
        ext_norm_mac(raw, norm, sizeof(norm));
        if (norm[0] == '\0' || strlen(norm) < 17) {
            LOG_ERROR("record whitelist: invalid mac '%s'\n", raw);
            continue;
        }
        if (strstr(buf, norm) != NULL) {
            continue; /* 已存在 */
        }
        fwx_uci_add_list(ctx, EXT_RW_UCI_KEY, norm);
        added++;
    }

    if (added > 0) {
        fwx_uci_commit(ctx, "fwx_record");
    }
    uci_free_context(ctx);
    fwx_ext_whitelist_invalidate();
    LOG_INFO("record whitelist: added %d mac(s)\n", added);
    return fwx_gen_api_response_data(API_CODE_SUCCESS, NULL);
}

/*
 * del_record_whitelist —— controller 传 data: {mac: "..."}，只认 code==2000。
 * 不存在的 mac 也返回成功（幂等），与界面的「删完刷新」行为一致。
 */
struct json_object *fwx_api_del_record_whitelist(struct json_object *req_obj)
{
    struct json_object *mac_obj = NULL;
    struct uci_context *ctx = NULL;
    const char *raw = NULL;
    char norm[EXT_MAC_LEN] = {0};

    if (!req_obj ||
        !json_object_object_get_ex(req_obj, "mac", &mac_obj)) {
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }
    raw = json_object_get_string(mac_obj);
    ext_norm_mac(raw ? raw : "", norm, sizeof(norm));
    if (norm[0] == '\0') {
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }

    ctx = uci_alloc_context();
    if (!ctx) {
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }
    ext_rw_ensure_section(ctx);
    fwx_uci_del_list(ctx, EXT_RW_UCI_KEY, norm);
    fwx_uci_commit(ctx, "fwx_record");
    uci_free_context(ctx);
    fwx_ext_whitelist_invalidate();
    LOG_INFO("record whitelist: deleted %s\n", norm);
    return fwx_gen_api_response_data(API_CODE_SUCCESS, NULL);
}

/* ---------------------------------------------------------------- 历史流量 */

static long long ext_sum_global_file(const char *path, long long *up, long long *down)
{
    struct json_object *root = NULL;
    struct json_object *hourly = NULL;
    int i;
    int len;

    root = json_object_from_file(path);
    if (!root) {
        return 0;
    }
    if (json_object_object_get_ex(root, "hourly", &hourly) &&
        json_object_is_type(hourly, json_type_array)) {
        len = json_object_array_length(hourly);
        for (i = 0; i < len; i++) {
            struct json_object *hour_obj = json_object_array_get_idx(hourly, i);
            struct json_object *traffic = NULL;
            struct json_object *v = NULL;

            if (!hour_obj ||
                !json_object_object_get_ex(hour_obj, "traffic", &traffic)) {
                continue;
            }
            if (up && json_object_object_get_ex(traffic, "up_bytes", &v)) {
                *up += (long long)json_object_get_int64(v);
            }
            if (down && json_object_object_get_ex(traffic, "down_bytes", &v)) {
                *down += (long long)json_object_get_int64(v);
            }
        }
    }
    json_object_put(root);
    return 0;
}

static long long ext_sum_hourly_file(const char *path, int which)
{
    /* which: 0=up, 1=down, 2=online_time, 3=active_time */
    struct json_object *root = NULL;
    struct json_object *hourly = NULL;
    long long total = 0;
    int i;
    int len;

    root = json_object_from_file(path);
    if (!root) {
        return 0;
    }
    if (json_object_object_get_ex(root, "hourly_stats", &hourly) &&
        json_object_is_type(hourly, json_type_array)) {
        len = json_object_array_length(hourly);
        for (i = 0; i < len; i++) {
            struct json_object *hour_obj = json_object_array_get_idx(hourly, i);
            struct json_object *v = NULL;
            struct json_object *traffic = NULL;

            if (!hour_obj) {
                continue;
            }
            if (which <= 1) {
                if (json_object_object_get_ex(hour_obj, "traffic", &traffic)) {
                    const char *key = (which == 0) ? "up_bytes" : "down_bytes";
                    if (json_object_object_get_ex(traffic, key, &v)) {
                        total += (long long)json_object_get_int64(v);
                    }
                }
            } else {
                const char *key = (which == 2) ? "online_time" : "active_time";
                if (json_object_object_get_ex(hour_obj, key, &v)) {
                    total += (long long)json_object_get_int64(v);
                }
            }
        }
    }
    json_object_put(root);
    return total;
}

/*
 * get_history_traffic_stats —— traffic_stat.htm renderHistory：
 *   data.list[].{date_str, up_bytes, down_bytes}，其余字段按 25.12 结构补全。
 * 历史读 fwx_user.c 落盘的 global/stats/traffic_<date>.json；今天用
 * get_global_traffic_stats() 的实时逐小时数组。
 */
struct json_object *fwx_api_get_history_traffic_stats(struct json_object *req_obj)
{
    struct json_object *data_obj = NULL;
    struct json_object *list_array = NULL;
    struct json_object *days_obj = NULL;
    long long total_up = 0;
    long long total_down = 0;
    unsigned int today = ext_today_start();
    int days = 30;
    int i;

    if (req_obj && json_object_object_get_ex(req_obj, "days", &days_obj) && days_obj) {
        days = json_object_get_int(days_obj);
    }
    if (days <= 0) {
        days = 1;
    }
    if (days > EXT_DAYS_MAX) {
        days = EXT_DAYS_MAX;
    }

    data_obj = json_object_new_object();
    list_array = json_object_new_array();
    if (!data_obj || !list_array) {
        if (data_obj) {
            json_object_put(data_obj);
        }
        if (list_array) {
            json_object_put(list_array);
        }
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }

    for (i = days - 1; i >= 0; i--) {
        unsigned int date = today - (unsigned int)(i * EXT_SECONDS_PER_DAY);
        char date_str[32] = {0};
        char path[EXT_PATH_LEN] = {0};
        long long day_up = 0;
        long long day_down = 0;
        struct json_object *day_obj = json_object_new_object();

        ext_date_string(date, date_str, sizeof(date_str));
        if (date == today) {
            traffic_stat_t traffic_array[HOURS_PER_DAY];
            int hour;

            memset(traffic_array, 0, sizeof(traffic_array));
            get_global_traffic_stats(traffic_array);
            for (hour = 0; hour < HOURS_PER_DAY; hour++) {
                day_up += (long long)traffic_array[hour].up_bytes;
                day_down += (long long)traffic_array[hour].down_bytes;
            }
        } else {
            snprintf(path, sizeof(path), "%s/global/stats/traffic_%s.json",
                     get_client_data_base_dir(), date_str);
            ext_sum_global_file(path, &day_up, &day_down);
        }

        total_up += day_up;
        total_down += day_down;

        if (!day_obj) {
            continue;
        }
        json_object_object_add(day_obj, "date", json_object_new_int64((int64_t)date));
        json_object_object_add(day_obj, "date_str", json_object_new_string(date_str));
        json_object_object_add(day_obj, "up_bytes", json_object_new_int64(day_up));
        json_object_object_add(day_obj, "down_bytes", json_object_new_int64(day_down));
        json_object_object_add(day_obj, "total_bytes",
                               json_object_new_int64(day_up + day_down));
        json_object_object_add(day_obj, "is_today",
                               json_object_new_int(date == today ? 1 : 0));
        json_object_array_add(list_array, day_obj);
    }

    json_object_object_add(data_obj, "days", json_object_new_int(days));
    json_object_object_add(data_obj, "start_date",
                           json_object_new_int64((int64_t)(today - (unsigned int)((days - 1) * EXT_SECONDS_PER_DAY))));
    json_object_object_add(data_obj, "end_date", json_object_new_int64((int64_t)today));
    json_object_object_add(data_obj, "total_up_bytes", json_object_new_int64(total_up));
    json_object_object_add(data_obj, "total_down_bytes", json_object_new_int64(total_down));
    json_object_object_add(data_obj, "total_bytes",
                           json_object_new_int64(total_up + total_down));
    json_object_object_add(data_obj, "list", list_array);
    /* traffic_stat.htm 的“今日流量统计”读 data.hourly_traffic：
     * [{hour, traffic:{up_bytes, down_bytes}}]。24.10 的 fwxd 本来就没有这个
     * 字段，界面这块一直空白，这里从内核实时统计（get_global_traffic_stats）补齐。 */
    {
        struct json_object *hourly = json_object_new_array();

        if (hourly) {
            traffic_stat_t hour_array[HOURS_PER_DAY];
            int hour;

            memset(hour_array, 0, sizeof(hour_array));
            get_global_traffic_stats(hour_array);
            for (hour = 0; hour < HOURS_PER_DAY; hour++) {
                struct json_object *h_obj = json_object_new_object();
                struct json_object *t_obj = json_object_new_object();

                if (!h_obj || !t_obj) {
                    if (h_obj) {
                        json_object_put(h_obj);
                    }
                    if (t_obj) {
                        json_object_put(t_obj);
                    }
                    continue;
                }
                json_object_object_add(t_obj, "up_bytes",
                                       json_object_new_int64(
                                           (long long)hour_array[hour].up_bytes));
                json_object_object_add(t_obj, "down_bytes",
                                       json_object_new_int64(
                                           (long long)hour_array[hour].down_bytes));
                json_object_object_add(t_obj, "total_bytes",
                                       json_object_new_int64(
                                           (long long)hour_array[hour].up_bytes +
                                           (long long)hour_array[hour].down_bytes));
                json_object_object_add(h_obj, "hour", json_object_new_int(hour));
                json_object_object_add(h_obj, "traffic", t_obj);
                json_object_array_add(hourly, h_obj);
            }
            json_object_object_add(data_obj, "hourly_traffic", hourly);
        }
    }
    return fwx_gen_api_response_data(API_CODE_SUCCESS, data_obj);
}

/* ---------------------------------------------------------------- 每日汇总 */

/* feature.cfg 的 #class 分段 → appid → 分类 key（game/video/chat/...）。
 * fanchmwrt 的 feature.cfg 用「#class <key> <id> <中文名>」做分段标记，
 * 其后所有应用都属于该分类，直到下一个 #class。 */
#define EXT_CLASS_TABLE_MAX 1024
static struct {
    int appid;
    int class_id;      /* #class 行的分类编号（1 game / 2 video ...），parental detail 的 category_stats 要用 */
    char key[24];
} g_class_map[EXT_CLASS_TABLE_MAX];
static int g_class_map_count = 0;
static time_t g_class_map_ts = 0;

static void ext_class_map_load(void)
{
    FILE *fp = fopen("/tmp/feature.cfg", "r");
    char line[2048] = {0};
    char cur_key[24] = {0};
    int cur_class_id = 0;

    g_class_map_count = 0;
    g_class_map_ts = time(NULL);
    if (!fp) {
        return;
    }
    while (fgets(line, sizeof(line), fp)) {
        if (strncmp(line, "#class", 6) == 0) {
            char key[24] = {0};
            int class_id = 0;
            /* 格式：#class <key> <id> <中文名>（gen_class.sh 按 $3/$2/$4 生成 app_class.txt） */
            if (sscanf(line + 6, "%23s %d", key, &class_id) >= 1) {
                snprintf(cur_key, sizeof(cur_key), "%s", key);
                cur_class_id = class_id;
            }
            continue;
        }
        if (line[0] >= '0' && line[0] <= '9' && cur_key[0] != '\0' &&
            g_class_map_count < EXT_CLASS_TABLE_MAX) {
            int appid = atoi(line);
            if (appid > 0) {
                g_class_map[g_class_map_count].appid = appid;
                g_class_map[g_class_map_count].class_id = cur_class_id;
                snprintf(g_class_map[g_class_map_count].key,
                         sizeof(g_class_map[g_class_map_count].key), "%s", cur_key);
                g_class_map_count++;
            }
        }
    }
    fclose(fp);
}

static const char *ext_class_of_appid(int appid)
{
    int i;

    if (g_class_map_ts == 0 || difftime(time(NULL), g_class_map_ts) > 300.0) {
        ext_class_map_load();
    }
    for (i = 0; i < g_class_map_count; i++) {
        if (g_class_map[i].appid == appid) {
            return g_class_map[i].key;
        }
    }
    return NULL;
}

/* appid → 分类编号（未收录返回 0），parental detail 的 category_stats 用 */
static int ext_class_id_of_appid(int appid)
{
    int i;

    if (g_class_map_ts == 0 || difftime(time(NULL), g_class_map_ts) > 300.0) {
        ext_class_map_load();
    }
    for (i = 0; i < g_class_map_count; i++) {
        if (g_class_map[i].appid == appid) {
            return g_class_map[i].class_id;
        }
    }
    return 0;
}

static const char *EXT_CATEGORY_KEYS[5] = {"game", "video", "chat", "shopping", "music"};

/* 聚合容器：appid → 秒数 */
struct ext_app_time {
    int appid;
    long long seconds;
};

static void ext_app_time_add(struct ext_app_time *map, int *count, int max,
                             int appid, long long seconds)
{
    int i;

    if (appid <= 0 || seconds <= 0) {
        return;
    }
    for (i = 0; i < *count; i++) {
        if (map[i].appid == appid) {
            map[i].seconds += seconds;
            return;
        }
    }
    if (*count < max) {
        map[*count].appid = appid;
        map[*count].seconds = seconds;
        (*count)++;
    }
}

static int ext_app_time_cmp(const void *a, const void *b)
{
    const struct ext_app_time *x = a;
    const struct ext_app_time *y = b;

    if (y->seconds > x->seconds) {
        return 1;
    }
    if (y->seconds < x->seconds) {
        return -1;
    }
    return 0;
}

/* 从 top_apps_<date>.json 收集 per-app 秒数 */
static void ext_collect_top_apps_file(const char *path,
                                      struct ext_app_time *map, int *count, int max)
{
    struct json_object *root = NULL;
    struct json_object *apps = NULL;
    int i;
    int len;

    root = json_object_from_file(path);
    if (!root) {
        return;
    }
    if (json_object_object_get_ex(root, "apps", &apps) &&
        json_object_is_type(apps, json_type_array)) {
        len = json_object_array_length(apps);
        for (i = 0; i < len; i++) {
            struct json_object *app_obj = json_object_array_get_idx(apps, i);
            struct json_object *id_obj = NULL;
            struct json_object *t_obj = NULL;

            if (!app_obj ||
                !json_object_object_get_ex(app_obj, "appid", &id_obj) ||
                !json_object_object_get_ex(app_obj, "total_time", &t_obj)) {
                continue;
            }
            ext_app_time_add(map, count, max,
                             json_object_get_int(id_obj),
                             (long long)json_object_get_int64(t_obj));
        }
    }
    json_object_put(root);
}

/*
 * get_user_daily_summary —— user_history.htm：
 *   data.list 必须恰好 30 项，每项 {up_bytes, down_bytes, active_seconds,
 *   online_seconds, category_seconds:{game,video,chat,shopping,music},
 *   top_apps:[{appid,name,seconds}]}。
 * 历史天读 hourly_<date>.json + top_apps_<date>.json；今天用
 * user_record_list（已结束会话）+ online_session（进行中）+ client->visit
 * （逐 app 时长）实时聚合。
 */
struct json_object *fwx_api_get_user_daily_summary(struct json_object *req_obj)
{
    struct json_object *mac_obj = NULL;
    struct json_object *data_obj = NULL;
    struct json_object *list_array = NULL;
    const char *mac = NULL;
    char norm[EXT_MAC_LEN] = {0};
    unsigned int today = ext_today_start();
    int day;

    if (!req_obj || !json_object_object_get_ex(req_obj, "mac", &mac_obj) || !mac_obj) {
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }
    mac = json_object_get_string(mac_obj);
    ext_norm_mac(mac ? mac : "", norm, sizeof(norm));
    if (norm[0] == '\0') {
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }

    data_obj = json_object_new_object();
    list_array = json_object_new_array();
    if (!data_obj || !list_array) {
        if (data_obj) {
            json_object_put(data_obj);
        }
        if (list_array) {
            json_object_put(list_array);
        }
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }

    for (day = EXT_SUMMARY_DAYS - 1; day >= 0; day--) {
        unsigned int date = today - (unsigned int)(day * EXT_SECONDS_PER_DAY);
        char date_str[32] = {0};
        char mac_dir[96] = {0};
        char path[EXT_PATH_LEN] = {0};
        long long up = 0;
        long long down = 0;
        long long online = 0;
        long long active = 0;
        struct ext_app_time app_map[64];
        int app_count = 0;
        struct json_object *day_obj = NULL;
        struct json_object *cat_obj = NULL;
        struct json_object *apps_array = NULL;
        int k;

        ext_date_string(date, date_str, sizeof(date_str));
        ext_mac_dirname(norm, mac_dir, sizeof(mac_dir));

        if (date == today) {
            user_record_t *record = NULL;
            client_node_t *client = ext_client_by_mac(norm);
            visit_info_t *p_info = NULL;

            list_for_each_entry(record, &user_record_list, list) {
                time_t rt = (time_t)record->timestamp;
                struct tm tm_rec;
                struct tm *tr = localtime_r(&rt, &tm_rec);
                unsigned int rec_date = 0;

                if (!tr) {
                    continue;
                }
                tr->tm_hour = 0;
                tr->tm_min = 0;
                tr->tm_sec = 0;
                rec_date = (unsigned int)mktime(tr);

                if (!ext_mac_equal(record->mac, norm) || rec_date != date) {
                    continue;
                }
                up += (long long)record->up_bytes;
                down += (long long)record->down_bytes;
                online += (long long)record->online_duration;
                active += (long long)record->active_duration;
            }
            if (client && client->online == 1) {
                up += (long long)client->online_session.up_bytes;
                down += (long long)client->online_session.down_bytes;
                online += (long long)client->online_session.online_duration;
                active += (long long)client->online_session.active_duration;
            }
            if (client) {
                list_for_each_entry(p_info, &client->visit, visit) {
                    long long seconds = (long long)(p_info->latest_time - p_info->first_time);
                    if (p_info->first_time >= date &&
                        seconds > 0 && seconds < EXT_SECONDS_PER_DAY) {
                        ext_app_time_add(app_map, &app_count, 64, p_info->appid, seconds);
                    }
                }
            }
        } else {
            snprintf(path, sizeof(path), "%s/%s/stats/hourly_%s.json",
                     get_client_data_base_dir(), mac_dir, date_str);
            up = ext_sum_hourly_file(path, 0);
            down = ext_sum_hourly_file(path, 1);
            online = ext_sum_hourly_file(path, 2);
            active = ext_sum_hourly_file(path, 3);

            snprintf(path, sizeof(path), "%s/%s/stats/top_apps_%s.json",
                     get_client_data_base_dir(), mac_dir, date_str);
            ext_collect_top_apps_file(path, app_map, &app_count, 64);
        }

        day_obj = json_object_new_object();
        cat_obj = json_object_new_object();
        apps_array = json_object_new_array();
        if (!day_obj || !cat_obj || !apps_array) {
            if (day_obj) {
                json_object_put(day_obj);
            }
            if (cat_obj) {
                json_object_put(cat_obj);
            }
            if (apps_array) {
                json_object_put(apps_array);
            }
            continue;
        }

        for (k = 0; k < 5; k++) {
            json_object_object_add(cat_obj, EXT_CATEGORY_KEYS[k], json_object_new_int(0));
        }
        if (app_count > 1) {
            qsort(app_map, app_count, sizeof(app_map[0]), ext_app_time_cmp);
        }
        for (k = 0; k < app_count && k < EXT_TOP_APPS_NUM; k++) {
            const char *class_key = ext_class_of_appid(app_map[k].appid);
            struct json_object *app_obj = json_object_new_object();

            if (class_key) {
                struct json_object *exist = NULL;
                long long prev = 0;

                if (json_object_object_get_ex(cat_obj, class_key, &exist)) {
                    prev = (long long)json_object_get_int64(exist);
                }
                json_object_object_add(cat_obj, class_key,
                                       json_object_new_int64(prev + app_map[k].seconds));
            }
            if (app_obj) {
                json_object_object_add(app_obj, "appid",
                                       json_object_new_int(app_map[k].appid));
                json_object_object_add(app_obj, "name",
                                       json_object_new_string(get_app_name_by_id(app_map[k].appid)));
                json_object_object_add(app_obj, "seconds",
                                       json_object_new_int64(app_map[k].seconds));
                json_object_array_add(apps_array, app_obj);
            }
        }

        /* user_history.htm 的横轴是 data: list.map(day => day.date) 且
         * axisLabel 里做 date.slice(5) —— date 必须是 "YYYY-MM-DD" 字符串，
         * 传时间戳会让 slice 抛异常、整张图渲染失败。时间戳另给 date_ts。 */
        json_object_object_add(day_obj, "date", json_object_new_string(date_str));
        json_object_object_add(day_obj, "date_str", json_object_new_string(date_str));
        json_object_object_add(day_obj, "date_ts", json_object_new_int64((int64_t)date));
        json_object_object_add(day_obj, "available",
                               json_object_new_int((up + down + active + online) > 0 ? 1 : 0));
        json_object_object_add(day_obj, "up_bytes", json_object_new_int64(up));
        json_object_object_add(day_obj, "down_bytes", json_object_new_int64(down));
        json_object_object_add(day_obj, "active_seconds", json_object_new_int64(active));
        json_object_object_add(day_obj, "online_seconds", json_object_new_int64(online));
        json_object_object_add(day_obj, "category_seconds", cat_obj);
        json_object_object_add(day_obj, "top_apps", apps_array);
        json_object_array_add(list_array, day_obj);
    }

    json_object_object_add(data_obj, "mac", json_object_new_string(norm));
    json_object_object_add(data_obj, "days", json_object_new_int(EXT_SUMMARY_DAYS));
    json_object_object_add(data_obj, "list", list_array);
    return fwx_gen_api_response_data(API_CODE_SUCCESS, data_obj);
}

/* ---------------------------------------------------------------- 家长控制规则 */

static int ext_uci_mac_in_array_list(const char *package, const char *section,
                                     const char *target_norm)
{
    struct uci_context *ctx = NULL;
    char pkg_buf[64] = {0};
    char sec_buf[64] = {0};
    int num = 0;
    int found = 0;
    int i;

    ctx = uci_alloc_context();
    if (!ctx) {
        return 0;
    }
    /* fwx_uci_get_list_num 的参数是 char *（非 const），拷一份消掉
     * -Wdiscarded-qualifiers 警告 —— 只是签名不严谨，不改语义 */
    snprintf(pkg_buf, sizeof(pkg_buf), "%s", package);
    snprintf(sec_buf, sizeof(sec_buf), "%s", section);
    num = fwx_uci_get_list_num(ctx, pkg_buf, sec_buf);
    for (i = 0; i < num && !found; i++) {
        char mac[EXT_MAC_LEN] = {0};
        char path[128] = {0};

        snprintf(path, sizeof(path), "%s.@%s[%d].mac", package, section, i);
        if (fwx_uci_get_value(ctx, path, mac, sizeof(mac)) == 0 && mac[0] != '\0') {
            char norm[EXT_MAC_LEN] = {0};
            ext_norm_mac(mac, norm, sizeof(norm));
            if (norm[0] != '\0' && strcmp(norm, target_norm) == 0) {
                found = 1;
            }
        }
    }
    uci_free_context(ctx);
    return found;
}

/*
 * get_user_parental_control_rules —— user_detail.htm loadBehaviorRules：
 *   data: {mac, af_whitelist, mf_whitelist,
 *          list: [{module, rule_id, rule_name, condition_type, condition, today_status}]}
 * 24.10 的管控规则只有 macfilter 的时段规则（time 型）；appfilter 是按应用类别的
 * 全局规则，不属于单一终端的专属管控，不塞进 list，只反映白名单标志。
 */
struct json_object *fwx_api_get_user_parental_control_rules(struct json_object *req_obj)
{
    struct json_object *mac_obj = NULL;
    struct json_object *data_obj = NULL;
    struct json_object *list_obj = NULL;
    const char *mac = NULL;
    char norm[EXT_MAC_LEN] = {0};
    int af_whitelist = 0;
    int mf_whitelist = 0;
    int app_matched = 0;
    const char *status_key = "unlimited";

    if (!req_obj || !json_object_object_get_ex(req_obj, "mac", &mac_obj) || !mac_obj) {
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }
    mac = json_object_get_string(mac_obj);
    ext_norm_mac(mac ? mac : "", norm, sizeof(norm));
    if (norm[0] == '\0') {
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }

    data_obj = json_object_new_object();
    list_obj = json_object_new_array();
    if (!data_obj || !list_obj) {
        if (data_obj) {
            json_object_put(data_obj);
        }
        if (list_obj) {
            json_object_put(list_obj);
        }
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }

    json_object_object_add(data_obj, "mac", json_object_new_string(norm));
    af_whitelist = ext_uci_mac_in_array_list("appfilter_whitelist", "whitelist_mac", norm);
    mf_whitelist = ext_uci_mac_in_array_list("macfilter_whitelist", "whitelist_mac", norm);
    json_object_object_add(data_obj, "af_whitelist", json_object_new_int(af_whitelist ? 1 : 0));
    json_object_object_add(data_obj, "mf_whitelist", json_object_new_int(mf_whitelist ? 1 : 0));

    ext_collect_macfilter_rules(list_obj, norm, &app_matched);

    if (!mf_whitelist && ext_mbl_hit(norm)) {
        status_key = "mac_blocked";
    } else if (app_matched && !af_whitelist) {
        status_key = "app_limited";
    }
    json_object_object_add(data_obj, "pc_status", json_object_new_string(status_key));
    json_object_object_add(data_obj, "pc_status_key", json_object_new_string(status_key));

    json_object_object_add(data_obj, "list", list_obj);
    return fwx_gen_api_response_data(API_CODE_SUCCESS, data_obj);
}
/* ------------------------------------------------------- 设备黑名单（mac-blacklist 页） */

/*
 * luci-app-fwx-mac-blacklist 的三个接口（get/add/del_mac_blacklist）。
 * 25.12 的实现就是纯 uci：mac_blacklist.base.mac_list（named section，
 * 由包自带的 mac_blacklist.config 提供），保存后 touch
 * /tmp/macfilter_rules_state 触发规则重载 —— 无内核改动。
 * 生效：rule_manager.lua 每周期把黑名单作为内核规则 102 下发（命中断网）。
 */
#define EXT_MBL_UCI_KEY "mac_blacklist.base.mac_list"
#define EXT_MBL_STATE "/tmp/macfilter_rules_state"
#ifndef EXT_MBL_MAX
#define EXT_MBL_MAX 128
#endif

static int ext_mbl_load(char macs[][EXT_MAC_LEN], int max)
{
    struct uci_context *ctx = uci_alloc_context();
    char buf[8192] = {0};
    int count = 0;
    char *p = NULL;
    char *save = NULL;

    if (!ctx) {
        return 0;
    }
    if (fwx_uci_get_list_value(ctx, EXT_MBL_UCI_KEY, buf, sizeof(buf), " ") == 0 &&
        buf[0] != '\0') {
        for (p = strtok_r(buf, " \t", &save); p && count < max;
             p = strtok_r(NULL, " \t", &save)) {
            ext_norm_mac(p, macs[count], EXT_MAC_LEN);
            if (macs[count][0] != '\0') {
                count++;
            }
        }
    }
    uci_free_context(ctx);
    return count;
}

static int ext_mbl_save(char macs[][EXT_MAC_LEN], int count)
{
    struct uci_context *ctx = uci_alloc_context();
    int i;
    int ret = 0;

    if (!ctx) {
        return -1;
    }
    fwx_uci_delete(ctx, EXT_MBL_UCI_KEY);
    for (i = 0; i < count; i++) {
        if (macs[i][0] != '\0') {
            fwx_uci_add_list(ctx, EXT_MBL_UCI_KEY, macs[i]);
        }
    }
    if (fwx_uci_commit(ctx, "mac_blacklist") != UCI_OK) {
        ret = -1;
    }
    uci_free_context(ctx);
    return ret;
}

static void ext_mbl_touch(void)
{
    FILE *fp = fopen(EXT_MBL_STATE, "a");
    if (fp) {
        fclose(fp);
    }
}

static int ext_mbl_hit(const char *mac)
{
    char macs[EXT_MBL_MAX][EXT_MAC_LEN] = {{0}};
    int count = ext_mbl_load(macs, EXT_MBL_MAX);
    int i;

    for (i = 0; i < count; i++) {
        if (strcmp(macs[i], mac) == 0) {
            return 1;
        }
    }
    return 0;
}

static void ext_mbl_append_item(struct json_object *arr, const char *mac)
{
    struct json_object *item_obj = json_object_new_object();
    client_node_t *dev = ext_client_by_mac(mac);

    if (!item_obj) {
        return;
    }
    json_object_object_add(item_obj, "mac", json_object_new_string(mac));
    if (dev) {
        json_object_object_add(item_obj, "hostname",
                               json_object_new_string(dev->hostname ? dev->hostname : "--"));
        json_object_object_add(item_obj, "nickname",
                               json_object_new_string(dev->nickname ? dev->nickname : "--"));
    } else {
        json_object_object_add(item_obj, "hostname", json_object_new_string("--"));
        json_object_object_add(item_obj, "nickname", json_object_new_string("--"));
    }
    json_object_array_add(arr, item_obj);
}

struct json_object *fwx_api_get_mac_blacklist(struct json_object *req_obj)
{
    char macs[EXT_MBL_MAX][EXT_MAC_LEN] = {{0}};
    int count = ext_mbl_load(macs, EXT_MBL_MAX);
    struct json_object *data_obj = json_object_new_object();
    struct json_object *list_obj = json_object_new_array();
    int i;

    (void)req_obj;
    if (!data_obj || !list_obj) {
        if (data_obj) {
            json_object_put(data_obj);
        }
        if (list_obj) {
            json_object_put(list_obj);
        }
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }
    for (i = 0; i < count; i++) {
        ext_mbl_append_item(list_obj, macs[i]);
    }
    json_object_object_add(data_obj, "list", list_obj);
    return fwx_gen_api_response_data(API_CODE_SUCCESS, data_obj);
}

struct json_object *fwx_api_add_mac_blacklist(struct json_object *req_obj)
{
    char macs[EXT_MBL_MAX][EXT_MAC_LEN] = {{0}};
    int count = ext_mbl_load(macs, EXT_MBL_MAX);
    struct json_object *mac_obj = NULL;
    struct json_object *mac_list_obj = NULL;
    int valid_count = 0;
    int i;

    if (!req_obj) {
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }
    mac_obj = json_object_object_get(req_obj, "mac");
    if (mac_obj) {
        const char *raw = json_object_get_string(mac_obj);
        char norm[EXT_MAC_LEN] = {0};
        ext_norm_mac(raw ? raw : "", norm, sizeof(norm));
        if (norm[0] != '\0') {
            int dup = 0;
            int j;
            for (j = 0; j < count; j++) {
                if (strcmp(macs[j], norm) == 0) {
                    dup = 1;
                    break;
                }
            }
            if (!dup && count < EXT_MBL_MAX) {
                snprintf(macs[count], EXT_MAC_LEN, "%s", norm);
                count++;
            }
            valid_count++;
        }
    }
    if (json_object_object_get_ex(req_obj, "mac_list", &mac_list_obj) &&
        json_object_is_type(mac_list_obj, json_type_array)) {
        int len = json_object_array_length(mac_list_obj);
        for (i = 0; i < len; i++) {
            struct json_object *item = json_object_array_get_idx(mac_list_obj, i);
            const char *raw = item ? json_object_get_string(item) : NULL;
            char norm[EXT_MAC_LEN] = {0};
            int dup = 0;
            int j;

            if (!raw || raw[0] == '\0') {
                continue;
            }
            ext_norm_mac(raw, norm, sizeof(norm));
            if (norm[0] == '\0') {
                continue;
            }
            valid_count++;
            for (j = 0; j < count; j++) {
                if (strcmp(macs[j], norm) == 0) {
                    dup = 1;
                    break;
                }
            }
            if (!dup && count < EXT_MBL_MAX) {
                snprintf(macs[count], EXT_MAC_LEN, "%s", norm);
                count++;
            }
        }
    }
    if (valid_count == 0) {
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }
    if (ext_mbl_save(macs, count) != 0) {
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }
    ext_mbl_touch();
    return fwx_gen_api_response_data(API_CODE_SUCCESS, NULL);
}

struct json_object *fwx_api_del_mac_blacklist(struct json_object *req_obj)
{
    char macs[EXT_MBL_MAX][EXT_MAC_LEN] = {{0}};
    int count = ext_mbl_load(macs, EXT_MBL_MAX);
    struct json_object *mac_obj = NULL;
    const char *raw = NULL;
    char norm[EXT_MAC_LEN] = {0};
    int keep = 0;
    int i;

    if (!req_obj ||
        !json_object_object_get_ex(req_obj, "mac", &mac_obj)) {
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }
    raw = json_object_get_string(mac_obj);
    ext_norm_mac(raw ? raw : "", norm, sizeof(norm));
    if (norm[0] == '\0') {
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }
    for (i = 0; i < count; i++) {
        if (strcmp(macs[i], norm) == 0) {
            continue; /* 丢弃被删除的项 */
        }
        if (keep != i) {
            memcpy(macs[keep], macs[i], EXT_MAC_LEN);
        }
        keep++;
    }
    if (ext_mbl_save(macs, keep) != 0) {
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }
    ext_mbl_touch();
    return fwx_gen_api_response_data(API_CODE_SUCCESS, NULL);
}

/* ------------------------------------------------------- 系统基础信息 */

/*
 * get_system_base_info —— 仪表板配置页调用。
 * 24.10 没有 user_session proc（fwx_user_session），恒 0；
 * wireless_support 依赖 g_fwx_capability（24.10 无此结构），不输出。
 */
struct json_object *fwx_api_get_system_base_info(struct json_object *req_obj)
{
    struct json_object *data_obj = json_object_new_object();

    (void)req_obj;
    if (!data_obj) {
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }
    json_object_object_add(data_obj, "user_session_enable", json_object_new_int(0));
    return fwx_gen_api_response_data(API_CODE_SUCCESS, data_obj);
}

/* ------------------------------------------------------- 家长控制详情 */

/*
 * get_parental_control_detail —— user_status.htm 的 showParentalControlDetail：
 *   data: { pc_status, pc_status_key, af_whitelist, mf_whitelist,
 *           appfilter_rules: [{rule_id, rule_name, mode, app_list, start_time, end_time}],
 *           macfilter_rules: [{rule_id, rule_name, mode, match_type, start_time, end_time}] }
 * pc_status 三态：mac_blocked（命中黑名单）/ app_limited（命中应用过滤规则）/ unlimited。
 * 25.12 的版本从家长控制缓存 JSON 读；24.10 没有那套缓存，这里直接从
 * uci 规则实时聚合，语义一致、无缓存时差。
 */
/* 解析 <pkg>.@rule[idx].time_rule 时间规则，输出到 time_array。
 * 格式与 rule_manager.lua 一致：多段用空格分隔，每段 "周几,HH:MM,HH:MM"
 * （周几可多个、也可省略）。返回 1 表示当前时间落在任一段内（今日生效）。 */
static int ext_parse_rule_time_rules(struct uci_context *ctx, const char *pkgname,
                                     int idx, struct json_object *time_array)
{
    char path[128] = {0};
    char buf[1024] = {0};
    int effective_today = 0;
    time_t now_t = time(NULL);
    struct tm now_buf;
    struct tm *now_tm = localtime_r(&now_t, &now_buf);
    char *seg = NULL;
    char *save = NULL;

    if (!ctx || !pkgname || !time_array) {
        return 0;
    }
    snprintf(path, sizeof(path), "%s.@rule[%d].time_rule", pkgname, idx);
    if (fwx_uci_get_list_value(ctx, path, buf, sizeof(buf), " ") != 0 || buf[0] == '\0') {
        return 0;
    }

    for (seg = strtok_r(buf, " ", &save); seg; seg = strtok_r(NULL, " ", &save)) {
        char tmp[256] = {0};
        char *token = NULL;
        char *tsave = NULL;
        char *start_time = NULL;
        char *end_time = NULL;
        struct json_object *tr_obj = json_object_new_object();
        struct json_object *weekdays = json_object_new_array();
        int match_today = 0;
        int has_weekday = 0;

        if (!tr_obj || !weekdays) {
            if (tr_obj) {
                json_object_put(tr_obj);
            }
            if (weekdays) {
                json_object_put(weekdays);
            }
            continue;
        }
        snprintf(tmp, sizeof(tmp), "%s", seg);
        for (token = strtok_r(tmp, ",", &tsave); token; token = strtok_r(NULL, ",", &tsave)) {
            if (strchr(token, ':') != NULL) {
                if (!start_time) {
                    start_time = token;
                } else if (!end_time) {
                    end_time = token;
                }
            } else {
                int wd = atoi(token);
                if (wd >= 0 && wd <= 6) {
                    struct json_object *wd_obj = json_object_new_int(wd);
                    if (wd_obj) {
                        json_object_array_add(weekdays, wd_obj);
                    }
                    if (now_tm && wd == now_tm->tm_wday) {
                        match_today = 1;
                    }
                    has_weekday = 1;
                }
            }
        }
        if (start_time && end_time && (!has_weekday || match_today)) {
            int sh = 0;
            int sm = 0;
            int eh = 0;
            int em = 0;
            int now_min = now_tm ? now_tm->tm_hour * 60 + now_tm->tm_min : -1;

            sscanf(start_time, "%d:%d", &sh, &sm);
            sscanf(end_time, "%d:%d", &eh, &em);
            if (now_min >= sh * 60 + sm && now_min <= eh * 60 + em) {
                effective_today = 1;
            }
        }

        json_object_object_add(tr_obj, "weekdays", weekdays);
        json_object_object_add(tr_obj, "start_time",
                               json_object_new_string(start_time ? start_time : ""));
        json_object_object_add(tr_obj, "end_time",
                               json_object_new_string(end_time ? end_time : ""));
        json_object_object_add(tr_obj, "start",
                               json_object_new_string(start_time ? start_time : ""));
        json_object_object_add(tr_obj, "end",
                               json_object_new_string(end_time ? end_time : ""));
        json_object_array_add(time_array, tr_obj);
    }
    return effective_today;
}

/* macfilter 规则聚合（time 规则 + 黑名单条目），parental rules/detail 两个接口共用。
 * match_type: time_range（命中时段规则）/ blacklist（命中设备黑名单）。 */
static void ext_collect_macfilter_rules(struct json_object *arr, const char *norm,
                                        int *matched)
{
    struct uci_context *ctx = uci_alloc_context();
    int rule_num = 0;
    int i;
    time_t now_t = time(NULL);
    struct tm *now_tm = localtime(&now_t);

    if (!ctx) {
        return;
    }
    rule_num = fwx_uci_get_list_num(ctx, "macfilter", "rule");
    for (i = 0; i < rule_num; i++) {
        char path[128] = {0};
        char id_str[32] = {0};
        char name_str[128] = {0};
        char mac_str[EXT_MAC_LEN] = {0};
        char enabled_str[16] = {0};
        char time_buf[1024] = {0};
        char mode_str[16] = {0};
        struct json_object *rule_obj = NULL;
        struct json_object *time_array = NULL;
        int enabled = 1;
        int effective_today = 0;
        int rule_mode = 1;   /* 1=所有设备 2=指定设备（界面 getModeText 的判定） */

        snprintf(path, sizeof(path), "macfilter.@rule[%d].id", i);
        if (fwx_uci_get_value(ctx, path, id_str, sizeof(id_str)) != 0) {
            continue;
        }
        snprintf(path, sizeof(path), "macfilter.@rule[%d].name", i);
        fwx_uci_get_value(ctx, path, name_str, sizeof(name_str));
        snprintf(path, sizeof(path), "macfilter.@rule[%d].user_mac", i);
        fwx_uci_get_value(ctx, path, mac_str, sizeof(mac_str));
        snprintf(path, sizeof(path), "macfilter.@rule[%d].enabled", i);
        if (fwx_uci_get_value(ctx, path, enabled_str, sizeof(enabled_str)) == 0) {
            enabled = atoi(enabled_str);
        }
        snprintf(path, sizeof(path), "macfilter.@rule[%d].mode", i);
        if (fwx_uci_get_value(ctx, path, mode_str, sizeof(mode_str)) == 0 && mode_str[0] != '\0') {
            rule_mode = atoi(mode_str) == 2 ? 2 : 1;
        }

        /* mode=1 是"所有设备"规则（没有 user_mac），对本设备同样生效；
         * mode=2 才按 user_mac 精确匹配（macfilter.@rule 的 uci mode 语义） */
        if (!enabled || (rule_mode != 1 && !ext_mac_equal(mac_str, norm))) {
            continue;
        }

        rule_obj = json_object_new_object();
        time_array = json_object_new_array();
        if (!rule_obj || !time_array) {
            if (rule_obj) {
                json_object_put(rule_obj);
            }
            if (time_array) {
                json_object_put(time_array);
            }
            continue;
        }

        json_object_object_add(rule_obj, "module", json_object_new_string("macfilter"));
        json_object_object_add(rule_obj, "rule_id", json_object_new_int(atoi(id_str)));
        json_object_object_add(rule_obj, "rule_name", json_object_new_string(name_str));
        json_object_object_add(rule_obj, "mode", json_object_new_int(rule_mode));
        json_object_object_add(rule_obj, "match_type", json_object_new_string("time_range"));

        snprintf(path, sizeof(path), "macfilter.@rule[%d].time_rule", i);
        if (fwx_uci_get_list_value(ctx, path, time_buf, sizeof(time_buf), " ") == 0 &&
            time_buf[0] != '\0') {
            char *seg = NULL;
            char *save = NULL;

            for (seg = strtok_r(time_buf, " ", &save); seg;
                 seg = strtok_r(NULL, " ", &save)) {
                char tmp[256] = {0};
                char *token = NULL;
                char *tsave = NULL;
                char *start_time = NULL;
                char *end_time = NULL;
                struct json_object *tr_obj = json_object_new_object();
                struct json_object *weekdays = json_object_new_array();
                int match_today = 0;
                int has_weekday = 0;

                if (!tr_obj || !weekdays) {
                    if (tr_obj) {
                        json_object_put(tr_obj);
                    }
                    if (weekdays) {
                        json_object_put(weekdays);
                    }
                    continue;
                }
                snprintf(tmp, sizeof(tmp), "%s", seg);
                for (token = strtok_r(tmp, ",", &tsave); token;
                     token = strtok_r(NULL, ",", &tsave)) {
                    if (strchr(token, ':') != NULL) {
                        if (!start_time) {
                            start_time = token;
                        } else if (!end_time) {
                            end_time = token;
                        }
                    } else {
                        int wd = atoi(token);
                        if (wd >= 0 && wd <= 6) {
                            struct json_object *wd_obj = json_object_new_int(wd);
                            if (wd_obj) {
                                json_object_array_add(weekdays, wd_obj);
                            }
                            if (now_tm && wd == now_tm->tm_wday) {
                                match_today = 1;
                            }
                            has_weekday = 1;
                        }
                    }
                }
                if (start_time && end_time) {
                    if (!has_weekday || match_today) {
                        int sh = 0;
                        int sm = 0;
                        int eh = 0;
                        int em = 0;
                        int now_min = now_tm ? now_tm->tm_hour * 60 + now_tm->tm_min : -1;

                        sscanf(start_time, "%d:%d", &sh, &sm);
                        sscanf(end_time, "%d:%d", &eh, &em);
                        if (now_min >= sh * 60 + sm && now_min <= eh * 60 + em) {
                            effective_today = 1;
                        }
                    }
                }

                json_object_object_add(tr_obj, "weekdays", weekdays);
                /* 界面（user_detail.htm 的 format_behavior_time_rules、user_status.htm
                 * 的 formatTimeRuleText）读的是 start_time / end_time；
                 * 同时保留 start / end 供其他消费方使用 */
                json_object_object_add(tr_obj, "start_time",
                                       json_object_new_string(start_time ? start_time : ""));
                json_object_object_add(tr_obj, "end_time",
                                       json_object_new_string(end_time ? end_time : ""));
                json_object_object_add(tr_obj, "start",
                                       json_object_new_string(start_time ? start_time : ""));
                json_object_object_add(tr_obj, "end",
                                       json_object_new_string(end_time ? end_time : ""));
                json_object_array_add(time_array, tr_obj);
            }
        }

        json_object_object_add(rule_obj, "time_rules", time_array);
        json_object_object_add(rule_obj, "start_time", json_object_new_string(""));
        json_object_object_add(rule_obj, "end_time", json_object_new_string(""));
        {
            struct json_object *status_obj = json_object_new_object();
            if (status_obj) {
                json_object_object_add(status_obj, "effective_today",
                                       json_object_new_int(effective_today));
                json_object_object_add(status_obj, "unlimited", json_object_new_int(0));
                json_object_object_add(rule_obj, "today_status", status_obj);
            }
        }
        json_object_array_add(arr, rule_obj);
        /* 只有今日时段命中才算"正在生效"：rule_manager 里没有 time_rule 的规则
         * 从不生效（is_time_in_range 返回 false），这里保持一致 */
        if (effective_today) {
            *matched = 1;
        }
    }
    uci_free_context(ctx);

    /* 黑名单条目：命中设备黑名单时补一条 match_type=blacklist（id 用 0，排在最前） */
    if (ext_mbl_hit(norm)) {
        struct json_object *rule_obj = json_object_new_object();
        if (rule_obj) {
            json_object_object_add(rule_obj, "module", json_object_new_string("macfilter"));
            json_object_object_add(rule_obj, "rule_id", json_object_new_int(0));
            json_object_object_add(rule_obj, "rule_name", json_object_new_string("Internet Blacklist"));
            json_object_object_add(rule_obj, "mode", json_object_new_int(2));   /* 指定设备 */
            json_object_object_add(rule_obj, "match_type", json_object_new_string("blacklist"));
            json_object_object_add(rule_obj, "start_time", json_object_new_string(""));
            json_object_object_add(rule_obj, "end_time", json_object_new_string(""));
            json_object_array_add(arr, rule_obj);
            *matched = 1;
        }
    }
}

static void ext_detail_appfilter_rules(struct json_object *arr, const char *norm,
                                       int *matched)
{
    struct uci_context *ctx = uci_alloc_context();
    int rule_num = 0;
    int i;

    if (!ctx) {
        return;
    }
    rule_num = fwx_uci_get_list_num(ctx, "appfilter", "rule");
    for (i = 0; i < rule_num; i++) {
        char path[128] = {0};
        char id_str[32] = {0};
        char name_str[128] = {0};
        char mac_str[EXT_MAC_LEN] = {0};
        char enabled_str[16] = {0};
        int enabled = 1;
        int af_mode = 1;    /* 1=所有设备 2=指定设备 */
        int af_active = 0;  /* 今日时段是否命中 */
        struct json_object *rule_obj = NULL;
        struct json_object *app_list = NULL;
        int app_count = 0;

        snprintf(path, sizeof(path), "appfilter.@rule[%d].id", i);
        if (fwx_uci_get_value(ctx, path, id_str, sizeof(id_str)) != 0) {
            continue;
        }
        snprintf(path, sizeof(path), "appfilter.@rule[%d].name", i);
        fwx_uci_get_value(ctx, path, name_str, sizeof(name_str));
        snprintf(path, sizeof(path), "appfilter.@rule[%d].user_mac", i);
        fwx_uci_get_value(ctx, path, mac_str, sizeof(mac_str));
        snprintf(path, sizeof(path), "appfilter.@rule[%d].enabled", i);
        if (fwx_uci_get_value(ctx, path, enabled_str, sizeof(enabled_str)) == 0) {
            enabled = atoi(enabled_str);
        }
        snprintf(path, sizeof(path), "appfilter.@rule[%d].mode", i);
        {
            char mode_str[16] = {0};

            if (fwx_uci_get_value(ctx, path, mode_str, sizeof(mode_str)) == 0 &&
                mode_str[0] != '\0') {
                af_mode = atoi(mode_str) == 2 ? 2 : 1;
            }
        }

        /* mode=1 是"所有设备"规则（没有 user_mac），对本设备同样生效；
         * mode=2 才按 user_mac 精确匹配 */
        if (!enabled || (af_mode != 1 && !ext_mac_equal(mac_str, norm))) {
            continue;
        }

        rule_obj = json_object_new_object();
        app_list = json_object_new_array();
        if (!rule_obj || !app_list) {
            if (rule_obj) {
                json_object_put(rule_obj);
            }
            if (app_list) {
                json_object_put(app_list);
            }
            continue;
        }
        json_object_object_add(rule_obj, "rule_id", json_object_new_int(atoi(id_str)));
        json_object_object_add(rule_obj, "rule_name", json_object_new_string(name_str));
        json_object_object_add(rule_obj, "mode", json_object_new_int(af_mode));
        {
            struct json_object *af_time_rules = json_object_new_array();
            if (af_time_rules) {
                af_active = ext_parse_rule_time_rules(ctx, "appfilter", i, af_time_rules);
                struct json_object *af_status = json_object_new_object();

                json_object_object_add(rule_obj, "time_rules", af_time_rules);
                if (af_status) {
                    json_object_object_add(af_status, "effective_today",
                                           json_object_new_int(af_active));
                    json_object_object_add(af_status, "unlimited", json_object_new_int(0));
                    json_object_object_add(rule_obj, "today_status", af_status);
                }
            }
        }
        json_object_object_add(rule_obj, "start_time", json_object_new_string(""));
        json_object_object_add(rule_obj, "end_time", json_object_new_string(""));

        /* 命中的应用清单：uci 键名是 app_id（rule_manager.lua 的
         * load_appfilter_rules 读 section.app_id），list 与单值 option 都兼容 */
        snprintf(path, sizeof(path), "appfilter.@rule[%d].app_id", i);
        {
            char apps_buf[1024] = {0};
            int id_list[128] = {0};
            int class_hit[64] = {0};   /* class_id → app 个数（给 category_stats） */
            int n_ids = 0;
            int j;

            if (fwx_uci_get_list_value(ctx, path, apps_buf, sizeof(apps_buf), " ") == 0 &&
                apps_buf[0] != '\0') {
                char *ap = NULL;
                char *asave = NULL;
                for (ap = strtok_r(apps_buf, " \t", &asave); ap && n_ids < 128;
                     ap = strtok_r(NULL, " \t", &asave)) {
                    int appid = atoi(ap);
                    if (appid > 0) {
                        id_list[n_ids++] = appid;
                    }
                }
            } else {
                /* 单值 option 兜底 */
                char one[32] = {0};
                if (fwx_uci_get_value(ctx, path, one, sizeof(one)) == 0) {
                    int appid = atoi(one);
                    if (appid > 0) {
                        id_list[n_ids++] = appid;
                    }
                }
            }

            for (j = 0; j < n_ids; j++) {
                struct json_object *app_obj = json_object_new_object();
                int cid = ext_class_id_of_appid(id_list[j]);

                if (app_obj) {
                    json_object_object_add(app_obj, "id", json_object_new_int(id_list[j]));
                    json_object_object_add(app_obj, "name",
                                           json_object_new_string(get_app_name_by_id(id_list[j])));
                    json_object_array_add(app_list, app_obj);
                    app_count++;
                }
                if (cid > 0 && cid < 64) {
                    class_hit[cid]++;
                }
            }

            /* category_stats：user_status.htm 的“受限应用”一行读的是
             * rule.category_stats（[{id: 分类号, count: 个数}]），
             * 分类号与 #class 行一致（/tmp/app_class.txt 的 $1） */
            {
                struct json_object *stats = json_object_new_array();
                if (stats) {
                    for (j = 1; j < 64; j++) {
                        if (class_hit[j] > 0) {
                            struct json_object *item = json_object_new_object();
                            if (item) {
                                json_object_object_add(item, "id", json_object_new_int(j));
                                json_object_object_add(item, "count",
                                                       json_object_new_int(class_hit[j]));
                                json_object_array_add(stats, item);
                            }
                        }
                    }
                    json_object_object_add(rule_obj, "category_stats", stats);
                }
            }
        }

        json_object_object_add(rule_obj, "app_list", app_list);
        json_object_array_add(arr, rule_obj);
        /* 与 macfilter 同口径：今日时段命中 + 规则里确实有应用，才算正在生效 */
        if (app_count > 0 && af_active) {
            *matched = 1;
        }
    }
    uci_free_context(ctx);
}

struct json_object *fwx_api_get_parental_control_detail(struct json_object *req_obj)
{
    struct json_object *mac_obj = NULL;
    struct json_object *data_obj = NULL;
    struct json_object *af_rules = NULL;
    struct json_object *mf_rules = NULL;
    const char *mac = NULL;
    char norm[EXT_MAC_LEN] = {0};
    int af_whitelist = 0;
    int mf_whitelist = 0;
    int app_matched = 0;
    const char *status_key = "unlimited";

    if (!req_obj || !json_object_object_get_ex(req_obj, "mac", &mac_obj) || !mac_obj) {
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }
    mac = json_object_get_string(mac_obj);
    ext_norm_mac(mac ? mac : "", norm, sizeof(norm));
    if (norm[0] == '\0') {
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }

    data_obj = json_object_new_object();
    af_rules = json_object_new_array();
    mf_rules = json_object_new_array();
    if (!data_obj || !af_rules || !mf_rules) {
        if (data_obj) {
            json_object_put(data_obj);
        }
        if (af_rules) {
            json_object_put(af_rules);
        }
        if (mf_rules) {
            json_object_put(mf_rules);
        }
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }

    af_whitelist = ext_uci_mac_in_array_list("appfilter_whitelist", "whitelist_mac", norm);
    mf_whitelist = ext_uci_mac_in_array_list("macfilter_whitelist", "whitelist_mac", norm);

    ext_detail_appfilter_rules(af_rules, norm, &app_matched);
    ext_collect_macfilter_rules(mf_rules, norm, &app_matched);

    if (mf_whitelist) {
        status_key = "unlimited";
    } else if (ext_mbl_hit(norm) > 0) {
        status_key = "mac_blocked";
    } else if (app_matched && !af_whitelist) {
        status_key = "app_limited";
    }

    json_object_object_add(data_obj, "pc_status", json_object_new_string(status_key));
    json_object_object_add(data_obj, "pc_status_key", json_object_new_string(status_key));
    json_object_object_add(data_obj, "af_whitelist", json_object_new_int(af_whitelist ? 1 : 0));
    json_object_object_add(data_obj, "mf_whitelist", json_object_new_int(mf_whitelist ? 1 : 0));
    json_object_object_add(data_obj, "appfilter_rules", af_rules);
    json_object_object_add(data_obj, "macfilter_rules", mf_rules);
    return fwx_gen_api_response_data(API_CODE_SUCCESS, data_obj);
}

/* ---- be72: 特征库信息（v1.8.0 补函数体：v1.8.0 只加了声明与分派行，
     链接时 undefined reference 导致 build-41 失败 —— 教训：加分派行必须
     同步补函数体，自查脚本已入 CI 前置流程） ---- */
struct json_object *fwx_api_get_feature_info(struct json_object *req_obj) {
    struct json_object *resp;
    struct json_object *data_obj;
    char version[128] = {0};
    int feature_count = 0;
    FILE *fp;

    (void)req_obj;

    /* 版本：/etc/fwxd/feature.cfg 头部的 "#version xxx" */
    fp = fopen("/etc/fwxd/feature.cfg", "r");
    if (fp) {
        char line[512] = {0};
        while (fgets(line, sizeof(line), fp)) {
            if (strncmp(line, "#version ", 9) == 0) {
                char *v = line + 9;
                char *e;
                while (*v == ' ')
                    v++;
                e = v + strlen(v);
                while (e > v && (e[-1] == '\n' || e[-1] == '\r'))
                    e--;
                *e = 0;
                snprintf(version, sizeof(version), "%s", v);
                break;
            }
        }
        fclose(fp);
    }
    if (version[0] == 0)
        snprintf(version, sizeof(version), "unknown");

    /* 个数：内核已加载的特征条数（/proc/sys/fwx/feature_count） */
    fp = fopen("/proc/sys/fwx/feature_count", "r");
    if (fp) {
        if (fscanf(fp, "%d", &feature_count) != 1)
            feature_count = 0;
        fclose(fp);
    }

    data_obj = json_object_new_object();
    if (!data_obj)
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    json_object_object_add(data_obj, "version", json_object_new_string(version));
    json_object_object_add(data_obj, "count", json_object_new_int(feature_count));
    json_object_object_add(data_obj, "feature_count", json_object_new_int(feature_count));
    resp = fwx_gen_api_response_data(API_CODE_SUCCESS, data_obj);
    json_object_put(data_obj);
    return resp;
}
