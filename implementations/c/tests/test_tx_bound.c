/* Small JSON writes account for buffer headers and resume after ACKs. */
#include "internal.h"
#include <assert.h>
#include <arpa/inet.h>
#include <string.h>
#include <unistd.h>
typedef struct {bool server,full,recovered;size_t written,received,full_bytes;cgp_peer *peer;} app;
static size_t allocations(cgp_peer *p){size_t total=0;for(cgp_stream *s=p->streams;s;s=s->next)for(cgp_buffer *b=s->head;b;b=b->next)total+=sizeof(*b)+b->length;return total;}
static void connected(cgp_peer *p,const char *fp,void *user){
    (void)fp;app *a=user;a->peer=p;assert(cgp_publish(p,0,"{\"type\":\"info\",\"protocol_version\":\"0.2.0\"}"));
    if(!a->server)return;
    while(cgp_publish(p,4,"{}")) {
        ++a->written;assert(a->written<=CGP_TX_LIMIT/(sizeof(cgp_buffer)+6));
    }
    assert(a->written>1000);assert(p->queued==allocations(p));assert(p->queued<=CGP_TX_LIMIT);
    assert(CGP_TX_LIMIT-p->queued<sizeof(cgp_buffer)+6);
    a->full=true;a->full_bytes=p->queued;
}
static void tick(cgp_peer *p,void *user){
    app *a=user;if(!a->server || !a->full || a->recovered || p->queued>=a->full_bytes)return;
    assert(p->queued==allocations(p));
    if(cgp_publish(p,4,"{}")) {++a->written;a->recovered=true;assert(cgp_finish_channel(p,4));}
}
static void message(cgp_peer *p,uint32_t ch,const char *json,size_t length,void *user){
    (void)p;app *a=user;if(!ch)return;assert(!a->server && ch==4);assert(length==2 && !memcmp(json,"{}",2));++a->received;
}
int main(void){
    app server={.server=true},client={0};
    cgp_options o={.role=CGP_LAUNCH_MONITOR,.draft=CGP_DRAFT_2,.is_server=true,.name="queue regression",.callbacks={.connected=connected,.message=message,.tick=tick},.user=&server};
    inet_pton(AF_INET,"127.0.0.1",&o.local_ip);cgp_engine *server_engine=cgp_create_options(&o);assert(server_engine);
    o.is_server=false;o.role=CGP_SIMULATOR;o.user=&client;cgp_engine *client_engine=cgp_create_options(&o);assert(client_engine);
    struct sockaddr_in addr={.sin_family=AF_INET,.sin_addr=o.local_ip,.sin_port=htons(cgp_port(server_engine))};assert(cgp_connect(client_engine,&addr,"queue regression"));
    uint64_t deadline=cgp_now_ns()+15000000000ULL;
    while(cgp_now_ns()<deadline && (!server.recovered || client.received<server.written)){cgp_poll(server_engine);cgp_poll(client_engine);usleep(1000);}
    assert(server.full && server.recovered && client.received==server.written);
    for(unsigned i=0;i<100;++i){cgp_poll(server_engine);cgp_poll(client_engine);usleep(1000);}
    assert(server.peer->queued==0);assert(!server.peer->failed && !client.peer->failed);
    cgp_destroy(client_engine);cgp_destroy(server_engine);return 0;
}
