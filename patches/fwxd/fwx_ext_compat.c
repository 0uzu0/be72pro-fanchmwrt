// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * 24.10.4 的 fwxd 缺的两个小接口，在这里补齐（不引入任何新依赖）：
 *
 *   get_init_status / set_init_status
 *     仪表板「首次使用引导」浮层用。24.10.4 的 fwxd 没有这两个 api，
 *     页面调用拿到 error，浮层就被判定为「已完成」而永不弹出。
 *     语义与 25.12.6 完全一致：读 /etc/fwx_init_status，文件不存在按 1（已引导）
 *     处理，只有显式写入 0 才会再次弹出。
 *
 * 之所以单独放一个文件而不是往 fwx_ubus.c 里插函数体：
 *   锚点脚本只需要在 fwx_ubus.c 里插「声明 + 分派表两行」，
 *   逻辑全部留在本文件，改动面最小、最不容易随上游漂移失效。
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <json-c/json.h>
#include "fwx.h"

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
