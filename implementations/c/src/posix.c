/* SPDX-License-Identifier: Apache-2.0 */
#include "cgp.h"
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
uint64_t cgp_now_ns(void) {
    struct timespec now; clock_gettime(CLOCK_MONOTONIC,&now);
    return (uint64_t)now.tv_sec*UINT64_C(1000000000)+(uint64_t)now.tv_nsec;
}
void cgp_random(void *data,size_t length) {
    int fd=open("/dev/urandom",O_RDONLY);
    if (fd<0) abort();
    unsigned char *at=data;
    while (length) {
        ssize_t n=read(fd,at,length);
        if (n<0 && errno==EINTR) continue;
        if (n<=0) abort();
        at+=n; length-=(size_t)n;
    }
    close(fd);
}
void cgp_log(const char *format,...) {
    va_list args; va_start(args,format); vfprintf(stderr,format,args); va_end(args); fputc('\n',stderr);
}
