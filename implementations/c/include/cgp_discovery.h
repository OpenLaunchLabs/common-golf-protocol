/* SPDX-License-Identifier: Apache-2.0 */
#ifndef CGP_DISCOVERY_H
#define CGP_DISCOVERY_H
#include <stdint.h>
#include <netinet/in.h>
typedef struct cgp_discovery cgp_discovery;
typedef void (*cgp_discovered_key)(const char *name,const struct sockaddr_in *address,const char *key,void *user);
typedef void (*cgp_discovered)(const char *name,const struct sockaddr_in *address,void *user);
/* macOS Bonjour or Linux libavahi-compat-libdnssd. Own and poll on one thread.
 * Legacy drafts 1/2 helper; use cgp_discovery_start_key for default draft 3.
 * NULL name/port0 browses only; servers advertise their actual ephemeral port. */
cgp_discovery *cgp_discovery_start(const char *name,uint16_t port,cgp_discovered,void *);
/* Draft3: validate required key before invoking callback; advertise the key,
 * alpn and caps TXT hints. NULL key is allowed only for a browse-only instance. */
cgp_discovery *cgp_discovery_start_key(const char *name,uint16_t port,const char *key,const char *alpn,const char *caps,cgp_discovered_key,void *);
void cgp_discovery_poll(cgp_discovery *);
void cgp_discovery_stop(cgp_discovery *);
#endif
