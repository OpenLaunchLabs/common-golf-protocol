/* SPDX-License-Identifier: Apache-2.0 */
#include "internal.h"
#include <ctype.h>
#include <stdlib.h>
#include <string.h>

static const char *space(const char *p,const char *end) {
    while(p<end && (*p==' '||*p=='\t'||*p=='\r'||*p=='\n'))++p;
    return p;
}
/* Input has passed the strict JSON grammar guard. Locate encoded value spans
 * without changing numeric spelling or mistaking an escaped string for a key. */
static const char *skip(const char *p,const char *end) {
    p=space(p,end); if(p==end)return end;
    if(*p=='"') {++p;while(p<end){if(*p=='\\'){p+=2;continue;}if(*p++=='"')break;}return p;}
    if(*p=='{'||*p=='[') {
        unsigned depth=0;
        do { if(*p=='"'){p=skip(p,end);continue;} if(*p=='{'||*p=='[')++depth;
             if(*p=='}'||*p==']')--depth; ++p; }while(p<end && depth);
        return p;
    }
    while(p<end && *p!=',' && *p!='}' && *p!=']' && !isspace((unsigned char)*p))++p;
    return p;
}
static bool escaped_nul(const char *p,const char *end) {
    for(;p<end;++p)if(*p=='\\' && ++p<end) {
        if(*p=='u' && end-p>=5 && !memcmp(p+1,"0000",4))return true;
    }
    return false;
}
static int field(const char *p,const char *end,const char *name,const char **value,const char **last) {
    int count=0;p=space(p,end);if(p==end || *p++!='{')return -1;
    while((p=space(p,end))<end && *p!='}') {
        const char *key_end=skip(p,end);
        bool nul=escaped_nul(p,key_end);
        cJSON *key=cJSON_ParseWithLength(p,(size_t)(key_end-p));
        if(!cJSON_IsString(key)){cJSON_Delete(key);return -1;}
        p=space(key_end,end);if(p==end || *p++!=':'){cJSON_Delete(key);return -1;}
        p=space(p,end);const char *q=skip(p,end);
        if(!nul && !strcmp(key->valuestring,name)){++count;*value=p;*last=q;}
        cJSON_Delete(key);p=space(q,end);if(p<end && *p==',')++p;else break;
    }
    return count>1 ? -1 : count;
}
bool cgp_uuid_json_valid(const char *json,size_t length,bool required) {
    const char *value=NULL,*end=NULL;
    int found=field(json,json+length,"shot_uuid",&value,&end);
    if(found<0)return false;
    if(!found)return !required;
    if(escaped_nul(value,end))return false;
    cJSON *uuid=cJSON_ParseWithLength(value,(size_t)(end-value));
    bool valid=cJSON_IsString(uuid) && strlen(uuid->valuestring)==36;
    if(valid) {
        const char *s=uuid->valuestring;
        for(unsigned i=0;i<36 && valid;++i) {
            bool separator=i==8||i==13||i==18||i==23;
            valid=separator ? s[i]=='-' : (s[i]>='0'&&s[i]<='9')||(s[i]>='a'&&s[i]<='f');
        }
        valid=valid && s[14]=='7' && strchr("89ab",s[19]);
    }
    cJSON_Delete(uuid);return valid;
}
static bool identifier(const char *s) {
    if(!s || !*s || strlen(s)>128)return false;
    for(;*s;++s)if(!((*s>='a'&&*s<='z')||(*s>='0'&&*s<='9')||*s=='.'||*s=='_'||*s=='-'))return false;
    return true;
}
bool cgp_menu_receive(cgp_peer *p,const char *json,size_t length) {
    if(p->engine->draft!=CGP_DRAFT_3 || p->engine->is_server)return true;
    const char *value=NULL,*end=NULL;
    cgp_binding parsed[CGP_MAX_BINDINGS]={0};unsigned count=0;
    bool valid=true;
    int found=field(json,json+length,"contracts",&value,&end);
    if(found<0)valid=false;
    if(found>0) {
        if(*value++!='[')valid=false;
        while(valid && (value=space(value,end))<end && *value!=']') {
            if(count==CGP_MAX_BINDINGS){valid=false;break;}
            const char *next=skip(value,end),*n=NULL,*nend=NULL,*id=NULL,*idend=NULL;
            if(field(value,next,"channel",&n,&nend)!=1 || field(value,next,"contract",&id,&idend)!=1){valid=false;break;}
            uint64_t channel=0;
            if(n==nend)valid=false;
            for(const char *c=n;valid && c<nend;++c) {
                if(*c<'0'||*c>'9' || channel>UINT32_MAX/10ULL){valid=false;break;}
                channel=channel*10+(unsigned)(*c-'0');
                if(channel>UINT32_MAX)valid=false;
            }
            if(escaped_nul(id,idend))valid=false;
            cJSON *name=cJSON_ParseWithLength(id,(size_t)(idend-id));
            if(!valid || channel<1000 || !cJSON_IsString(name) || !identifier(name->valuestring)){cJSON_Delete(name);valid=false;break;}
            for(unsigned i=0;i<count;++i)if(parsed[i].channel==channel)valid=false;
            parsed[count].channel=(uint32_t)channel;
            strcpy(parsed[count].contract,name->valuestring);cJSON_Delete(name);
            ++count;value=space(next,end);if(value<end && *value==',')++value;else break;
        }
    }
    if(p->menu_seen) {
        if(!p->menu_valid || !valid)return false;
        unsigned old_count=0;
        for(unsigned i=0;i<CGP_MAX_BINDINGS;++i)if(p->bindings[i].channel)++old_count;
        if(count!=old_count)return false;
        for(unsigned i=0;i<count;++i)if(!cgp_private_matches(p,parsed[i].channel,parsed[i].contract))return false;
        return true;
    }
    p->menu_seen=true;p->menu_valid=valid;
    if(valid) {
        for(unsigned i=0;i<count;++i) {
            int kind=p->engine->callbacks.contract ? p->engine->callbacks.contract(p,parsed[i].contract,p->engine->user) : -1;
            parsed[i].kind=(cgp_private_kind)(kind>=0 && kind<=CGP_PRIVATE_RAW_DATAGRAM ? kind : 255);
        }
        memcpy(p->bindings,parsed,sizeof(parsed));
    }
    if(p->engine->callbacks.menu)p->engine->callbacks.menu(p,valid,p->engine->user);
    return valid;
}
char *cgp_menu_info(cgp_peer *p,const char *json) {
    cJSON *info=cJSON_Parse(json);
    if(!info)return NULL;
    if(p->engine->is_server) {
        cJSON *entries=cJSON_CreateArray();
        for(unsigned i=0;i<CGP_MAX_BINDINGS;++i)if(p->bindings[i].channel) {
            cJSON *entry=cJSON_CreateObject();cJSON_AddNumberToObject(entry,"channel",p->bindings[i].channel);
            cJSON_AddStringToObject(entry,"contract",p->bindings[i].contract);cJSON_AddItemToArray(entries,entry);
        }
        cJSON_DeleteItemFromObjectCaseSensitive(info,"contracts");cJSON_AddItemToObject(info,"contracts",entries);
    }
    char *result=cJSON_PrintUnformatted(info);cJSON_Delete(info);return result;
}
