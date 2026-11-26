// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * 防火墙基础设置 —— 从 fanchmwrt 25.12.6 的
 *   package/fcm/fwxd/src/fwx_firewall.c
 * 原样移植而来（逻辑未改）。
 *
 * 为什么这个文件能直接搬到 24.10.4：
 *   - 它只读写 fw4 的标准 uci 节点 firewall.@defaults[0]
 *     （syn_flood / input / output / forward / fullcone），
 *     没有 fanchmwrt 自有的规则模型，与锐捷这棵树的 fw4/nftables 完全同构。
 *   - 依赖的 fwx_uci_get_value / fwx_uci_set_int_value / fwx_uci_commit
 *     来自 libfwx_common，两个版本源码逐字节相同。
 * 因此这里不构成「两套防火墙打架」，只是一个 FullCone + 基础策略的开关页。
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <json-c/json.h>
#include <uci.h>
#include "fwx.h"
#include "fwx_firewall.h"
#include "fwx_uci.h"

static struct json_object *get_basic_setting(void)
{
    struct uci_context *ctx = uci_alloc_context();
    struct json_object *data_obj = NULL;
    struct json_object *basic_obj = NULL;
    char syn_flood[16] = {0};
    char input[32] = {0};
    char output[32] = {0};
    char forward[32] = {0};
    char fullcone[16] = {0};
    char section_type[32] = {0};

    if (!ctx) {
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }
    if (fwx_uci_get_value(ctx, "firewall.@defaults[0]",
                          section_type, sizeof(section_type)) != UCI_OK) {
        LOG_ERROR("Failed to read firewall defaults\n");
        uci_free_context(ctx);
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }
    fwx_uci_get_value(ctx, "firewall.@defaults[0].syn_flood", syn_flood, sizeof(syn_flood));
    fwx_uci_get_value(ctx, "firewall.@defaults[0].input", input, sizeof(input));
    fwx_uci_get_value(ctx, "firewall.@defaults[0].output", output, sizeof(output));
    fwx_uci_get_value(ctx, "firewall.@defaults[0].forward", forward, sizeof(forward));
    fwx_uci_get_value(ctx, "firewall.@defaults[0].fullcone", fullcone, sizeof(fullcone));

    data_obj = json_object_new_object();
    basic_obj = json_object_new_object();
    if (!data_obj || !basic_obj) {
        if (data_obj) {
            json_object_put(data_obj);
        }
        if (basic_obj) {
            json_object_put(basic_obj);
        }
        uci_free_context(ctx);
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }

    json_object_object_add(basic_obj, "syn_flood",
                           json_object_new_int(atoi(syn_flood)));
    json_object_object_add(basic_obj, "input", json_object_new_string(input));
    json_object_object_add(basic_obj, "output", json_object_new_string(output));
    json_object_object_add(basic_obj, "forward", json_object_new_string(forward));
    json_object_object_add(basic_obj, "fullcone",
                           json_object_new_int(atoi(fullcone)));
    json_object_object_add(data_obj, "basic", basic_obj);

    uci_free_context(ctx);
    return fwx_gen_api_response_data(API_CODE_SUCCESS, data_obj);
}

static struct json_object *set_basic_setting(struct json_object *req_obj)
{
    struct json_object *fullcone_obj = NULL;
    struct uci_context *ctx = NULL;
    int fullcone = 0;

    if (!json_object_object_get_ex(req_obj, "fullcone", &fullcone_obj)) {
        LOG_ERROR("FullCone setting is missing\n");
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }
    if (!json_object_is_type(fullcone_obj, json_type_int) &&
        !json_object_is_type(fullcone_obj, json_type_boolean)) {
        LOG_ERROR("FullCone setting type is invalid\n");
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }

    fullcone = json_object_get_int(fullcone_obj);
    LOG_INFO("set_firewall parsed fullcone: %d\n", fullcone);
    if (fullcone != 0 && fullcone != 1) {
        LOG_ERROR("Invalid FullCone setting: %d\n", fullcone);
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }

    ctx = uci_alloc_context();
    if (!ctx) {
        return fwx_gen_api_response_data(API_CODE_ERROR, NULL);
    }
    fwx_uci_set_int_value(ctx, "firewall.@defaults[0].fullcone", fullcone);
    fwx_uci_commit(ctx, "firewall");
    uci_free_context(ctx);

    system("/etc/init.d/firewall reload >/dev/null 2>&1");
    LOG_INFO("set_firewall basic setting applied\n");
    return fwx_gen_api_response_data(API_CODE_SUCCESS, NULL);
}

struct json_object *fwx_api_get_firewall(struct json_object *req_obj)
{
    (void)req_obj;
    return get_basic_setting();
}

struct json_object *fwx_api_set_firewall(struct json_object *req_obj)
{
    struct json_object *action_obj = NULL;
    struct json_object *basic_obj = NULL;
    struct json_object *response_obj = NULL;
    const char *action = NULL;

    LOG_INFO("fwx_api_set_firewall request: %s\n",
             req_obj ? json_object_to_json_string(req_obj) : "null");

    if (!req_obj ||
        !json_object_object_get_ex(req_obj, "action", &action_obj)) {
        LOG_ERROR("Firewall setting request is invalid\n");
        response_obj = fwx_gen_api_response_data(API_CODE_ERROR, NULL);
        goto done;
    }

    action = json_object_get_string(action_obj);
    if (action && strcmp(action, "set_basic") == 0) {
        if (!json_object_object_get_ex(req_obj, "basic", &basic_obj) ||
            !json_object_is_type(basic_obj, json_type_object)) {
            LOG_ERROR("Firewall basic setting is invalid\n");
            response_obj = fwx_gen_api_response_data(API_CODE_ERROR, NULL);
            goto done;
        }
        response_obj = set_basic_setting(basic_obj);
        goto done;
    }

    LOG_ERROR("Unsupported firewall action: %s\n", action ? action : "");
    response_obj = fwx_gen_api_response_data(API_CODE_ERROR, NULL);

done:
    LOG_INFO("fwx_api_set_firewall response: %s\n",
             response_obj ? json_object_to_json_string(response_obj) : "null");
    return response_obj;
}
