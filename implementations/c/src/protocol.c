/* SPDX-License-Identifier: Apache-2.0 */
#include "internal.h"
#include <string.h>

const char *cgp_alpn(cgp_draft draft) {
    return draft == CGP_DRAFT_1 ? "common-golf/draft-1" :
           draft == CGP_DRAFT_2 ? "common-golf/draft-2" : NULL;
}
const char *cgp_protocol_version(cgp_draft draft) {
    return draft == CGP_DRAFT_1 ? "0.1.1" : draft == CGP_DRAFT_2 ? "0.2.0" : NULL;
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
    if (!p || channel<1000 || !cgp_contract_valid(contract) || kind!=CGP_PRIVATE_JSON) return false;
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
