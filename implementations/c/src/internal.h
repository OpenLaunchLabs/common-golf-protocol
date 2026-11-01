/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include "cgp.h"
#include "framing.h"
#include "cJSON.h"
#include <ngtcp2/ngtcp2.h>
#include <ngtcp2/ngtcp2_crypto.h>
#include <wolfssl/ssl.h>

#ifdef ESP_PLATFORM
#include "sdkconfig.h"
#else
#ifndef CONFIG_CGP_MAX_CONNECTIONS
#define CONFIG_CGP_MAX_CONNECTIONS 8
#endif
#endif

#define CGP_CHANNELS 9
#define CGP_MAX_RAW_STREAMS 16
#define CGP_TX_LIMIT (4 * 65543)
/* Streams the remote may open (QUIC transport parameters), plus room for our
 * own. Tracking covers every stream a well-behaved peer can open, so refusals
 * only happen when our own sends exceed CGP_LOCAL_STREAMS. Streams are
 * allocated on demand; the cap costs nothing until it is used. */
#define CGP_REMOTE_STREAMS_UNI 24
#define CGP_REMOTE_STREAMS_BIDI 24
#define CGP_LOCAL_STREAMS 16
#define CGP_MAX_STREAMS (CGP_REMOTE_STREAMS_UNI + CGP_REMOTE_STREAMS_BIDI + CGP_LOCAL_STREAMS)

typedef struct cgp_buffer {
    struct cgp_buffer *next;
    uint64_t offset;
    size_t length, sent;
    uint8_t bytes[];
} cgp_buffer;

typedef struct cgp_stream {
    struct cgp_stream *next;
    int64_t id;
    bool outgoing, stopped, awaiting_close, fin_requested, fin_sent, assigned;
    unsigned channel;
    cgp_rx rx;
    cgp_buffer *head, *tail;
    uint64_t next_offset;
    uint8_t *held;
    size_t held_length;
    bool held_fin, pending_menu, replaying, transport_closed;
} cgp_stream;

typedef struct {
    uint32_t channel;
    cgp_private_kind kind;
    char contract[257];
    uint64_t sequence;
    bool exhausted, declined;
    uint8_t *pending;
    size_t pending_length;
} cgp_binding;
#define CGP_MAX_BINDINGS 16

typedef struct cgp_peer {
    struct cgp_engine *engine;
    ngtcp2_conn *quic;
    WOLFSSL *tls;
    ngtcp2_crypto_conn_ref ref;
    struct sockaddr_in remote;
    ngtcp2_cid original_dcid;
    char name[96];
    uint64_t created, next_action, release_at;
    bool ready, started, failed;
    bool notified, info_sent;
    char fingerprint[65], expected_key[65];
    bool menu_seen, menu_valid;
    unsigned datagram_cursor;
    void *user_data;
    cgp_binding bindings[CGP_MAX_BINDINGS];
    int64_t write_cursor;
    unsigned shot_number, held_arrow;
    char shot_uuid[37];
    bool holding;
    cgp_stream *streams;
    int64_t tx[CGP_CHANNELS], rx[CGP_CHANNELS];
    size_t queued; /* Allocated TX buffer headers plus payload, excluding allocator metadata. */
    uint64_t sent_messages, received_messages, suppressed_messages;
} cgp_peer;

struct cgp_engine {
    cgp_role role;
    cgp_draft draft;
    bool is_server;
    cgp_callbacks callbacks;
    void *user;
    char name[64], fingerprint[65];
    int socket;
    struct sockaddr_in local;
    WOLFSSL_CTX *tls;
    cgp_peer peers[CONFIG_CGP_MAX_CONNECTIONS];
};

bool cgp_publish(cgp_peer *peer, unsigned channel, const char *json);
bool cgp_json_valid(const char *json, size_t length);
/* Call only after cgp_json_valid. Returns only primitive fields used by the
 * mock, skipping unknown/nested content without building a full JSON DOM. */
cJSON *cgp_json_extract(const char *json, size_t length);

cgp_binding *cgp_find_binding(cgp_peer *, uint32_t);

bool cgp_uuid_json_valid(const char *, size_t, bool required);
bool cgp_menu_receive(cgp_peer *, const char *, size_t);
char *cgp_menu_info(cgp_peer *, const char *);
bool cgp_open_connect(const cgp_peer *, uint32_t);
