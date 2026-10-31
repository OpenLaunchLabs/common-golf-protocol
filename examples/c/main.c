/* SPDX-License-Identifier: Apache-2.0 */
#include "demo_app.h"
#ifdef CGP_HAVE_DNSSD
#include "cgp_discovery.h"
#endif
#include <arpa/inet.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>
static volatile sig_atomic_t stopped;
static void stop(int sig){(void)sig;stopped=1;}
#ifdef CGP_HAVE_DNSSD
typedef struct {const char *name;cgp_engine *engine;} lookup;
static void discovered(const char *name,const struct sockaddr_in *address,const char *key,void *user) {
    lookup *l=user;if(!strcmp(name,l->name))cgp_connect_key(l->engine,address,name,key);
}
#endif
int main(int argc,char **argv) {
    const char *role="device",*name="C example",*identity=".cgp-identities/c-example",*discover=NULL;
    for(int i=1;i<argc;i+=2) {
        if(i+1>=argc)return 2;
        const char *key=argv[i],*value=argv[i+1];
        if(!strcmp(key,"--role"))role=value;
        else if(!strcmp(key,"--name"))name=value;
        else if(!strcmp(key,"--identity"))identity=value;
        else if(!strcmp(key,"--discover"))discover=value;
        else return 2;
    }
    if(strcmp(role,"device") && strcmp(role,"simulator"))return 2;
    cgp_demo_app app={.simulator=!strcmp(role,"simulator")};
    if(app.simulator && !discover){fputs("simulator requires --discover <device name>\n",stderr);return 2;}
    cgp_options options={.role=app.simulator ? CGP_SIMULATOR : CGP_LAUNCH_MONITOR,
        .draft=CGP_DRAFT_3,.listener=!app.simulator,.identity_directory=identity,
        .name=name,.callbacks=cgp_demo_callbacks(),.user=&app};
    cgp_engine *engine=cgp_create_options(&options);if(!engine)return 1;
    setvbuf(stdout,NULL,_IOLBF,0);signal(SIGTERM,stop);signal(SIGINT,stop);
#ifdef CGP_HAVE_DNSSD
    lookup l={.name=discover,.engine=engine};
    cgp_discovery *discovery=cgp_discovery_start_key(app.simulator ? NULL : name,
        app.simulator ? 0 : cgp_port(engine),cgp_identity(engine),"draft-3",app.simulator ? "" : "ball",app.simulator ? discovered : NULL,&l);
    if(!discovery){cgp_destroy(engine);return 1;}
    while(!stopped){cgp_discovery_poll(discovery);cgp_poll(engine);usleep(10000);}
    cgp_discovery_stop(discovery);
#else
    fputs("DNS-SD development library is required for this example\n",stderr);
#endif
    cgp_destroy(engine);return app.connected ? 0 : 1;
}
