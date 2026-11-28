#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""batch6：CPU 占用改用 /proc/stat 两次采样（原 top|awk 在 busybox 下恒 0）"""

P = "scripts/patch_fwxd_ext.py"
s = open(P, encoding="utf-8").read()

anchor = '''    memset(result, 0, sizeof(result));
    int cpu_usage = 0;

    if (exec_with_result_line("top -n 1 | grep 'CPU:' | awk -F '%' '{print$4}' | awk -F ' ' '{print$2}'", result, sizeof(result)) < 0) {

        cpu_usage = 0;
    } else {
        cpu_usage = 100 - atoi(result);
    } '''

insert = '''    /* be72: CPU 占用改用 /proc/stat 两次采样 —— 原实现 top -n 1 | grep 'CPU:'
       在 busybox 的 top 里没有该格式，永远取不到值 -> cpu 恒 0（假数据）。
       首次调用无历史基线返回 0，页面轮询 5s 后即为真实值。 */
    {
        static unsigned long long be72_prev_total = 0;
        static unsigned long long be72_prev_idle = 0;
        unsigned long long u = 0, n = 0, s = 0, idle = 0, iow = 0, irq = 0, sirq = 0, steal = 0;
        unsigned long long total = 0, idle_all = 0;
        int cpu_usage = 0;
        FILE *sfp = fopen("/proc/stat", "r");
        if (sfp) {
            if (fscanf(sfp, "cpu %llu %llu %llu %llu %llu %llu %llu %llu",
                       &u, &n, &s, &idle, &iow, &irq, &sirq, &steal) >= 5) {
                total = u + n + s + idle + iow + irq + sirq + steal;
                idle_all = idle + iow;
                if (be72_prev_total != 0 && total > be72_prev_total) {
                    unsigned long long dt = total - be72_prev_total;
                    unsigned long long di = idle_all - be72_prev_idle;
                    cpu_usage = (int)(100ULL * (dt - di) / dt);
                    if (cpu_usage < 0)
                        cpu_usage = 0;
                    if (cpu_usage > 100)
                        cpu_usage = 100;
                }
                be72_prev_total = total;
                be72_prev_idle = idle_all;
            }
            fclose(sfp);
        }
        snprintf(buf, sizeof(buf), "%d", cpu_usage);
        json_object_object_add(system_status, "cpu", json_object_new_string(buf));
    }'''

assert s.count(anchor) == 1, "CPU 锚点未命中"
s = s.replace(anchor, insert, 1)
open(P, "w", encoding="utf-8").write(s)
print("batch6 CPU 采样已加")
