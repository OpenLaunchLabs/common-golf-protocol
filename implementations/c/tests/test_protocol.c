#include "internal.h"
#include <assert.h>
#include <string.h>
int main(void) {
    assert(!strcmp(cgp_alpn(CGP_DRAFT_1),"common-golf/draft-1"));
    assert(!strcmp(cgp_alpn(CGP_DRAFT_2),"common-golf/draft-2"));
    assert(!strcmp(cgp_protocol_version(CGP_DRAFT_2),"0.2.0"));
    assert(!cgp_alpn((cgp_draft)0));
    assert(!cgp_alpn((cgp_draft)99));
    cgp_engine e={.draft=CGP_DRAFT_2}; cgp_peer p={.engine=&e},other={.engine=&e};
    assert(cgp_bind_private(&p,1001,CGP_PRIVATE_JSON,"io.openlaunch.opengolfcoach"));
    assert(cgp_private_matches(&p,1001,"io.openlaunch.opengolfcoach"));
    assert(!cgp_private_matches(&other,1001,"io.openlaunch.opengolfcoach"));
    assert(!cgp_private_matches(&p,1001,"io.other"));
    assert(!cgp_bind_private(&p,1001,CGP_PRIVATE_JSON,"io.other"));
    assert(!cgp_bind_private(&p,9,CGP_PRIVATE_JSON,"io.test"));
    assert(!cgp_bind_private(&p,1002,CGP_PRIVATE_JSON,""));
    assert(cgp_bind_private(&p,UINT32_MAX,CGP_PRIVATE_JSON,"io.test"));
    assert(cgp_private_matches(&p,UINT32_MAX,"io.test"));
    return 0;
}
