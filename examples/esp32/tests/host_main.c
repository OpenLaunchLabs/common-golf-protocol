/* SPDX-License-Identifier: Apache-2.0
 * Test-only host adapter. Endpoint arguments are an integration-test fixture;
 * firmware exclusively consumes DNS-SD discovery results. */
#include "demo.h"
#include <arpa/inet.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static volatile sig_atomic_t done;
static void stop(int signal_number) { (void)signal_number; done = 1; }

uint64_t cgp_now_ns(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (uint64_t)now.tv_sec * UINT64_C(1000000000) + (uint64_t)now.tv_nsec;
}
void cgp_random(void *destination, size_t length) { arc4random_buf(destination, length); }
void cgp_log(const char *format, ...) {
    va_list args;
    va_start(args, format);
    vprintf(format, args);
    va_end(args);
    putchar('\n');
}

int main(int argc, char **argv) {
    if (argc < 2) return 2;
    cgp_role role;
    if (!strcmp(argv[1], "launch_monitor")) role = CGP_LAUNCH_MONITOR;
    else if (!strcmp(argv[1], "control_box")) role = CGP_CONTROL_BOX;
    else if (!strcmp(argv[1], "simulator")) role = CGP_SIMULATOR;
    else return 2;
    setvbuf(stdout, NULL, _IOLBF, 0);
    signal(SIGINT, stop);
    signal(SIGTERM, stop);
    struct in_addr local;
    inet_pton(AF_INET, "127.0.0.1", &local);
    cgp_engine *engine = cgp_create(role, argv[1], local);
    if (!engine) return 1;
    printf("KEY %s\n", cgp_identity(engine));
    printf("PORT %u\n", cgp_port(engine));
    for (int i = 2; i < argc; ++i) {
        struct sockaddr_in remote = {.sin_family = AF_INET, .sin_addr = local,
                                    .sin_port = htons((uint16_t)strtoul(argv[i], NULL, 10))};
#if CONFIG_CGP_DRAFT == 3
        if (++i >= argc || !cgp_connect_key(engine, &remote, "test-server", argv[i])) return 1;
#else
        if (!cgp_connect(engine, &remote, "test-server")) return 1;
#endif
    }
    uint64_t next_stats = 0;
    while (!done) {
        cgp_poll(engine);
        if (cgp_now_ns() >= next_stats) {
            cgp_statistics(engine);
            next_stats = cgp_now_ns() + UINT64_C(10000000000);
        }
        usleep(10000);
    }
    cgp_destroy(engine);
    return 0;
}
