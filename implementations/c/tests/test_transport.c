/* Native QUIC regression: standard ownership, private JSON, and Open Connect
 * replies share one connection. Schema validation remains a test concern. */
#include "internal.h"
#include <assert.h>
#include <arpa/inet.h>
#include <string.h>
#include <unistd.h>
typedef struct { bool server; cgp_peer *peer; unsigned messages, private_messages, replies; } test_app;
static const char *body="{\"battery_percent\":101,\"future\":true}";
static void connected(cgp_peer *p,const char *fingerprint,void *user) {
    test_app *a=user;a->peer=p;
    assert(fingerprint && strlen(fingerprint)==64);
    assert(cgp_publish(p,0,"{\"type\":\"info\",\"protocol_version\":\"0.2.0\"}"));
    assert(cgp_publish(p,a->server ? 4 : 6,body));
    assert(!cgp_publish(p,a->server ? 6 : 4,body));
    assert(!cgp_publish(p,9,body));
    assert(!cgp_publish(p,1000,body));
    assert(cgp_bind_private(p,UINT32_MAX,CGP_PRIVATE_JSON,"io.test.json"));
    assert(cgp_publish(p,UINT32_MAX,"{\"type\":\"private\",\"name\":\"io.test.json\"}"));
    if (a->server) assert(cgp_publish(p,2,"{\"ShotNumber\":1}"));
    else assert(!cgp_publish(p,2,"{\"Code\":200}")); /* device opens */
}
static void message(cgp_peer *p,uint32_t channel,const char *json,size_t length,void *user) {
    test_app *a=user;
    if(channel==0)return;
    if(channel==UINT32_MAX){++a->private_messages;return;}
    if(channel==2){
        ++a->replies;
        if(!a->server) assert(cgp_publish(p,2,"{\"Code\":200}"));
        return;
    }
    assert(channel==(a->server ? 6u : 4u));
    assert(length==strlen(body) && !memcmp(json,body,length));
    ++a->messages;
}
int main(void) {
    test_app server={.server=true},client={0};
    cgp_options o={.role=CGP_LAUNCH_MONITOR,.draft=CGP_DRAFT_2,.listener=true,.name="native regression",
        .callbacks={.connected=connected,.message=message},.user=&server};
    inet_pton(AF_INET,"127.0.0.1",&o.local_ip);
    cgp_engine *listener=cgp_create_options(&o);assert(listener);
    o.role=CGP_SIMULATOR;o.listener=false;o.user=&client;
    cgp_engine *connector=cgp_create_options(&o);assert(connector);
    struct sockaddr_in address={.sin_family=AF_INET,.sin_addr=o.local_ip,.sin_port=htons(cgp_port(listener))};
    assert(cgp_connect(connector,&address,"native regression"));
    uint64_t deadline=cgp_now_ns()+UINT64_C(10000000000);
    while(cgp_now_ns()<deadline && (!server.messages || !client.messages || !server.private_messages || !client.private_messages || !server.replies || !client.replies)) {
        cgp_poll(listener);cgp_poll(connector);usleep(1000);
    }
    assert(server.messages==1 && client.messages==1);
    assert(server.private_messages==1 && client.private_messages==1);
    assert(server.replies==1 && client.replies==1);
    assert(!server.peer->failed && !client.peer->failed);
    cgp_destroy(connector);cgp_destroy(listener);
    return 0;
}
