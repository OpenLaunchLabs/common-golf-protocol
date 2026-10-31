/* SPDX-License-Identifier: Apache-2.0 */
#include "demo_app.h"
#include <stdio.h>
static void connected(cgp_peer *peer,const char *fingerprint,void *user) {
    cgp_demo_app *app=user;app->connected=true;
    cgp_log("connected fingerprint=%s",fingerprint ? fingerprint : "none");
    cgp_publish(peer,0,"{\"type\":\"info\",\"protocol_version\":\"0.2.0\",\"name\":\"C example\"}");
    if(app->simulator)cgp_publish(peer,6,"{\"type\":\"state\",\"ready\":true,\"mode\":\"range\"}");
    else cgp_publish(peer,1,"{\"type\":\"shot\",\"shot_number\":1,\"ball_speed_meters_per_second\":60,\"vertical_launch_angle_degrees\":12,\"horizontal_launch_angle_degrees\":0}");
}
static void message(cgp_peer *peer,uint32_t channel,const char *json,size_t length,void *user) {
    (void)peer;cgp_demo_app *app=user;++app->received;
    printf("channel=%u %.*s\n",(unsigned)channel,(int)length,json);
}
static int accept_channel(cgp_peer *peer,uint32_t channel,void *user) {
    (void)peer;(void)user;return channel==2 || channel>=1000 ? CGP_NOT_INTERESTED : 0;
}
cgp_callbacks cgp_demo_callbacks(void) {
    return (cgp_callbacks){.connected=connected,.message=message,.accept_channel=accept_channel};
}
