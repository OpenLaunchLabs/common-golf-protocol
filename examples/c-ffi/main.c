/* C consumer of the Rust DLL: no Rust types, allocator or Tokio exposed. */
#include "cgp_rust.h"
#include <stdio.h>
#include <string.h>
static unsigned received;
static void CGP_CALL event(void *context, uint32_t channel, const uint8_t *bytes, uint32_t length) {
    (void)context;
    printf("channel %u: %.*s\n", channel, (int)length, (const char *)bytes);
    received++;
}
static int check(int32_t result) {
    if (result < 0) fprintf(stderr, "%s\n", cgp_last_error());
    return result >= 0;
}
int main(int argc, char **argv) {
    CgpEndpoint *endpoint = NULL;
    const char *config = "{\"identity_directory\":\"cgp-ffi-identity\",\"role\":\"simulator\",\"info\":{\"type\":\"info\",\"protocol_version\":\"0.3.0\",\"name\":\"C ABI example\"}}";
    if (cgp_abi_version() != 1 || !check(cgp_create(config, &endpoint))) return 1;
    if (argc == 3) {
        char fingerprint[65];
        unsigned i;
        if (!check(cgp_connect(endpoint, argv[1], argv[2]))) goto fail;
        if (!check(cgp_peer_fingerprint(endpoint, fingerprint, sizeof fingerprint))) goto fail;
        if (strcmp(fingerprint, argv[2])) goto fail;
        for (i = 0; i < 20 && received == 0; ++i)
            if (!check(cgp_poll(endpoint, 500, event, NULL))) goto fail;
        if (!received) goto fail;
        {
            const char *state = "{\"type\":\"simulator_state\"}";
            if (!check(cgp_send(endpoint, 6, (const uint8_t *)state, (uint32_t)strlen(state), 1000))) goto fail;
        }
        received = 0;
        for (i = 0; i < 20 && received == 0; ++i)
            if (!check(cgp_poll(endpoint, 500, event, NULL))) goto fail;
        if (!received) goto fail;
    } else if (argc == 2 && !strcmp(argv[1], "--discover")) {
        if (!check(cgp_discover(endpoint, 1000, event, NULL))) goto fail;
    } else {
        /* Deterministic ABI smoke, including error propagation. */
        if (cgp_send(endpoint, 6, NULL, 0, 100) != -1 || !*cgp_last_error()) goto fail;
    }
    if (!check(cgp_disconnect(endpoint))) goto fail;
    return check(cgp_destroy(endpoint)) ? 0 : 1;
fail:
    cgp_destroy(endpoint);
    return 1;
}
