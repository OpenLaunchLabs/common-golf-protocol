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
typedef enum { CGP_DRAFT_1 = 1, CGP_DRAFT_2 = 2, CGP_DRAFT_3 = 3 } cgp_draft;
typedef struct cgp_engine cgp_engine;
typedef struct cgp_peer cgp_peer;
typedef enum { CGP_PRIVATE_JSON, CGP_PRIVATE_RAW, CGP_PRIVATE_DATAGRAM, CGP_PRIVATE_RAW_DATAGRAM } cgp_private_kind;
/* Callbacks execute on the polling thread. JSON is borrowed until return.
 * The connected callback must publish info before opening another channel.
 * received messages may precede peer info because streams are independent. */
typedef struct {
    void (*connected)(cgp_peer *, const char *fingerprint, void *user);
    void (*message)(cgp_peer *, uint32_t channel, const char *json, size_t length, void *user);
    void (*raw_stream)(cgp_peer *, int64_t stream, uint32_t, const uint8_t *, size_t, bool fin, void *user);
    /* Both stream directions are closed; connection teardown uses disconnected. */
    void (*raw_closed)(cgp_peer *, int64_t stream, void *user);
    void (*datagram)(cgp_peer *, uint32_t, uint64_t sequence, const uint8_t *, size_t, void *user);
    /* An asynchronously queued datagram was rejected (negative ngtcp2 code).
     * Oversize rejects leave the connection open; synchronous rejects return false. */
    void (*datagram_error)(cgp_peer *, uint32_t, uint64_t sequence, int error, void *user);
    /* Exact local contract definition. Return its kind, or -1 if unsupported. */
    int (*contract)(cgp_peer *, const char *identifier, void *user);
    void (*menu)(cgp_peer *, bool available, void *user);
    void (*tick)(cgp_peer *, void *user);
    void (*disconnected)(cgp_peer *, void *user);
    /* Optional policy: 0 accepts, 1 declines. Standard ownership always applies. */
    int (*accept_channel)(cgp_peer *, uint32_t channel, void *user);
} cgp_callbacks;
typedef struct {
    cgp_role role; /* Application role, independent of the QUIC connection role. */
    cgp_draft draft; /* Zero/omitted selects draft 3; 1/2 require explicit opt-in. */
    bool is_server; /* true accepts QUIC connections; false initiates them. */
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
/* Draft 3 requires the discovered 64-lowercase-hex key; checked before info. */
bool cgp_connect_key(cgp_engine *, const struct sockaddr_in *, const char *, const char *expected_key);
bool cgp_key_valid(const char *);
/* Generate a canonical UUIDv7 from UTC milliseconds (application supplies time). */
bool cgp_shot_uuid(uint64_t unix_milliseconds, char out[37]);
void cgp_poll(cgp_engine *); /* call every 10 ms */
unsigned cgp_connections(const cgp_engine *);
void cgp_statistics(const cgp_engine *);
/* Queues a strict UTF-8 JSON object. Channels use one stream per direction.
 * Open Connect replies use its device-opened bidirectional stream (channel 2
 * on drafts 1/2, a server-menu channel on draft 3).
 * false means malformed/forbidden or backpressure: retry later as appropriate.
 * A declined channel returns false; the connection remains usable. */
bool cgp_publish(cgp_peer *, unsigned channel, const char *json);
/* End/reset a stream before reopening it. Counters are application-owned. */
bool cgp_close_channel(cgp_peer *, uint32_t channel);
/* Queue FIN after all buffered data. No reset or data loss. */
bool cgp_finish_channel(cgp_peer *, uint32_t channel);
/* Drafts 1/2: static JSON binding. Draft 3: server-only menu entry, before info.
 * The first server info automatically advertises all configured entries. */
bool cgp_bind_private(cgp_peer *, uint32_t, cgp_private_kind, const char *contract);
bool cgp_custom_ready(const cgp_peer *);
int64_t cgp_open_raw(cgp_peer *, uint32_t channel, bool bidirectional);
bool cgp_send_raw_on_stream(cgp_peer *, int64_t stream, const uint8_t *, size_t);
bool cgp_finish_stream(cgp_peer *, int64_t stream);
bool cgp_cancel_stream(cgp_peer *, int64_t stream);
bool cgp_send_datagram(cgp_peer *, uint32_t, const uint8_t *, size_t);
size_t cgp_max_datagram_payload(const cgp_peer *);
bool cgp_private_matches(const cgp_peer *, uint32_t, const char *contract);
const char *cgp_channel_contract(const cgp_peer *, uint32_t);
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
