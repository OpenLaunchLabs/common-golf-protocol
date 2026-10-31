/* SPDX-License-Identifier: Apache-2.0 */
#include "internal.h"
#include <assert.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

static int contract(cgp_peer *peer,const char *id,void *user) {
    (void)peer;(void)user;
    return !strcmp(id,"com.example.json-v1") ? CGP_PRIVATE_JSON : -1;
}
static bool receive(cgp_peer *p,const char *json){assert(cgp_json_valid(json,strlen(json)));return cgp_menu_receive(p,json,strlen(json));}
int main(void) {
    assert(cgp_uuid_json_valid("{}",2,false));assert(!cgp_uuid_json_valid("{}",2,true));
    const char *bad_uuid[]={"null","123","\"017f22e2-79b0-4cc3-98c4-dc0c0c07398f\"","\"017F22e2-79b0-7cc3-98c4-dc0c0c07398f\"","\"017f22e2-79b0-7cc3-78c4-dc0c0c07398f\"","\"017f22e2-79b0-7cc3-98c4-dc0c0c07398f\\u0000\""};
    for(unsigned i=0;i<sizeof(bad_uuid)/sizeof(*bad_uuid);++i){char json[256];snprintf(json,sizeof(json),"{\"shot_uuid\":%s}",bad_uuid[i]);assert(!cgp_uuid_json_valid(json,strlen(json),false));}
    const char *good_uuid="{\"shot_uuid\":\"017f22e2-79b0-7cc3-98c4-dc0c0c07398f\"}";
    assert(cgp_uuid_json_valid(good_uuid,strlen(good_uuid),true));
    cgp_engine e={.draft=CGP_DRAFT_3,.callbacks={.contract=contract}};
    cgp_peer p={.engine=&e};
    const char *first="{\"type\":\"info\",\"contracts\":[{\"channel\":1000,\"contract\":\"com.example.json-v1\"},{\"channel\":1001,\"contract\":\"com.example.unknown-v1\"}]}";
    assert(receive(&p,first));assert(cgp_custom_ready(&p));
    assert(cgp_private_matches(&p,1000,"com.example.json-v1"));
    assert(cgp_find_binding(&p,1001)->kind>CGP_PRIVATE_RAW_DATAGRAM);
    assert(receive(&p,"{\"contracts\":[{\"contract\":\"com.example.unknown-v1\",\"channel\":1001},{\"contract\":\"com.example.json-v1\",\"channel\":1000}]}"));
    assert(!receive(&p,"{\"contracts\":[]}"));assert(cgp_private_matches(&p,1000,"com.example.json-v1"));
    const char *invalid[]={
        "{\"contracts\":[{\"channel\":1000.0,\"contract\":\"com.example.json-v1\"}]}",
        "{\"contracts\":[{\"channel\":1e3,\"contract\":\"com.example.json-v1\"}]}",
        "{\"contracts\":[{\"channel\":\"1000\",\"contract\":\"com.example.json-v1\"}]}",
        "{\"contracts\":[{\"channel\":2,\"contract\":\"com.example.json-v1\"}]}",
        "{\"contracts\":[{\"channel\":4294967296,\"contract\":\"com.example.json-v1\"}]}",
        "{\"contracts\":[{\"channel\":1000,\"contract\":\"Com.example.json-v1\"}]}",
        "{\"contracts\":[{\"channel\":1000,\"contract\":\"com.example.json-v1\\u0000tail\"}]}",
        "{\"contracts\":[{\"channel\":1000,\"contract\":null}]}",
        "{\"contracts\":[{\"channel\":1000,\"contract\":\"com.example.json-v1\"},{\"channel\":1000,\"contract\":\"other\"}]}",
        "{\"contracts\":[],\"contracts\":[]}",
        "{\"contracts\":[{\"channel\":1000,\"channel\":1001,\"contract\":\"other\"}]}"
    };
    for(unsigned i=0;i<sizeof(invalid)/sizeof(*invalid);++i) {
        cgp_peer bad={.engine=&e};assert(!receive(&bad,invalid[i]));assert(!cgp_custom_ready(&bad));
        assert(!receive(&bad,first));assert(!cgp_channel_contract(&bad,1000));
    }
    cgp_peer escaped={.engine=&e};assert(receive(&escaped,"{\"contracts\":[{\"chan\\u006eel\":4294967295,\"contract\":\"com.example.j\\u0073on-v1\"}]}"));
    assert(cgp_private_matches(&escaped,UINT32_MAX,"com.example.json-v1"));
    cgp_peer nul={.engine=&e};assert(receive(&nul,"{\"contracts\\u0000suffix\":123}"));
    assert(!cgp_channel_contract(&nul,1000));
    e.listener=true;cgp_peer server={.engine=&e};assert(cgp_bind_private(&server,1000,CGP_PRIVATE_JSON,"com.example.json-v1"));
    char *json=cgp_menu_info(&server,"{\"type\":\"info\",\"name\":\"test\"}");assert(json && strstr(json,"contracts"));free(json);
    server.info_sent=true;assert(!cgp_bind_private(&server,1001,CGP_PRIVATE_RAW,"com.example.raw-v1"));
    char uuid[37];assert(cgp_shot_uuid(1,uuid));assert(strlen(uuid)==36 && uuid[14]=='7' && strchr("89ab",uuid[19]));assert(!cgp_shot_uuid(UINT64_MAX,uuid));
    assert(!cgp_key_valid(NULL));assert(!cgp_key_valid("abcdef"));
    assert(cgp_key_valid("0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"));
    return 0;
}
