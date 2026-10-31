/* SPDX-License-Identifier: Apache-2.0 */
#include "cgp_discovery.h"
#include <dns_sd.h>
#include <stdlib.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <sys/select.h>
#include <netdb.h>

typedef struct resolve_request { struct resolve_request *next; struct cgp_discovery *owner; DNSServiceRef ref; char name[256]; bool complete; } resolve_request;
struct cgp_discovery { DNSServiceRef browse,registration; resolve_request *requests; cgp_discovered callback; void *user; };
static void resolved(DNSServiceRef ref,DNSServiceFlags flags,uint32_t interface,DNSServiceErrorType error,const char *fullname,const char *host,uint16_t port,uint16_t txtlen,const unsigned char *txt,void *user) {
    (void)ref;(void)flags;(void)interface;(void)fullname;(void)txtlen;(void)txt;
    resolve_request *r=user; r->complete=true;
    if (error || !r->owner->callback) return;
    struct addrinfo hint={.ai_family=AF_INET,.ai_socktype=SOCK_DGRAM},*addresses=NULL;
    if (getaddrinfo(host,NULL,&hint,&addresses)) return;
    for (struct addrinfo *a=addresses;a;a=a->ai_next) {
        struct sockaddr_in address=*(struct sockaddr_in *)a->ai_addr; address.sin_port=port;
        r->owner->callback(r->name,&address,r->owner->user);
    }
    freeaddrinfo(addresses);
}
static void browsed(DNSServiceRef ref,DNSServiceFlags flags,uint32_t interface,DNSServiceErrorType error,const char *name,const char *type,const char *domain,void *user) {
    (void)ref;
    if (error || !(flags & kDNSServiceFlagsAdd)) return;
    cgp_discovery *d=user; resolve_request *r=calloc(1,sizeof(*r)); if (!r) return;
    r->owner=d; snprintf(r->name,sizeof(r->name),"%s",name);
    if (DNSServiceResolve(&r->ref,0,interface,name,type,domain,resolved,r)) {free(r);return;}
    r->next=d->requests; d->requests=r;
}
cgp_discovery *cgp_discovery_start(const char *name,uint16_t port,cgp_discovered callback,void *user) {
    cgp_discovery *d=calloc(1,sizeof(*d)); if (!d) return NULL;
    d->callback=callback;d->user=user;
    if (name && port && DNSServiceRegister(&d->registration,0,0,name,"_common-golf._udp",NULL,NULL,htons(port),0,NULL,NULL,NULL)) goto fail;
    if (callback && DNSServiceBrowse(&d->browse,0,0,"_common-golf._udp",NULL,browsed,d)) goto fail;
    return d;
fail: cgp_discovery_stop(d); return NULL;
}
static void process(DNSServiceRef ref) {
    if (!ref) return;
    int fd=DNSServiceRefSockFD(ref); if (fd<0 || fd>=FD_SETSIZE) return;
    fd_set fds; FD_ZERO(&fds);FD_SET(fd,&fds);struct timeval wait={0};
    if (select(fd+1,&fds,NULL,NULL,&wait)>0) DNSServiceProcessResult(ref);
}
void cgp_discovery_poll(cgp_discovery *d) {
    if (!d) return;
    process(d->registration);process(d->browse);
    resolve_request **link=&d->requests;
    while (*link) {
        resolve_request *r=*link;process(r->ref);
        if (r->complete) {*link=r->next;DNSServiceRefDeallocate(r->ref);free(r);} else link=&r->next;
    }
}
void cgp_discovery_stop(cgp_discovery *d) {
    if (!d) return;
    if (d->browse) DNSServiceRefDeallocate(d->browse);
    if (d->registration) DNSServiceRefDeallocate(d->registration);
    while (d->requests) {resolve_request *r=d->requests;d->requests=r->next;DNSServiceRefDeallocate(r->ref);free(r);}
    free(d);
}
