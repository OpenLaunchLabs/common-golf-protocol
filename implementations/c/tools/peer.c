/* SPDX-License-Identifier: Apache-2.0
 * Explicit addressing is a diagnostic fixture only; applications use DNS-SD. */
#include "cgp.h"
#include "cJSON.h"
#ifdef CGP_HAVE_DNSSD
#include "cgp_discovery.h"
#endif
#include <arpa/inet.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>

typedef struct { FILE *output; bool listener; const char *version; unsigned received; bool connected; cJSON *fixtures; } app;
static volatile sig_atomic_t done;
static void stop(int n) { (void)n; done=1; }
static void connected(cgp_peer *p,const char *fingerprint,void *user) {
    app *a=user; a->connected=true;
    fprintf(a->output,"{\"kind\":\"connected\",\"fingerprint\":\"%s\",\"alpn\":\"%s\"}\n",fingerprint ? fingerprint : "",cgp_alpn(cgp_peer_draft(p))); fflush(a->output);
    char info[256];
    snprintf(info,sizeof(info),"{\"type\":\"info\",\"protocol_version\":\"%s\",\"name\":\"C reference\"}",a->version ? a->version : cgp_protocol_version(cgp_peer_draft(p)));
    cgp_publish(p,0,info);
    cgp_bind_private(p,1000,CGP_PRIVATE_JSON,"io.common-golf.interop-json-v1");
    if (a->fixtures) {
        char draft[2]={(char)('0'+cgp_peer_draft(p)),0};
        cJSON *entries=cJSON_GetObjectItemCaseSensitive(cJSON_GetObjectItemCaseSensitive(cJSON_GetObjectItemCaseSensitive(a->fixtures,"drafts"),draft),a->listener ? "listen" : "connect"),*entry;
        cJSON_ArrayForEach(entry,entries) {
            unsigned ch=(unsigned)cJSON_GetObjectItemCaseSensitive(entry,"channel")->valuedouble;
            char *json=cJSON_PrintUnformatted(cJSON_GetObjectItemCaseSensitive(entry,"message"));
            if (json) {cgp_publish(p,ch,json);free(json);}
        }
    } else
    if (a->listener) {
        char shot[512];
        snprintf(shot,sizeof(shot),"{\"type\":\"shot\",\"shot_number\":1,\"ball_speed_meters_per_second\":60.5,\"vertical_launch_angle_degrees\":12.5,\"horizontal_launch_angle_degrees\":-1.5,%s}",
            cgp_peer_draft(p)==CGP_DRAFT_1 ? "\"reduced_accuracy\":true" : "\"estimated\":[\"ball_speed_meters_per_second\"]");
        cgp_publish(p,1,shot);
        cgp_publish(p,3,"{\"type\":\"club\",\"shot_number\":1,\"club_speed_meters_per_second\":40}");
        cgp_publish(p,4,"{\"type\":\"status\",\"ready\":true,\"battery_percent\":75}");
        cgp_publish(p,5,"{\"type\":\"control\",\"command\":\"left\",\"event\":\"press\"}");
        cgp_publish(p,8,"{\"type\":\"flight\",\"shot_number\":1,\"carry_distance_meters\":200}");
        cgp_publish(p,1000,"{\"type\":\"private\",\"name\":\"io.common-golf.interop-json-v1\",\"message\":\"opaque JSON ☃\"}");
        cgp_publish(p,2,"{\"DeviceID\":\"C reference\",\"Units\":\"Yards\",\"ShotNumber\":2,\"APIversion\":\"1\",\"BallData\":{\"SpinAxis\":0,\"TotalSpin\":2500,\"Speed\":150,\"VLA\":12,\"HLA\":0},\"ShotDataOptions\":{\"ContainsBallData\":true,\"ContainsClubData\":false}}");
    } else {
        cgp_publish(p,6,"{\"type\":\"state\",\"ready\":true,\"mode\":\"range\"}");
        cgp_publish(p,7,"{\"type\":\"result\",\"shot_number\":1,\"accepted\":true,\"carry_distance_meters\":200}");
    }
}
static void message(cgp_peer *p,uint32_t ch,const char *json,size_t length,void *user) {
    app *a=user;
    fprintf(a->output,"{\"channel\":%u,\"message\":%.*s}\n",ch,(int)length,json); fflush(a->output); ++a->received;
    if (ch==2 && !a->listener) cgp_publish(p,2,"{\"Code\":200,\"Message\":\"Shot received\"}");
}
#ifdef CGP_HAVE_DNSSD
static void discovered(const char *name,const struct sockaddr_in *address,void *user) {
    app *a=user;
    fputs("{\"kind\":\"discovered\",\"name\":\"",a->output);
    for (const unsigned char *p=(const unsigned char *)name;*p;++p) {
        if (*p=='"' || *p=='\\') fputc('\\',a->output);
        if (*p<32) fprintf(a->output,"\\u%04x",*p); else fputc(*p,a->output);
    }
    fprintf(a->output,"\",\"port\":%u}\n",ntohs(address->sin_port)); fflush(a->output);
}
#endif
int main(int argc,char **argv) {
    int draft=2,port=0; double duration=3; const char *role="listen",*host="127.0.0.1",*output=NULL,*identity=NULL,*version=NULL,*advertise=NULL,*fixtures=NULL;
    for (int i=1;i<argc;i+=2) {
        if (i+1>=argc) return 2;
        const char *key=argv[i],*value=argv[i+1];
        if (!strcmp(key,"--draft")) draft=atoi(value);
        else if (!strcmp(key,"--role")) role=value;
        else if (!strcmp(key,"--port")) port=atoi(value);
        else if (!strcmp(key,"--host")) host=value;
        else if (!strcmp(key,"--output")) output=value;
        else if (!strcmp(key,"--identity")) identity=value;
        else if (!strcmp(key,"--version")) version=value;
        else if (!strcmp(key,"--advertise")) advertise=value;
        else if (!strcmp(key,"--fixtures")) fixtures=value;
        else if (!strcmp(key,"--duration")) duration=atof(value);
        else return 2;
    }
    if (draft<1 || draft>2 || port<0 || port>65535 || duration<=0 || (strcmp(role,"listen") && strcmp(role,"connect") && strcmp(role,"discover"))) return 2;
    app a={.listener=!strcmp(role,"listen"),.version=version};
    if (fixtures) {
        FILE *f=fopen(fixtures,"rb");if(!f)return 1;fseek(f,0,SEEK_END);long size=ftell(f);rewind(f);
        char *text=malloc((size_t)size+1);if(!text)return 1;size_t n=fread(text,1,(size_t)size,f);fclose(f);text[n]=0;
        a.fixtures=cJSON_Parse(text);free(text);if(!a.fixtures)return 1;
    }
    a.output=output ? fopen(output,"w") : stdout; if (!a.output) return 1;
#ifdef CGP_HAVE_DNSSD
    cgp_discovery *discovery=NULL;
    if (!strcmp(role,"discover")) {
        discovery=cgp_discovery_start(NULL,0,discovered,&a); if (!discovery) return 1;
        uint64_t end=cgp_now_ns()+(uint64_t)(duration*1e9);
        while (cgp_now_ns()<end) {cgp_discovery_poll(discovery);usleep(10000);}
        cgp_discovery_stop(discovery); if(output) fclose(a.output); return 0;
    }
#else
    if (!strcmp(role,"discover") || advertise) {fputs("DNS-SD development library required\n",stderr);return 2;}
#endif
    cgp_options options={.role=a.listener ? CGP_LAUNCH_MONITOR : CGP_SIMULATOR,.draft=(cgp_draft)draft,.listener=a.listener,
        .name="C reference",.identity_directory=identity,.callbacks={.connected=connected,.message=message},.user=&a};
    inet_pton(AF_INET,"127.0.0.1",&options.local_ip);
    cgp_engine *engine=cgp_create_options(&options); if (!engine) return 1;
    setvbuf(stdout,NULL,_IOLBF,0); signal(SIGTERM,stop); signal(SIGINT,stop);
#ifdef CGP_HAVE_DNSSD
    if (advertise) {
        discovery=cgp_discovery_start(advertise,cgp_port(engine),NULL,NULL);
        if (!discovery) return 1;
    }
#endif
    if (a.listener) printf("READY %u\n",cgp_port(engine));
    else {
        struct sockaddr_in remote={.sin_family=AF_INET,.sin_port=htons((uint16_t)port)};
        if (!port || inet_pton(AF_INET,host,&remote.sin_addr)!=1 || !cgp_connect(engine,&remote,"fixture")) return 1;
    }
    uint64_t end=cgp_now_ns()+(uint64_t)(duration*1e9);
    while (!done && cgp_now_ns()<end) {
        cgp_poll(engine);
#ifdef CGP_HAVE_DNSSD
        cgp_discovery_poll(discovery);
#endif
        usleep(10000);
    }
#ifdef CGP_HAVE_DNSSD
    cgp_discovery_stop(discovery);
#endif
    cgp_destroy(engine); if (output) fclose(a.output);
    cJSON_Delete(a.fixtures);return a.connected && a.received ? 0 : 1;
}
