/* Real QUIC/TLS regression for mutual raw keys, server menus and binary data. */
#include "internal.h"
#include <assert.h>
#include <arpa/inet.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>

typedef struct {
    bool is_server;
    cgp_peer *peer;
    unsigned connected, menus, json, raw_bytes, raw_fin, datagrams, rejected, shots, closed, clubs, datagram_errors;
    bool shot_reopened, saw_pending_shot, saw_pending_status;
    uint64_t next_datagram;
} app;
static const uint8_t payload[]={0,255,128,0,'a','b','c'};
static int contract(cgp_peer *p,const char *name,void *user) {
    (void)p;(void)user;
    if(!strcmp(name,"io.test.json"))return CGP_PRIVATE_JSON;
    if(!strcmp(name,"io.test.raw"))return CGP_PRIVATE_RAW;
    if(!strcmp(name,"io.test.datagram"))return CGP_PRIVATE_DATAGRAM;
    return -1;
}
static void menu(cgp_peer *p,bool available,void *user) {
    app *a=user;assert(available);++a->menus;
    assert(cgp_custom_ready(p));
    assert(cgp_private_matches(p,1000,"io.test.json"));
    assert(!cgp_publish(p,2,"{}"));
    assert(cgp_publish(p,1000,"{\"binary_transport\":true}"));
    int64_t stream=cgp_open_raw(p,1001,true);assert(stream>=0);
    assert(cgp_send_raw_on_stream(p,stream,payload,sizeof(payload)));
    assert(cgp_finish_stream(p,stream));
    assert(!cgp_send_raw_on_stream(p,stream,payload,sizeof(payload)));
    assert(!cgp_bind_private(p,1005,CGP_PRIVATE_JSON,"io.test.too-late"));
}
/* Deliberately bypass the public sender check to exercise remote malformed
 * handling and verify that code 2 permits a corrected channel reopen. */
static void inject_bad_shot(cgp_peer *p) {
    const char *json="{\"type\":\"shot\",\"shot_uuid\":\"invalid\"}";
    cgp_stream *s=calloc(1,sizeof(*s));assert(s);
    assert(!ngtcp2_conn_open_uni_stream(p->quic,&s->id,NULL));
    s->outgoing=s->assigned=true;s->channel=1;cgp_rx_init(&s->rx);
    s->next=p->streams;p->streams=s;p->tx[1]=s->id;
    size_t length=strlen(json)+8+8192; /* Keep unsent bytes queued after STOP. */cgp_buffer *b=calloc(1,sizeof(*b)+length);assert(b);
    b->length=length;cgp_write_u32(b->bytes,1);cgp_write_u32(b->bytes+4,(uint32_t)strlen(json));
    memcpy(b->bytes+8,json,strlen(json));s->head=s->tail=b;s->next_offset=length;p->queued+=sizeof(*b)+length;
}
static void connected(cgp_peer *p,const char *key,void *user) {
    app *a=user;a->peer=p;++a->connected;assert(cgp_key_valid(key));
    if(a->is_server){
        assert(cgp_bind_private(p,1000,CGP_PRIVATE_JSON,"io.test.json"));
        assert(cgp_bind_private(p,1001,CGP_PRIVATE_RAW,"io.test.raw"));
        assert(cgp_bind_private(p,1002,CGP_PRIVATE_DATAGRAM,"io.test.datagram"));
    }else assert(!cgp_bind_private(p,1000,CGP_PRIVATE_JSON,"io.test.json"));
    assert(cgp_publish(p,0,"{\"type\":\"info\",\"protocol_version\":\"0.3.0\"}"));
    assert(!cgp_publish(p,a->is_server ? 1 : 7,"{\"shot_uuid\":\"invalid\"}"));
    if(a->is_server)inject_bad_shot(p);
    if(a->is_server) {
        char status[10000];
        strcpy(status,"{\"type\":\"device_status\",\"padding\":\"");
        size_t start=strlen(status);memset(status+start,'x',8192);strcpy(status+start+8192,"\"}");
        assert(cgp_publish(p,4,status));
        assert(cgp_publish(p,3,"{\"type\":\"club\",\"club_speed_meters_per_second\":1}"));
        assert(cgp_close_channel(p,3));
        assert(cgp_publish(p,3,"{\"type\":\"club\",\"club_speed_meters_per_second\":2}"));
    }
}
static int accept_test_channel(cgp_peer *p,uint32_t channel,void *user) {
    (void)p;app *a=user;
    if(!a->is_server && channel==4){++a->rejected;return CGP_NOT_INTERESTED;}return 0;
}
static void message(cgp_peer *p,uint32_t channel,const char *json,size_t length,void *user) {
    (void)p;app *a=user;
    if(channel==1000){assert(length==25 && !memcmp(json,"{\"binary_transport\":true}",length));++a->json;}
    else if(channel==3){assert(strstr(json,"second\":2"));++a->clubs;}
    else if(channel==1){assert(cgp_uuid_json_valid(json,length,true));++a->shots;}
    else assert(channel==0);
}
static void raw(cgp_peer *p,int64_t stream,uint32_t channel,const uint8_t *bytes,size_t length,bool fin,void *user) {
    app *a=user;assert(channel==1001);
    assert(!memcmp(bytes,payload+(a->raw_bytes%sizeof(payload)),length));a->raw_bytes+=(unsigned)length;
    if(fin){
        ++a->raw_fin;
        bool outgoing=(stream&1)==(a->is_server ? 1 : 0);
        if(!outgoing){assert(cgp_send_raw_on_stream(p,stream,payload,sizeof(payload)));assert(cgp_finish_stream(p,stream));}
    }
}
static void raw_closed(cgp_peer *p,int64_t stream,void *user){(void)p;(void)stream;++((app*)user)->closed;}
static void datagram(cgp_peer *p,uint32_t channel,uint64_t sequence,const uint8_t *bytes,size_t length,void *user) {
    (void)p;(void)sequence;app *a=user;assert(channel==1002);
    assert(length==sizeof(payload) && !memcmp(bytes,payload,length));++a->datagrams;
}
static void datagram_error(cgp_peer *p,uint32_t channel,uint64_t sequence,int error,void *user) {
    (void)p;app *a=user;assert(channel==1002 && sequence==99 && error==NGTCP2_ERR_INVALID_ARGUMENT);++a->datagram_errors;
}
static void tick(cgp_peer *p,void *user) {
    app *a=user;
    if(cgp_custom_ready(p) && cgp_now_ns()>=a->next_datagram){
        if(cgp_send_datagram(p,1002,payload,sizeof(payload)))a->next_datagram=cgp_now_ns()+UINT64_C(10000000);
    }
}
static void run(bool wrong_key) {
    app server={.is_server=true},client={0};
    cgp_options o={.role=CGP_LAUNCH_MONITOR,.is_server=true,.name="draft3 test",
        .callbacks={.connected=connected,.message=message,.contract=contract,.menu=menu,
        .raw_stream=raw,.raw_closed=raw_closed,.datagram=datagram,.datagram_error=datagram_error,.tick=tick,.accept_channel=accept_test_channel},.user=&server};
    inet_pton(AF_INET,"127.0.0.1",&o.local_ip);
    cgp_engine *server_engine=cgp_create_options(&o);assert(server_engine && server_engine->draft==CGP_DRAFT_3);
    o.role=CGP_SIMULATOR;o.is_server=false;o.user=&client;
    cgp_engine *client_engine=cgp_create_options(&o);assert(client_engine && client_engine->draft==CGP_DRAFT_3);
    struct sockaddr_in address={.sin_family=AF_INET,.sin_addr=o.local_ip,.sin_port=htons(cgp_port(server_engine))};
    assert(!cgp_connect(client_engine,&address,"missing key"));
    assert(!cgp_connect_key(client_engine,&address,"bad key","invalid"));
    char key[65];strcpy(key,cgp_identity(server_engine));
    if(wrong_key)key[0]=key[0]=='0' ? '1' : '0';
    assert(cgp_connect_key(client_engine,&address,"draft3 test",key));
    uint64_t deadline=cgp_now_ns()+(wrong_key ? UINT64_C(2000000000) : UINT64_C(5000000000));
    while(cgp_now_ns()<deadline) {
        cgp_poll(server_engine);cgp_poll(client_engine);usleep(1000);
        if(wrong_key && !client_engine->peers[0].quic)break;
        if(!wrong_key && server.peer)for(cgp_stream *s=server.peer->streams;s;s=s->next) {
            if(!s->awaiting_close)continue;
            if(s->channel==1){server.saw_pending_shot=true;assert(!cgp_publish(server.peer,1,"{\"shot_uuid\":\"017f22e2-79b0-7cc3-98c4-dc0c0c07398f\"}"));}
            if(s->channel==4){server.saw_pending_status=true;assert(!cgp_publish(server.peer,4,"{}"));}
        }
        if(!wrong_key && server.peer && !server.shot_reopened && server.peer->tx[1]==-1) {
            assert(cgp_publish(server.peer,1,"{\"type\":\"shot\",\"shot_uuid\":\"017f22e2-79b0-7cc3-98c4-dc0c0c07398f\"}"));
            server.shot_reopened=true;
        }
        if(!wrong_key && server.raw_fin==2 && client.raw_fin==2 && server.datagrams && client.datagrams &&
            server.json && client.json && server.closed==2 && client.closed==2 && client.shots && client.clubs && server.peer && server.peer->tx[4]==-2)break;
    }
    if(wrong_key){assert(!client_engine->peers[0].quic);assert(client.connected==0 && client.menus==0 && client.json==0 && client.raw_bytes==0);}
    else {
        assert(server.connected==1 && client.connected==1 && server.menus==1 && client.menus==1);
        assert(server.json==1 && client.json==1 && server.raw_bytes==14 && client.raw_bytes==14);
        assert(server.raw_fin==2 && client.raw_fin==2 && server.datagrams && client.datagrams);
        assert(client.shots==1 && server.shot_reopened && client.clubs==1);
        assert(server.saw_pending_shot && server.saw_pending_status);
        assert(client.rejected==1 && server.peer->tx[4]==-2);
        assert(!cgp_publish(server.peer,4,"{\"type\":\"device_status\",\"ready\":false}"));
        assert(!server.peer->failed && !client.peer->failed);
        /* Individual raw cancellation must not permanently decline its channel. */
        int64_t cancelled=cgp_open_raw(server.peer,1001,true);assert(cancelled>=0);
        assert(cgp_cancel_stream(server.peer,cancelled));
        deadline=cgp_now_ns()+UINT64_C(2000000000);
        while(cgp_now_ns()<deadline && server.closed<3){cgp_poll(server_engine);cgp_poll(client_engine);usleep(1000);}
        assert(server.closed==3 && !cgp_find_binding(server.peer,1001)->declined);
        int64_t reopened=cgp_open_raw(server.peer,1001,true);assert(reopened>=0 && reopened!=cancelled);
        assert(cgp_send_raw_on_stream(server.peer,reopened,payload,sizeof(payload)));assert(cgp_finish_stream(server.peer,reopened));
        deadline=cgp_now_ns()+UINT64_C(2000000000);
        while(cgp_now_ns()<deadline && (server.raw_fin<3 || client.raw_fin<3)){cgp_poll(server_engine);cgp_poll(client_engine);usleep(1000);}
        assert(server.raw_fin==3 && client.raw_fin==3);
        /* Public oversize rejection is synchronous; also simulate a queued
         * packet whose size is no longer supported when the transport writes. */
        assert(!cgp_send_datagram(server.peer,1002,payload,cgp_max_datagram_payload(server.peer)+1));
        cgp_binding *b=cgp_find_binding(server.peer,1002);free(b->pending);
        b->pending=calloc(1,1300);assert(b->pending);b->pending_length=1300;b->pending[11]=99;
        deadline=cgp_now_ns()+UINT64_C(2000000000);
        while(cgp_now_ns()<deadline && !server.datagram_errors){cgp_poll(server_engine);cgp_poll(client_engine);usleep(1000);}
        assert(server.datagram_errors==1 && !server.peer->failed && !client.peer->failed);
        assert(cgp_send_datagram(server.peer,1002,payload,sizeof(payload)));
    }
    cgp_destroy(client_engine);cgp_destroy(server_engine);
}
int main(void){run(false);run(true);return 0;}
