/* SPDX-License-Identifier: Apache-2.0 */
#ifndef CGP_DISCOVERY_H
#define CGP_DISCOVERY_H
#include <stdint.h>
#include <netinet/in.h>
typedef struct cgp_discovery cgp_discovery;
typedef void (*cgp_discovered)(const char *name,const struct sockaddr_in *address,void *user);
/* macOS Bonjour or Linux libavahi-compat-libdnssd. Own and poll on one thread.
 * NULL name/port0 browses only; listeners advertise their actual ephemeral port. */
cgp_discovery *cgp_discovery_start(const char *name,uint16_t port,cgp_discovered,void *);
void cgp_discovery_poll(cgp_discovery *);
void cgp_discovery_stop(cgp_discovery *);
#endif
