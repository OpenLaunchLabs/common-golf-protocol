/* SPDX-License-Identifier: Apache-2.0 */
#include "cpu_stats.h"
#include "sdkconfig.h"

#if CONFIG_CGP_CPU_PROFILING
#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include "esp_ipc.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#if !defined(CONFIG_FREERTOS_GENERATE_RUN_TIME_STATS) || \
    !defined(CONFIG_FREERTOS_RUN_TIME_COUNTER_TYPE_U64) || \
    !defined(CONFIG_FREERTOS_RUN_TIME_STATS_USING_ESP_TIMER)
#error "CPU profiling requires FreeRTOS run time statistics with uint64_t counters and ESP Timer"
#endif
_Static_assert(sizeof(configRUN_TIME_COUNTER_TYPE) == sizeof(uint64_t),
               "CPU profiling requires a 64-bit FreeRTOS run time counter");

#define MAX_TASKS 32
typedef struct {
    uint64_t idle_us, at_us;
} core_sample;
typedef struct {
    TaskStatus_t tasks[MAX_TASKS];
    UBaseType_t count;
    configRUN_TIME_COUNTER_TYPE at_us;
    core_sample core[CONFIG_FREERTOS_NUMBER_OF_CORES];
} sample;
static sample samples[2];

/* Runtime counters are booked at task switches. Sample from an existing IPC
 * task on each core so a core that has stayed in IDLE is accounted correctly.
 * This is task-context IPC; no UART or expensive stack scanning in callbacks. */
static void sample_core(void *argument)
{
    core_sample *result = argument;
    result->idle_us = ulTaskGetIdleRunTimeCounterForCore(xPortGetCoreID());
    result->at_us = esp_timer_get_time();
}

static bool snapshot(sample *result)
{
    for (unsigned core = 0; core < CONFIG_FREERTOS_NUMBER_OF_CORES; ++core)
        if (esp_ipc_call_blocking(core, sample_core, &result->core[core]) != ESP_OK)
            return false;
    result->count = uxTaskGetSystemState(result->tasks, MAX_TASKS, &result->at_us);
    return result->count != 0;
}

static uint64_t task_delta(const sample *before, const TaskStatus_t *task)
{
    for (UBaseType_t i = 0; i < before->count; ++i)
        if (before->tasks[i].xHandle == task->xHandle &&
            before->tasks[i].xTaskNumber == task->xTaskNumber)
            return task->ulRunTimeCounter - before->tasks[i].ulRunTimeCounter;
    /* A newly created task has accumulated all its runtime in this window. */
    return task->ulRunTimeCounter;
}

static void report(const sample *before, const sample *after)
{
    uint64_t elapsed = after->at_us - before->at_us;
    if (!elapsed) return;
    double busy[2] = {0, 0};
    for (unsigned core = 0; core < CONFIG_FREERTOS_NUMBER_OF_CORES; ++core) {
        uint64_t interval = after->core[core].at_us - before->core[core].at_us;
        uint64_t idle = after->core[core].idle_us - before->core[core].idle_us;
        if (!interval || idle > interval) {
            printf("CPU invalid sample core=%u\n", core);
            return;
        }
        busy[core] = 100.0 * (interval - idle) / interval;
    }
    /* pcTaskName in TaskStatus_t points into the task's TCB, which another
     * core can free after the snapshot. Resolve our known groups through the
     * kernel's protected name lookup, then compare handles without touching
     * captured name pointers. Deleted transient tasks remain unclassified. */
    static const char *const group_names[] = {
        "main", "cgp-cpu", "wifi", "tiT", "mdns", "mdns recv task",
        "esp_timer", "sys_evt", "cgp-discovery"
    };
    TaskHandle_t groups[sizeof(group_names) / sizeof(group_names[0])];
    for (unsigned i = 0; i < sizeof(groups) / sizeof(groups[0]); ++i)
        groups[i] = xTaskGetHandle(group_names[i]);
    uint64_t main_us = 0, network_us = 0, profiler_us = 0, accounted_us = 0;
    for (UBaseType_t i = 0; i < after->count; ++i) {
        const TaskStatus_t *task = &after->tasks[i];
        uint64_t delta = task_delta(before, task);
        accounted_us += delta;
        if (task->xHandle == groups[0]) main_us += delta;
        else if (task->xHandle == groups[1]) profiler_us += delta;
        else {
            for (unsigned group = 2; group < sizeof(groups) / sizeof(groups[0]); ++group) {
                if (task->xHandle == groups[group]) {
                    network_us += delta;
                    break;
                }
            }
        }
    }
    /* Per-task percentages use ONE core as 100%; chip_busy_pct uses both cores
     * as 100%. ISR time is attributed to the interrupted task by FreeRTOS.
     * These are scheduler runtime estimates, not instruction/cycle profiling. */
    printf("CPU t_us=%" PRIu64 " window_us=%" PRIu64
           " core0_busy_pct=%.3f core1_busy_pct=%.3f chip_busy_pct=%.3f"
           " main_core_pct=%.3f network_core_pct=%.3f profiler_core_pct=%.3f"
           " accounted_core_pct=%.3f\n",
           (uint64_t)after->at_us, elapsed, busy[0], busy[1],
           (busy[0] + busy[1]) / CONFIG_FREERTOS_NUMBER_OF_CORES,
           100.0 * main_us / elapsed, 100.0 * network_us / elapsed,
           100.0 * profiler_us / elapsed, 100.0 * accounted_us / elapsed);
}

static void stats_task(void *argument)
{
    (void)argument;
    unsigned previous = 0;
    bool have_previous = snapshot(&samples[previous]);
    TickType_t wake = xTaskGetTickCount();
    while (true) {
        vTaskDelayUntil(&wake, pdMS_TO_TICKS(2000));
        unsigned next = previous ^ 1;
        if (snapshot(&samples[next])) {
            if (have_previous) report(&samples[previous], &samples[next]);
            previous = next;
            have_previous = true;
        } else {
            printf("CPU snapshot failed (task limit %d)\n", MAX_TASKS);
            have_previous = false;
        }
    }
}

void cgp_cpu_stats_start(void)
{
#if CONFIG_CGP_LOG_PAYLOADS
    const int log_payloads = 1;
#else
    const int log_payloads = 0;
#endif
    printf("CPU enabled clock=ESP_TIMER counter_bits=64 cpu_mhz=%d cores=%d payload_logs=%d\n",
           CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ, CONFIG_FREERTOS_NUMBER_OF_CORES,
           log_payloads);
    if (xTaskCreatePinnedToCore(stats_task, "cgp-cpu", 4096, NULL, 2, NULL, 0) != pdPASS)
        printf("CPU profiler task allocation failed\n");
}
#else
void cgp_cpu_stats_start(void) {}
#endif
