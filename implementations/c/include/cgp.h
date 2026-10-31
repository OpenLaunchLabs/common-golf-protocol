/* SPDX-License-Identifier: Apache-2.0 */
#ifndef CGP_H
#define CGP_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/socket.h>
#include <netinet/in.h>

#define CGP_SERVICE "_common-golf._udp"
#define CGP_NOT_INTERESTED 1
#define CGP_MALFORMED 2

typedef enum { CGP_LAUNCH_MONITOR, CGP_CONTROL_BOX, CGP_SIMULATOR } cgp_role;
typedef enum { CGP_DRAFT_1 = 1, CGP_DRAFT_2 = 2 } cgp_draft;
typedef struct cgp_engine cgp_engine;
typedef struct cgp_peer cgp_peer;
typedef enum { CGP_PRIVATE_JSON } cgp_private_kind;
/* Callbacks execute on the polling thread. JSON is borrowed until return.
 * The connected callback must publish info before opening another channel.
 * received messages may precede peer info because streams are independent. */
typedef struct {
    void (*connected)(cgp_peer *, const char *fingerprint, void *user);
    void (*message)(cgp_peer *, uint32_t channel, const char *json, size_t length, void *user);
    void (*tick)(cgp_peer *, void *user);
    void (*disconnected)(cgp_peer *, void *user);
    /* Optional policy: 0 accepts, 1 declines. Standard ownership always applies. */
    int (*accept_channel)(cgp_peer *, uint32_t channel, void *user);
    /* Return internal/developer/maker. Unknown and absent identities default maker. */
    const char *(*trust_tier)(const char *fingerprint, void *user);
} cgp_callbacks;
typedef struct {
    cgp_role role;
    cgp_draft draft;
    bool listener;
    const char *name;
    struct in_addr local_ip;
    /* Stable identity directory on POSIX; NULL creates an ephemeral test key.
     * ESP32 uses NVS and ignores this value. */
    const char *identity_directory;
    cgp_callbacks callbacks;
    void *user;
} cgp_options;

/* Single-threaded nonblocking engine; accepts multiple independent peers. */
cgp_engine *cgp_create_options(const cgp_options *options);
void cgp_destroy(cgp_engine *engine);
uint16_t cgp_port(const cgp_engine *engine);
const char *cgp_identity(const cgp_engine *engine);
/* Address supplied by DNS-SD (or explicit diagnostic fixtures only). */
bool cgp_connect(cgp_engine *, const struct sockaddr_in *, const char *discovered_name);
void cgp_poll(cgp_engine *); /* call every 10 ms */
unsigned cgp_connections(const cgp_engine *);
void cgp_statistics(const cgp_engine *);
/* Queues a strict UTF-8 JSON object. Channels use one stream per direction.
 * Channel 2 automatically replies on the device-opened bidirectional stream.
 * false means malformed/forbidden or backpressure: retry later as appropriate.
 * A declined channel returns false; the connection remains usable. */
bool cgp_publish(cgp_peer *, unsigned channel, const char *json);
/* End/reset a stream before reopening it. Counters are application-owned. */
bool cgp_close_channel(cgp_peer *, uint32_t channel);
/* Queue FIN after all buffered data. No reset or data loss. */
bool cgp_finish_channel(cgp_peer *, uint32_t channel);
/* Connection-local private JSON arrangement, configured by both endpoints. */
bool cgp_bind_private(cgp_peer *, uint32_t, cgp_private_kind, const char *contract);
bool cgp_private_matches(const cgp_peer *, uint32_t, const char *contract);
const char *cgp_channel_contract(const cgp_peer *, uint32_t);
const char *cgp_peer_tier(const cgp_peer *);
const char *cgp_peer_identity(const cgp_peer *);
cgp_draft cgp_peer_draft(const cgp_peer *);
void *cgp_peer_user_data(const cgp_peer *);
void cgp_peer_set_user_data(cgp_peer *, void *);
const char *cgp_alpn(cgp_draft);
const char *cgp_protocol_version(cgp_draft);
/* Sender checks draft-specific renamed fields. Receiver ignores unknown fields. */
bool cgp_json_valid(const char *, size_t);
/* Platform functions supplied by application (POSIX helper target provided). */
uint64_t cgp_now_ns(void);
void cgp_random(void *, size_t);
void cgp_log(const char *, ...) __attribute__((format(printf, 1, 2)));
#endif
