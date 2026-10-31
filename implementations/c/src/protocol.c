/* SPDX-License-Identifier: Apache-2.0 */
#include "internal.h"
#include <string.h>

const char *cgp_alpn(cgp_draft draft) {
    return draft == CGP_DRAFT_1 ? "common-golf/draft-1" :
           draft == CGP_DRAFT_2 ? "common-golf/draft-2" : draft == CGP_DRAFT_3 ? "common-golf/draft-3" : NULL;
}
const char *cgp_protocol_version(cgp_draft draft) {
    return draft == CGP_DRAFT_1 ? "0.1.1" : draft == CGP_DRAFT_2 ? "0.2.0" : draft == CGP_DRAFT_3 ? "0.3.0" : NULL;
}
const char *cgp_peer_tier(const cgp_peer *p) { return p->tier; }
const char *cgp_peer_identity(const cgp_peer *p) { return p->fingerprint[0] ? p->fingerprint : NULL; }
cgp_draft cgp_peer_draft(const cgp_peer *p) { return p->engine->draft; }
void *cgp_peer_user_data(const cgp_peer *p) { return p->user_data; }
void cgp_peer_set_user_data(cgp_peer *p, void *data) { p->user_data = data; }

cgp_binding *cgp_find_binding(cgp_peer *p, uint32_t channel) {
    for (unsigned i=0;i<CGP_MAX_BINDINGS;++i) if (p->bindings[i].channel == channel && channel >= 1000) return &p->bindings[i];
    return NULL;
}

static bool cgp_contract_valid(const char *id) {
    if (!id || !*id || strlen(id)>256) return false;
    for (const unsigned char *c=(const unsigned char *)id;*c;++c) if (*c<33 || *c>126) return false;
    return true;
}
bool cgp_bind_private(cgp_peer *p, uint32_t channel, cgp_private_kind kind, const char *contract) {
    if (!p || channel<1000 || !cgp_contract_valid(contract)) return false;
    if (p->engine->draft == CGP_DRAFT_3) {
        if (!p->engine->listener || p->info_sent || strlen(contract)>128 || kind>CGP_PRIVATE_RAW_DATAGRAM) return false;
        for (const char *c=contract;*c;++c) if (!((*c>='a' && *c<='z') || (*c>='0' && *c<='9') || *c=='.' || *c=='-' || *c=='_')) return false;
    } else if (kind!=CGP_PRIVATE_JSON) return false;
    cgp_binding *b=cgp_find_binding(p,channel);
    if (b) return b->kind==kind && !strcmp(b->contract,contract);
    for (unsigned i=0;i<CGP_MAX_BINDINGS;++i) if (!p->bindings[i].channel) {
        b=&p->bindings[i];b->channel=channel;b->kind=kind;strcpy(b->contract,contract);return true;
    }
    return false;
}
const char *cgp_channel_contract(const cgp_peer *p,uint32_t channel) {
    if (!p || channel<1000) return NULL;
    for (unsigned i=0;i<CGP_MAX_BINDINGS;++i) if (p->bindings[i].channel==channel) return p->bindings[i].contract;
    return NULL;
}
bool cgp_private_matches(const cgp_peer *p,uint32_t channel,const char *contract) {
    const char *bound=cgp_channel_contract(p,channel);return bound && contract && !strcmp(bound,contract);
}

bool cgp_custom_ready(const cgp_peer *p) {
    return p && (p->engine->draft != CGP_DRAFT_3 || (p->menu_seen && p->menu_valid));
}
bool cgp_open_connect(const cgp_peer *p, uint32_t channel) {
    return p->engine->draft == CGP_DRAFT_3 ? cgp_private_matches(p,channel,"io.common-golf.open-connect-v1") : channel==2;
}
bool cgp_key_valid(const char *key) {
    if (!key || strlen(key)!=64) return false;
    for (const char *c=key;*c;++c) if (!((*c>='0' && *c<='9') || (*c>='a' && *c<='f'))) return false;
    return true;
}
bool cgp_shot_uuid(uint64_t ms, char out[37]) {
    if (!out || ms>0xffffffffffffULL) return false;
    uint8_t bytes[16];cgp_random(bytes,sizeof(bytes));
    for (unsigned i=0;i<6;++i) bytes[i]=(uint8_t)(ms>>(40-8*i));
    bytes[6]=(bytes[6]&15)|0x70;bytes[8]=(bytes[8]&63)|0x80;
    static const char hex[]="0123456789abcdef";unsigned n=0;
    for (unsigned i=0;i<16;++i) {if (i==4||i==6||i==8||i==10) out[n++]='-';out[n++]=hex[bytes[i]>>4];out[n++]=hex[bytes[i]&15];}out[n]=0;return true;
}
