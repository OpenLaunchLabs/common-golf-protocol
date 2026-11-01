/* SPDX-License-Identifier: Apache-2.0 */
#include "internal.h"
#include "identity.h"
#include <ngtcp2/ngtcp2_crypto_wolfssl.h>
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <sys/socket.h>
#include <unistd.h>

/* Bound allocated queue records, not just their payload. Tiny writes still
 * consume a buffer header; zero-byte writes allocate nothing. */
static bool can_queue(const cgp_peer *p,size_t length) {
    return p->queued<=CGP_TX_LIMIT-sizeof(cgp_buffer) &&
        length<=CGP_TX_LIMIT-sizeof(cgp_buffer)-p->queued;
}

static cgp_stream *find_stream(cgp_peer *p, int64_t id) {
    for (cgp_stream *s = p->streams; s; s = s->next)
        if (s->id == id) return s;
    return NULL;
}

static bool stream_table_full(const cgp_peer *p) {
    unsigned count = 0;
    for (const cgp_stream *s = p->streams; s; s = s->next) ++count;
    return count >= CGP_MAX_STREAMS;
}

static cgp_stream *new_stream(cgp_peer *p, int64_t id) {
    if (stream_table_full(p)) return NULL;
    cgp_stream *s = calloc(1, sizeof(*s));
    if (!s) return NULL;
    s->id = id;
    s->channel = UINT32_MAX;
    cgp_rx_init(&s->rx);
    s->next = p->streams;
    p->streams = s;
    return s;
}

static void release_channel(cgp_peer *p, cgp_stream *s) {
    if (s->channel < CGP_CHANNELS && p->rx[s->channel] == s->id)
        p->rx[s->channel] = -1;
}

static void free_stream(cgp_peer *p, cgp_stream *s) {
    release_channel(p, s);
    cgp_rx_free(&s->rx);
    free(s->held);
    while (s->head) {
        cgp_buffer *b = s->head;
        s->head = b->next;
        p->queued -= sizeof(*b)+b->length;
        free(b);
    }
    free(s);
}

static void drop_peer(cgp_peer *p) {
    if (p->quic && p->notified && p->engine->callbacks.disconnected)
        p->engine->callbacks.disconnected(p, p->engine->user);
    for(unsigned i=0;i<CGP_MAX_BINDINGS;++i)free(p->bindings[i].pending);
    if (p->quic) ngtcp2_conn_del(p->quic);
    if (p->tls) wolfSSL_free(p->tls);
    while (p->streams) {
        cgp_stream *s = p->streams;
        p->streams = s->next;
        free_stream(p, s);
    }
    memset(p, 0, sizeof(*p));
}

static cgp_stream *channel_stream(cgp_peer *p, uint32_t channel, bool outgoing) {
    for (cgp_stream *s = p->streams; s; s = s->next) {
        if (!s->assigned || s->channel != channel || (s->outgoing != outgoing && !cgp_open_connect(p,channel)) || s->stopped) continue;
        /* On channel 2, the stream initiator's peer still owes replies after FIN. */
        if (!s->rx.ended || (outgoing && cgp_open_connect(p,channel) && !s->outgoing)) return s;
    }
    return NULL;
}

/* ngtcp2 can reject another write after STOP_SENDING while it still waits
 * for the RESET_STREAM ACK. Only stream_closed reveals the application code.
 * Pause this channel until then; a write error alone is not a code-1 refusal. */
static bool awaiting_channel_close(const cgp_peer *p,uint32_t channel) {
    for(const cgp_stream *s=p->streams;s;s=s->next)
        if(s->assigned && s->channel==channel && s->awaiting_close &&
           (s->outgoing || cgp_open_connect(p,channel)))return true;
    return false;
}

static bool publish_json(cgp_peer *p, unsigned channel, const char *json) {
    if (!p || !json || !p->ready || p->failed || (channel >= 9 && channel < 1000)) return false;
    cgp_binding *binding = cgp_find_binding(p,channel);
    if (p->engine->draft==CGP_DRAFT_3 && channel==2) return false;
    if (channel >= 1000 && (!binding || binding->kind!=CGP_PRIVATE_JSON || binding->declined || !cgp_custom_ready(p))) return false;
    bool sim = p->engine->role == CGP_SIMULATOR;
    if (channel && !p->info_sent) return false;
    if (channel < 1000 && channel != 0 && channel != 2 &&
        (sim ? !(channel == 6 || channel == 7) : (channel == 6 || channel == 7))) return false;
    if (p->engine->role == CGP_CONTROL_BOX && channel != 0 && channel != 5 && channel < 1000) return false;
    if ((channel < CGP_CHANNELS && p->tx[channel] == -2) || awaiting_channel_close(p,channel)) return false;
    size_t length = strlen(json);
    if (length > CGP_MAX_MESSAGE_BYTES || !cgp_json_valid(json, length)) return false;
    if(p->engine->draft==CGP_DRAFT_3 && (channel==1||channel==3||channel==7||channel==8||cgp_open_connect(p,channel)) &&
       !cgp_uuid_json_valid(json,length,channel==1||channel==7||channel==8))return false;
    cgp_stream *s = channel_stream(p, channel, true);
    if (s && s->fin_requested) return false;
    bool opening = !s;
    if (opening && (cgp_open_connect(p,channel) && sim)) return false; /* device opens compatibility */
    size_t bytes = length + 4 + (opening ? 4 : 0);
    if (!can_queue(p,bytes)) { ++p->suppressed_messages; return false; }
    if (opening && stream_table_full(p)) return false;
    cgp_buffer *b = malloc(sizeof(*b) + bytes);
    if (!b) { ++p->suppressed_messages; return false; }
    if (opening) {
        int64_t sid;
        int rc = cgp_open_connect(p,channel) ? ngtcp2_conn_open_bidi_stream(p->quic, &sid, NULL)
                              : ngtcp2_conn_open_uni_stream(p->quic, &sid, NULL);
        if (rc) { free(b); return false; }
        s = new_stream(p, sid);
        if (!s) { ngtcp2_conn_shutdown_stream(p->quic, 0, sid, CGP_NOT_INTERESTED); free(b); return false; }
        s->outgoing = true;
        s->channel = channel;s->assigned=true;
        if (channel < CGP_CHANNELS) p->tx[channel] = sid;
        if (cgp_open_connect(p,channel)) { s->rx.channel = channel; s->rx.has_channel = true; }
    }
    *b = (cgp_buffer){.offset = s->next_offset, .length = bytes};
    size_t prefix = opening ? 4 : 0;
    if (opening) cgp_write_u32(b->bytes, channel);
    cgp_write_u32(b->bytes + prefix, (uint32_t)length);
    memcpy(b->bytes + prefix + 4, json, length);
    if (s->tail) s->tail->next = b;
    else s->head = b;
    s->tail = b;
    s->next_offset += bytes;
    p->queued += sizeof(*b)+bytes;
    ++p->sent_messages;
    if (!channel) p->info_sent = true;
    return true;
}

bool cgp_publish(cgp_peer *p,unsigned channel,const char *json) {
    if(!p || !json)return false;
    if(channel || p->engine->draft!=CGP_DRAFT_3)return publish_json(p,channel,json);
    if(!cgp_json_valid(json,strlen(json)))return false;
    char *info=cgp_menu_info(p,json);if(!info)return false;
    bool sent=publish_json(p,0,info);free(info);
    if(sent && p->engine->is_server && !p->menu_seen) {
        p->menu_seen=p->menu_valid=true;
        if(p->engine->callbacks.menu)p->engine->callbacks.menu(p,true,p->engine->user);
    }
    return sent;
}

bool cgp_finish_channel(cgp_peer *p, uint32_t channel) {
    cgp_stream *s = channel_stream(p,channel,true);
    if (!s || s->stopped) return false;
    s->fin_requested=true; return true;
}

bool cgp_close_channel(cgp_peer *p, uint32_t channel) {
    cgp_stream *s = channel_stream(p, channel, true);
    if (!s) return false;
    /* RESET_STREAM releases the channel immediately; queued data is abandoned. */
    if (ngtcp2_conn_shutdown_stream_write(p->quic, 0, s->id, 0)) return false;
    s->stopped = true;
    if (channel < CGP_CHANNELS) p->tx[channel] = -1;
    return true;
}

static unsigned raw_streams(cgp_peer *p) {
    unsigned count=0;
    for (cgp_stream *s=p->streams;s;s=s->next) {
        cgp_binding *b=cgp_find_binding(p,s->channel);
        if (s->assigned && b && (b->kind & CGP_PRIVATE_RAW) && !s->stopped && !(s->rx.ended && (s->id&2)) && !(s->fin_sent && s->rx.ended)) ++count;
    }
    return count;
}

typedef struct { cgp_peer *peer; cgp_stream *stream; } receive_context;

static int accept_channel(void *user, uint32_t ch) {
    receive_context *r = user;
    cgp_peer *p = r->peer;
    cgp_stream *s = r->stream;
    bool sim = p->engine->role == CGP_SIMULATOR;
    bool reads = ch == 0 || ch >= 1000 || (sim ? (ch == 1 || ch == 2 || ch == 3 || ch == 4 || ch == 5 || ch == 8)
                                 : (ch == 6 || ch == 7));
    cgp_binding *binding = cgp_find_binding(p,ch);
    if(p->engine->draft==CGP_DRAFT_3 && ch==2)return 1;
    if(ch>=1000 && (!binding || binding->kind>CGP_PRIVATE_RAW_DATAGRAM || binding->kind==CGP_PRIVATE_DATAGRAM || !cgp_custom_ready(p)))return 1;
    bool raw=binding && (binding->kind & CGP_PRIVATE_RAW);
    bool open_connect=cgp_open_connect(p,ch);
    if(open_connect && (!sim || (s->id&2)))return 1;
    if(!reads || (ch<1000 && ((ch==2)==!!(s->id&2))) || (!raw && channel_stream(p,ch,false)))return 1;
    if(raw && raw_streams(p)>=CGP_MAX_RAW_STREAMS)return 1;
    if (p->engine->callbacks.accept_channel && p->engine->callbacks.accept_channel(p,ch,p->engine->user)) return 1;
    s->channel = ch;s->assigned=true;
    if (ch < CGP_CHANNELS) p->rx[ch] = s->id;
    return 0;
}

static int receive_json(void *user, uint32_t ch, const char *body, size_t length) {
    receive_context *r = user;
    cgp_peer *p = r->peer;
    if (length > CGP_MAX_MESSAGE_BYTES || !cgp_json_valid(body, length)) return 2;
    if(p->engine->draft==CGP_DRAFT_3 && (ch==1||ch==3||ch==7||ch==8||cgp_open_connect(p,ch)) &&
       !cgp_uuid_json_valid(body,length,ch==1||ch==7||ch==8))return CGP_MALFORMED;
    if(ch==0 && !cgp_menu_receive(p,body,length))return 2;
    ++p->received_messages;
    /* Start the application before dispatch if a peer stream races handshake callbacks. */
    if (!p->notified && p->ready) {
        p->notified = true;
        if (p->engine->callbacks.connected) p->engine->callbacks.connected(p, cgp_peer_identity(p), p->engine->user);
    }
    if (p->engine->callbacks.message) p->engine->callbacks.message(p,ch,body,length,p->engine->user);
    return 0;
}

static int receive_stream(ngtcp2_conn *conn, uint32_t flags, int64_t id,
                          uint64_t offset, const uint8_t *data, size_t length,
                          void *user, void *stream_user) {
    (void)offset; (void)stream_user;
    cgp_peer *p = user;
    cgp_stream *s = find_stream(p, id);
    if (!s) s = new_stream(p, id);
    if (!s) {
        ngtcp2_conn_extend_max_offset(conn, length);
        ngtcp2_conn_shutdown_stream(conn,0,id,CGP_NOT_INTERESTED);
        return 0;
    }
    bool fin = flags & NGTCP2_STREAM_DATA_FLAG_FIN;
    receive_context context = {p, s};
    if (!p->notified && p->ready) {
        p->notified = true;
        if (p->engine->callbacks.connected) p->engine->callbacks.connected(p,cgp_peer_identity(p),p->engine->user);
    }
    size_t credit=s->replaying ? 0 : length;
    uint8_t *combined=NULL;
    if(p->engine->draft==CGP_DRAFT_3 && !p->engine->is_server && (s->pending_menu || !p->menu_seen) && !s->rx.has_channel) {
        if(s->held_length+length>4096)return NGTCP2_ERR_CALLBACK_FAILURE;
        uint8_t *held=realloc(s->held,s->held_length+length);
        if(!held && length)return NGTCP2_ERR_CALLBACK_FAILURE;
        s->held=held;if(length)memcpy(held+s->held_length,data,length);s->held_length+=length;fin=fin||s->held_fin;s->held_fin=fin;
        uint32_t channel=s->held_length>=4 ? ((uint32_t)held[0]<<24)|((uint32_t)held[1]<<16)|((uint32_t)held[2]<<8)|held[3] : UINT32_MAX;
        if(!p->menu_seen && ((s->held_length<4 && !fin) || channel>=1000)) {
            s->pending_menu=true;ngtcp2_conn_extend_max_offset(conn,credit);return 0;
        }
        combined=s->held;data=combined;length=s->held_length;s->held=NULL;s->held_length=0;s->pending_menu=false;
    }
    size_t remaining=length;const uint8_t *at=data;int rc=s->rx.status;
    if(!rc && !s->rx.has_channel) {
        size_t n=4-s->rx.header_used;if(n>remaining)n=remaining;
        rc=cgp_rx_feed(&s->rx,at,n,false,accept_channel,receive_json,&context);at+=n;remaining-=n;
    }
    cgp_binding *binding=cgp_find_binding(p,s->channel);
    if(!rc && s->rx.has_channel && binding && (binding->kind & CGP_PRIVATE_RAW)) {
        if(p->engine->callbacks.raw_stream)p->engine->callbacks.raw_stream(p,s->id,s->channel,at,remaining,fin,p->engine->user);
        if(fin)s->rx.ended=true;
    } else if(!rc)rc=cgp_rx_feed(&s->rx,at,remaining,fin,accept_channel,receive_json,&context);
    free(combined);
    if (rc < 0) return NGTCP2_ERR_CALLBACK_FAILURE;
    /* Return consumed credit even for discarded streams. */
    ngtcp2_conn_extend_max_offset(conn, credit);
    if (!rc && !s->transport_closed && ngtcp2_conn_extend_max_stream_offset(conn, id, length))
        return NGTCP2_ERR_CALLBACK_FAILURE;
    if (rc && !s->stopped) {
        s->stopped = true;
        cgp_log("STOP peer=%s stream=%" PRId64 " code=%d", p->name, id, rc);
        /* Declined bidirectional compatibility streams also need their unused
         * local write half closed, or their stream slot can never be reclaimed. */
        int shutdown = s->transport_closed ? 0 : !fin ? ngtcp2_conn_shutdown_stream(conn, 0, id, (uint64_t)rc)
            : !(id & 2) ? ngtcp2_conn_shutdown_stream_write(conn, 0, id, (uint64_t)rc) : 0;
        if (shutdown) return NGTCP2_ERR_CALLBACK_FAILURE;
    }
    if (rc || fin) release_channel(p, s);
    return 0;
}

static int stream_opened(ngtcp2_conn *conn, int64_t id, void *user) {
    (void)conn;
    cgp_peer *p = user;
    if (!find_stream(p,id) && !new_stream(p,id)) ngtcp2_conn_shutdown_stream(conn,0,id,CGP_NOT_INTERESTED);
    return 0;
}

static int stream_closed(ngtcp2_conn *conn, uint32_t flags, int64_t id,
                         uint64_t code, void *user, void *stream_user) {
    (void)stream_user;
    cgp_peer *p = user;
    cgp_stream **link = &p->streams;
    while (*link && (*link)->id != id) link = &(*link)->next;
    if (*link) {
        cgp_stream *s = *link;
        if(s->pending_menu) {s->transport_closed=true;goto released;}
        *link = s->next;
        cgp_binding *binding=cgp_find_binding(p,s->channel);
        if(binding && code==CGP_NOT_INTERESTED && s->outgoing)binding->declined=true;
        if(binding && (binding->kind&CGP_PRIVATE_RAW) && p->engine->callbacks.raw_closed)p->engine->callbacks.raw_closed(p,s->id,p->engine->user);
        if (s->outgoing) {
            if (s->channel < CGP_CHANNELS && p->tx[s->channel] == s->id)
                p->tx[s->channel] = (flags & NGTCP2_STREAM_CLOSE_FLAG_APP_ERROR_CODE_SET) && (p->engine->draft==CGP_DRAFT_3 ? code==CGP_NOT_INTERESTED : code!=0) ? -2 : -1;
            cgp_log("channel closed peer=%s channel=%u code=%" PRIu64, p->name, s->channel, code);
        }
        free_stream(p, s);
    }
released:;
    /* One replacement slot for each incoming stream, including streams
     * refused because the table was full and never tracked. */
    bool local_client = !p->engine->is_server;
    if ((id & 1) != (local_client ? 0 : 1)) {
        if (id & 2) ngtcp2_conn_extend_max_streams_uni(conn, 1);
        else ngtcp2_conn_extend_max_streams_bidi(conn, 1);
    }
    return 0;
}

static int stream_reset(ngtcp2_conn *conn, int64_t id, uint64_t final_size,
                        uint64_t code, void *user, void *stream_user) {
    (void)conn; (void)final_size; (void)stream_user;
    cgp_peer *p = user;
    cgp_stream *s = find_stream(p, id);
    if (s) {
        cgp_binding *binding=cgp_find_binding(p,s->channel);
        if(binding && (binding->kind&CGP_PRIVATE_RAW)) {
            s->stopped=true;if(!(id&2))ngtcp2_conn_shutdown_stream_write(conn,0,id,code);
        }
        release_channel(p, s);
        cgp_rx_free(&s->rx);
        s->rx.status = 1;s->rx.ended=true;
    }
    return 0;
}

static int acknowledged(ngtcp2_conn *conn, int64_t id, uint64_t offset,
                        uint64_t length, void *user, void *stream_user) {
    (void)conn; (void)stream_user;
    cgp_peer *p = user;
    cgp_stream *s = find_stream(p, id);
    if (!s) return 0;
    while (s->head && s->head->offset + s->head->length <= offset + length) {
        cgp_buffer *b = s->head;
        s->head = b->next;
        p->queued -= sizeof(*b)+b->length;
        free(b);
    }
    if (!s->head) s->tail = NULL;
    return 0;
}

static int handshake_done(ngtcp2_conn *conn, void *user) {
    (void)conn;
    cgp_peer *p = user;
    const char *expected_alpn = cgp_alpn(p->engine->draft);
    char *alpn = NULL;
    unsigned short alpn_length = 0;
    if (wolfSSL_ALPN_GetProtocol(p->tls, &alpn, &alpn_length) != WOLFSSL_SUCCESS ||
        alpn_length != strlen(expected_alpn) || memcmp(alpn, expected_alpn, alpn_length)) {
        cgp_log("peer=%s did not negotiate %s", p->name, expected_alpn);
        return NGTCP2_ERR_CALLBACK_FAILURE;
    }
    char fingerprint[65];
    int identity = cgp_peer_fingerprint(p->tls, fingerprint);
    if(p->engine->draft==CGP_DRAFT_3) {
        int client_type=-1,server_type=-1;
        if(wolfSSL_get_negotiated_client_cert_type(p->tls,&client_type)!=WOLFSSL_SUCCESS ||
           wolfSSL_get_negotiated_server_cert_type(p->tls,&server_type)!=WOLFSSL_SUCCESS ||
           client_type!=WOLFSSL_CERT_TYPE_RPK || server_type!=WOLFSSL_CERT_TYPE_RPK || identity!=0 ||
           (!p->engine->is_server && strcmp(p->expected_key,fingerprint)))return NGTCP2_ERR_CALLBACK_FAILURE;
        const ngtcp2_transport_params *params=ngtcp2_conn_get_remote_transport_params(conn);
        if(!params || params->max_datagram_frame_size<1200)return NGTCP2_ERR_CALLBACK_FAILURE;
    }
    if (identity < 0 || (identity == 1 && !p->engine->is_server)) {
        cgp_log("peer=%s invalid or missing Ed25519 peer identity", p->name);
        return NGTCP2_ERR_CALLBACK_FAILURE;
    }
    if (identity == 0) memcpy(p->fingerprint, fingerprint, 65);
    const char *tier = p->engine->callbacks.trust_tier ? p->engine->callbacks.trust_tier(cgp_peer_identity(p),p->engine->user) : "maker";
    if (!tier || (strcmp(tier,"internal") && strcmp(tier,"developer"))) tier = "maker";
    snprintf(p->tier,sizeof(p->tier),"%s",tier);
    p->ready = true;
    cgp_log("CONNECTED peer=%s fingerprint=%s tier=%s handshake_ms=%" PRIu64,
            p->name, identity == 0 ? fingerprint : "none", p->tier, (cgp_now_ns() - p->created) / 1000000);
    return 0;
}

static int receive_datagram(ngtcp2_conn *conn, uint32_t flags, const uint8_t *data, size_t length, void *user) {
    (void)conn; (void)flags;
    cgp_peer *p=user;
    if (p->engine->draft != CGP_DRAFT_3 || length < 12) return 0;
    if (!p->notified && p->ready) {
        p->notified=true;
        if (p->engine->callbacks.connected) p->engine->callbacks.connected(p,cgp_peer_identity(p),p->engine->user);
    }
    uint32_t channel=((uint32_t)data[0]<<24)|((uint32_t)data[1]<<16)|((uint32_t)data[2]<<8)|data[3];
    cgp_binding *b=cgp_find_binding(p,channel);
    if (!b || b->kind>CGP_PRIVATE_RAW_DATAGRAM || !cgp_custom_ready(p) || !(b->kind & CGP_PRIVATE_DATAGRAM)) return 0;
    if(p->engine->callbacks.accept_channel && p->engine->callbacks.accept_channel(p,channel,p->engine->user))return 0;
    uint64_t seq=0;
    for (unsigned i=4;i<12;++i) seq=(seq<<8)|data[i];
    if (p->engine->callbacks.datagram) p->engine->callbacks.datagram(p,channel,seq,data+12,length-12,p->engine->user);
    return 0;
}

size_t cgp_max_datagram_payload(const cgp_peer *p) {
    if (!p || !p->quic || !p->ready || p->engine->draft != CGP_DRAFT_3) return 0;
    const ngtcp2_transport_params *params=ngtcp2_conn_get_remote_transport_params(p->quic);
    if (!params || params->max_datagram_frame_size < 15) return 0;
    /* Conservative budget for the 1200-byte path, AEAD/tag, packet and frame headers. */
    size_t size=params->max_datagram_frame_size-15;
    size_t path=ngtcp2_conn_get_path_max_tx_udp_payload_size(p->quic);
    size_t payload=path>100 ? path-100 : 0;
    if(payload>1100)payload=1100;
    return size<payload ? size : payload;
}
bool cgp_send_datagram(cgp_peer *p, uint32_t channel, const uint8_t *data, size_t length) {
    cgp_binding *b=p ? cgp_find_binding(p,channel) : NULL;
    if (!b || b->kind>CGP_PRIVATE_RAW_DATAGRAM || !cgp_custom_ready(p) || !(b->kind & CGP_PRIVATE_DATAGRAM) || p->failed || b->declined || awaiting_channel_close(p,channel) || !p->info_sent || !cgp_custom_ready(p) || b->pending || b->exhausted ||
        !cgp_max_datagram_payload(p) || length > cgp_max_datagram_payload(p) || (!data && length)) return false;
    b->pending=malloc(length+12);
    if (!b->pending) return false;
    b->pending_length=length+12;
    cgp_write_u32(b->pending,channel);
    for (unsigned i=0;i<8;++i) b->pending[4+i]=(uint8_t)(b->sequence >> (56-i*8));
    if (length) memcpy(b->pending+12,data,length);
    if (b->sequence==UINT64_MAX) b->exhausted=true; else ++b->sequence;
    return true;
}
int64_t cgp_open_raw(cgp_peer *p, uint32_t channel, bool bidirectional) {
    cgp_binding *binding=p ? cgp_find_binding(p,channel) : NULL;
    if (!binding || binding->kind>CGP_PRIVATE_RAW_DATAGRAM || binding->declined || !(binding->kind & CGP_PRIVATE_RAW) || !p->ready || p->failed || !p->info_sent ||
        !cgp_custom_ready(p) || awaiting_channel_close(p,channel) || raw_streams(p)>=CGP_MAX_RAW_STREAMS || !can_queue(p,4)) return -1;
    cgp_buffer *b=malloc(sizeof(*b)+4);
    if (!b) return -1;
    int64_t id;
    int rc=bidirectional ? ngtcp2_conn_open_bidi_stream(p->quic,&id,NULL) : ngtcp2_conn_open_uni_stream(p->quic,&id,NULL);
    if (rc) { free(b); return -1; }
    cgp_stream *s=new_stream(p,id);
    if (!s) { free(b); ngtcp2_conn_shutdown_stream_write(p->quic,0,id,CGP_NOT_INTERESTED);return -1; }
    s->outgoing=true;s->channel=channel;s->assigned=true;
    if (bidirectional) {s->rx.has_channel=true;s->rx.channel=channel;}
    *b=(cgp_buffer){.length=4};cgp_write_u32(b->bytes,channel);
    s->head=s->tail=b;s->next_offset=4;p->queued+=sizeof(*b)+4;
    return id;
}
bool cgp_send_raw_on_stream(cgp_peer *p,int64_t id,const uint8_t *data,size_t length) {
    cgp_stream *s=p ? find_stream(p,id) : NULL;
    cgp_binding *binding=s ? cgp_find_binding(p,s->channel) : NULL;
    if (!binding || binding->kind>CGP_PRIVATE_RAW_DATAGRAM || binding->declined || !(binding->kind & CGP_PRIVATE_RAW) || (!s->outgoing && (id&2)) || s->stopped || s->fin_requested ||
        !p->ready || p->failed || !p->info_sent || !cgp_custom_ready(p) || awaiting_channel_close(p,s->channel) || (!data && length) || (length && !can_queue(p,length))) return false;
    if (!length) return true;
    cgp_buffer *b=malloc(sizeof(*b)+length);
    if (!b) return false;
    *b=(cgp_buffer){.offset=s->next_offset,.length=length};
    if (length) memcpy(b->bytes,data,length);
    if (s->tail) s->tail->next=b; else s->head=b;
    s->tail=b;s->next_offset+=length;p->queued+=sizeof(*b)+length;return true;
}
bool cgp_send_raw(cgp_peer *p,uint32_t channel,const uint8_t *data,size_t length) {
    if (!p || (!data && length) || (length && !can_queue(p,length))) return false;
    cgp_stream *s=channel_stream(p,channel,true);
    int64_t id=s ? s->id : cgp_open_raw(p,channel,false);
    return id>=0 && cgp_send_raw_on_stream(p,id,data,length);
}
bool cgp_finish_stream(cgp_peer *p,int64_t id) {
    cgp_stream *s=p ? find_stream(p,id) : NULL;
    if (!s || s->stopped || (!s->outgoing && (id&2))) return false;
    s->fin_requested=true;return true;
}

bool cgp_cancel_stream(cgp_peer *p,int64_t id) {
    cgp_stream *s=p ? find_stream(p,id) : NULL;
    if (!s || s->stopped) return false;
    if (ngtcp2_conn_shutdown_stream(p->quic,0,id,0)) return false;
    s->stopped=true;return true;
}

static void random_bytes(uint8_t *dest, size_t length, const ngtcp2_rand_ctx *ctx) {
    (void)ctx;
    cgp_random(dest, length);
}

static int new_connection_id(ngtcp2_conn *conn, ngtcp2_cid *cid,
                              uint8_t *token, size_t length, void *user) {
    (void)conn; (void)user;
    cgp_random(cid->data, length);
    cid->datalen = length;
    cgp_random(token, NGTCP2_STATELESS_RESET_TOKENLEN);
    return 0;
}

static ngtcp2_conn *get_connection(ngtcp2_crypto_conn_ref *ref) {
    return ((cgp_peer *)ref->user_data)->quic;
}

static cgp_peer *new_peer(cgp_engine *e, const struct sockaddr_in *remote,
                          const char *name, const ngtcp2_pkt_hd *initial) {
    cgp_peer *p = NULL;
    for (unsigned i = 0; i < CONFIG_CGP_MAX_CONNECTIONS; ++i)
        if (!e->peers[i].quic) { p = &e->peers[i]; break; }
    if (!p) return NULL;
    memset(p, 0, sizeof(*p));
    p->engine = e;
    p->remote = *remote;
    p->created = cgp_now_ns();
    p->write_cursor=-1;
    snprintf(p->name, sizeof(p->name), "%s", name);
    for (unsigned ch = 0; ch < CGP_CHANNELS; ++ch) p->tx[ch] = p->rx[ch] = -1;
    ngtcp2_callbacks callbacks = {
        .client_initial = ngtcp2_crypto_client_initial_cb,
        .recv_client_initial = ngtcp2_crypto_recv_client_initial_cb,
        .recv_crypto_data = ngtcp2_crypto_recv_crypto_data_cb,
        .encrypt = ngtcp2_crypto_encrypt_cb,
        .decrypt = ngtcp2_crypto_decrypt_cb,
        .hp_mask = ngtcp2_crypto_hp_mask_cb,
        .recv_retry = ngtcp2_crypto_recv_retry_cb,
        .recv_stream_data = receive_stream,
        .recv_datagram = receive_datagram,
        .stream_open = stream_opened,
        .stream_close = stream_closed,
        .stream_reset = stream_reset,
        .acked_stream_data_offset = acknowledged,
        .handshake_completed = handshake_done,
        .rand = random_bytes,
        .get_new_connection_id = new_connection_id,
        .update_key = ngtcp2_crypto_update_key_cb,
        .delete_crypto_aead_ctx = ngtcp2_crypto_delete_crypto_aead_ctx_cb,
        .delete_crypto_cipher_ctx = ngtcp2_crypto_delete_crypto_cipher_ctx_cb,
        .get_path_challenge_data = ngtcp2_crypto_get_path_challenge_data_cb,
        .version_negotiation = ngtcp2_crypto_version_negotiation_cb,
    };
    ngtcp2_settings settings;
    ngtcp2_settings_default(&settings);
    settings.initial_ts = p->created;
    settings.handshake_timeout = 20 * NGTCP2_SECONDS;
    settings.max_tx_udp_payload_size = 1200;
    settings.no_pmtud = 1;
    ngtcp2_transport_params params;
    ngtcp2_transport_params_default(&params);
    /* These are moving flow-control windows, not message size ceilings. */
    params.initial_max_stream_data_uni = 4096;
    params.initial_max_stream_data_bidi_remote = 4096;
    params.initial_max_stream_data_bidi_local = 4096;
    params.initial_max_data = 16384;
    params.initial_max_streams_uni = CGP_REMOTE_STREAMS_UNI;
    params.initial_max_streams_bidi = CGP_REMOTE_STREAMS_BIDI;
    params.max_idle_timeout = (e->draft==CGP_DRAFT_3 ? 30 : 90) * NGTCP2_SECONDS;
    if(e->draft==CGP_DRAFT_3)params.max_datagram_frame_size=1200;
    params.max_udp_payload_size = 2048; /* Matches read_packets' receive buffer. */
    params.active_connection_id_limit = 2;
    params.disable_active_migration = 1;
    ngtcp2_path path = {
        .local = {(struct sockaddr *)&e->local, sizeof(e->local)},
        .remote = {(struct sockaddr *)&p->remote, sizeof(p->remote)},
    };
    ngtcp2_cid dcid, scid;
    scid.datalen = 8;
    cgp_random(scid.data, scid.datalen);
    int rc;
    if (initial) {
        p->original_dcid = initial->dcid;
        params.original_dcid = initial->dcid;
        params.original_dcid_present = 1;
        rc = ngtcp2_conn_server_new(&p->quic, &initial->scid, &scid, &path,
                initial->version, &callbacks, &settings, &params, NULL, p);
    } else {
        dcid.datalen = 8;
        cgp_random(dcid.data, dcid.datalen);
        rc = ngtcp2_conn_client_new(&p->quic, &dcid, &scid, &path,
                NGTCP2_PROTO_VER_V1, &callbacks, &settings, &params, NULL, p);
    }
    if (rc) goto failure;
    p->tls = wolfSSL_new(e->tls);
    if (!p->tls) goto failure;
    const char *expected_alpn = cgp_alpn(e->draft);
    if (wolfSSL_UseALPN(p->tls, (char *)expected_alpn, (unsigned)strlen(expected_alpn),
                       WOLFSSL_ALPN_FAILED_ON_MISMATCH) != WOLFSSL_SUCCESS) goto failure;
    p->ref = (ngtcp2_crypto_conn_ref){.get_conn = get_connection, .user_data = p};
    if (wolfSSL_set_app_data(p->tls, &p->ref) != WOLFSSL_SUCCESS) goto failure;
    if (initial) wolfSSL_set_accept_state(p->tls);
    else wolfSSL_set_connect_state(p->tls);
    ngtcp2_conn_set_tls_native_handle(p->quic, p->tls);
    ngtcp2_conn_set_keep_alive_timeout(p->quic, (e->draft==CGP_DRAFT_3 ? 5 : 15) * NGTCP2_SECONDS);
    cgp_log("CONNECTING peer=%s", p->name);
    return p;
failure:
    cgp_log("connection allocation/TLS setup failed peer=%s", p->name);
    drop_peer(p);
    return NULL;
}

cgp_engine *cgp_create_options(const cgp_options *options) {
    if (!options || !options->name ||
        options->role < CGP_LAUNCH_MONITOR || options->role > CGP_SIMULATOR) return NULL;
    cgp_draft draft=options->draft ? options->draft : CGP_DRAFT_3;
    if(!cgp_alpn(draft))return NULL;
    cgp_engine *e = calloc(1, sizeof(*e));
    if (!e) return NULL;
    e->socket = -1;
    e->role = options->role;
    e->draft = draft;
    e->is_server = options->is_server;
    e->callbacks = options->callbacks;
    e->user = options->user;
    snprintf(e->name, sizeof(e->name), "%s", options->name);
    e->tls = cgp_tls_context(options->is_server, draft==CGP_DRAFT_3, options->identity_directory, e->fingerprint);
    if (!e->tls) goto failure;
    cgp_log("identity Ed25519=%s",e->fingerprint);
    e->socket = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (e->socket < 0) goto failure;
    struct sockaddr_in bind_address = {.sin_family = AF_INET, .sin_addr.s_addr = htonl(INADDR_ANY)};
    if (bind(e->socket, (struct sockaddr *)&bind_address, sizeof(bind_address))) goto failure;
    socklen_t length = sizeof(e->local);
    if (getsockname(e->socket, (struct sockaddr *)&e->local, &length)) goto failure;
    e->local.sin_addr = options->local_ip;
    if (fcntl(e->socket, F_SETFL, O_NONBLOCK) < 0) goto failure;
    return e;
failure:
    cgp_destroy(e);
    return NULL;
}

void cgp_destroy(cgp_engine *e) {
    if (!e) return;
    for (unsigned i = 0; i < CONFIG_CGP_MAX_CONNECTIONS; ++i) drop_peer(&e->peers[i]);
    if (e->socket >= 0) close(e->socket);
    if (e->tls) wolfSSL_CTX_free(e->tls);
    free(e);
}

uint16_t cgp_port(const cgp_engine *e) { return ntohs(e->local.sin_port); }

bool cgp_connect(cgp_engine *e, const struct sockaddr_in *remote, const char *name) {
    if (e->draft==CGP_DRAFT_3)return false;
    return cgp_connect_key(e,remote,name,NULL);
}
bool cgp_connect_key(cgp_engine *e,const struct sockaddr_in *remote,const char *name,const char *key) {
    if(e->is_server || (e->draft==CGP_DRAFT_3 && !cgp_key_valid(key)))return false;
    for (unsigned i = 0; i < CONFIG_CGP_MAX_CONNECTIONS; ++i) {
        cgp_peer *p = &e->peers[i];
        if (p->quic && p->remote.sin_addr.s_addr == remote->sin_addr.s_addr &&
            p->remote.sin_port == remote->sin_port) return true;
    }
    cgp_peer *p=new_peer(e,remote,name,NULL);
    if(p && key)snprintf(p->expected_key,sizeof(p->expected_key),"%s",key);
    return p!=NULL;
}

static bool cid_matches(cgp_peer *p, const ngtcp2_version_cid *cid) {
    if (p->original_dcid.datalen == cid->dcidlen && cid->dcidlen &&
        !memcmp(p->original_dcid.data, cid->dcid, cid->dcidlen)) return true;
    size_t count = ngtcp2_conn_get_scid(p->quic, NULL);
    ngtcp2_cid small[8];
    ngtcp2_cid *ids = count <= 8 ? small : malloc(count * sizeof(*ids));
    if (!ids) return false;
    ngtcp2_conn_get_scid(p->quic, ids);
    bool found = false;
    for (size_t i = 0; i < count; ++i)
        if (ids[i].datalen == cid->dcidlen && !memcmp(ids[i].data, cid->dcid, cid->dcidlen)) found = true;
    if (ids != small) free(ids);
    return found;
}

static void fail_peer(cgp_peer *p, int error) {
    const ngtcp2_ccerr *received = ngtcp2_conn_get_ccerr(p->quic);
    cgp_log("DISCONNECTED peer=%s reason=%s tls_error=%d tls_alert=%u peer_code=0x%llx ALPN=%s", p->name, ngtcp2_strerror(error),
        ngtcp2_conn_get_tls_error(p->quic),ngtcp2_conn_get_tls_alert(p->quic),(unsigned long long)received->error_code,cgp_alpn(p->engine->draft));
    if (error != NGTCP2_ERR_DRAINING && error != NGTCP2_ERR_IDLE_CLOSE) {
        ngtcp2_ccerr close_error;
        ngtcp2_ccerr_default(&close_error);
        if (error == NGTCP2_ERR_CRYPTO)
            ngtcp2_ccerr_set_tls_alert(&close_error, ngtcp2_conn_get_tls_alert(p->quic), NULL, 0);
        else ngtcp2_ccerr_set_liberr(&close_error, error, NULL, 0);
        uint8_t packet[1200];
        ngtcp2_ssize length = ngtcp2_conn_write_connection_close(p->quic, NULL, NULL,
                packet, sizeof(packet), &close_error, cgp_now_ns());
        if (length > 0) sendto(p->engine->socket, packet, (size_t)length, 0,
                              (struct sockaddr *)&p->remote, sizeof(p->remote));
    }
    p->failed = true;
}

static void read_packets(cgp_engine *e) {
    uint8_t packet[2048];
    for (unsigned budget = 0; budget < 32; ++budget) {
        struct sockaddr_in remote;
        socklen_t address_length = sizeof(remote);
        ssize_t length = recvfrom(e->socket, packet, sizeof(packet), 0,
                                  (struct sockaddr *)&remote, &address_length);
        if (length < 0) break;
        ngtcp2_version_cid cid;
        if (ngtcp2_pkt_decode_version_cid(&cid, packet, (size_t)length, 8)) continue;
        cgp_peer *p = NULL;
        for (unsigned i = 0; i < CONFIG_CGP_MAX_CONNECTIONS; ++i) {
            cgp_peer *candidate = &e->peers[i];
            if (candidate->quic && !candidate->failed && cid_matches(candidate, &cid)) {
                p = candidate;
                break;
            }
        }
        if (!p && e->is_server) {
            ngtcp2_pkt_hd initial;
            if (!ngtcp2_accept(&initial, packet, (size_t)length))
                p = new_peer(e, &remote, "simulator", &initial);
        }
        if (!p) continue;
        ngtcp2_path path = {
            .local = {(struct sockaddr *)&e->local, sizeof(e->local)},
            .remote = {(struct sockaddr *)&remote, sizeof(remote)},
        };
        ngtcp2_pkt_info info = {0};
        int rc = ngtcp2_conn_read_pkt(p->quic, &path, &info, packet, (size_t)length, cgp_now_ns());
        if (rc) fail_peer(p, rc);
    }
}

static void write_one_datagram(cgp_peer *p) {
    uint8_t packet[1200];
    for (unsigned ntry=0;ntry<CGP_MAX_BINDINGS;++ntry) {
        unsigned i=(p->datagram_cursor+ntry)%CGP_MAX_BINDINGS;
        cgp_binding *b=&p->bindings[i];
        if (!b->pending) continue;
        ngtcp2_path_storage path; ngtcp2_path_storage_zero(&path);
        ngtcp2_pkt_info info={0}; int accepted=0;
        uint64_t now=cgp_now_ns();
        ngtcp2_ssize n=b->pending_length-12>cgp_max_datagram_payload(p) ? NGTCP2_ERR_INVALID_ARGUMENT :
            ngtcp2_conn_write_datagram(p->quic,&path.path,&info,packet,sizeof(packet),&accepted,0,0,b->pending,b->pending_length,now);
        if (n>0) {
            sendto(p->engine->socket,packet,(size_t)n,0,path.path.remote.addr,path.path.remote.addrlen);
            ngtcp2_conn_update_pkt_tx_time(p->quic,now);
        }
        if (n<0) {
            uint64_t sequence=0;
            for(unsigned j=4;j<12;++j)sequence=(sequence<<8)|b->pending[j];
            free(b->pending);b->pending=NULL;b->pending_length=0;
            cgp_log("DATAGRAM_REJECTED peer=%s channel=%u sequence=%" PRIu64 " reason=%s",p->name,b->channel,sequence,ngtcp2_strerror((int)n));
            if(p->engine->callbacks.datagram_error)
                p->engine->callbacks.datagram_error(p,b->channel,sequence,(int)n,p->engine->user);
            if(n!=NGTCP2_ERR_INVALID_ARGUMENT)fail_peer(p,(int)n);
        } else if(accepted) {free(b->pending);b->pending=NULL;b->pending_length=0;}
        p->datagram_cursor=(i+1)%CGP_MAX_BINDINGS;break;
    }
}

static void write_packets(cgp_peer *p) {
    uint8_t packet[1200];
    cgp_stream *cursor = find_stream(p,p->write_cursor);
    if (!cursor) cursor=p->streams;
    for (unsigned budget = 0; budget < 16; ++budget) {
        cgp_buffer *b = NULL;
        cgp_stream *s = NULL;
        /* Rotate between streams so one blocked channel cannot stall another. */
        cgp_stream *begin = cursor;
        if (cursor) do {
            s = cursor;
            cursor = cursor->next ? cursor->next : p->streams;
            if ((s->outgoing || !(s->id & 2)) && !s->stopped) {
                for (b = s->head; b && b->sent == b->length; b = b->next) {}
                if (b || (s->fin_requested && !s->fin_sent)) break;
            }
        } while (cursor != begin);
        bool finish = s && !s->stopped && s->fin_requested && !s->fin_sent && (!b || !b->next);
        ngtcp2_ssize consumed = -1;
        uint64_t now = cgp_now_ns();
        ngtcp2_path_storage path;
        ngtcp2_path_storage_zero(&path);
        ngtcp2_pkt_info info = {0};
        ngtcp2_ssize length = ngtcp2_conn_write_stream(p->quic, &path.path, &info,
                packet, sizeof(packet), &consumed, finish ? NGTCP2_WRITE_STREAM_FLAG_FIN : 0, (b || finish) ? s->id : -1,
                b ? b->bytes + b->sent : NULL, b ? b->length - b->sent : 0, now);
        if (length == NGTCP2_ERR_STREAM_SHUT_WR || length == NGTCP2_ERR_STREAM_NOT_FOUND) {
            s->stopped = true;
            s->awaiting_close = true;
            continue;
        }
        bool blocked = length == NGTCP2_ERR_STREAM_DATA_BLOCKED;
        if (blocked) {
            /* A flow-controlled application stream must not suppress transport
             * PING, ACK or retransmission work while the golfer is idle. */
            b = NULL;
            length = ngtcp2_conn_write_stream(p->quic, &path.path, &info,
                    packet, sizeof(packet), &consumed, 0, -1, NULL, 0, now);
        }
        if (length < 0) { fail_peer(p, (int)length); break; }
        if (consumed >= 0 && finish && (!b || (size_t)consumed == b->length - b->sent)) s->fin_sent=true;
        if (consumed > 0 && b) b->sent += (size_t)consumed;
        if (length == 0) {
            if (blocked) continue;
            break;
        }
        ssize_t sent = sendto(p->engine->socket, packet, (size_t)length, 0,
                              path.path.remote.addr, path.path.remote.addrlen);
        /* Local transient loss is repaired by QUIC retransmission. */
        if (sent < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != ENOBUFS)
            cgp_log("UDP send failed errno=%d", errno);
        ngtcp2_conn_update_pkt_tx_time(p->quic, now);
    }
    p->write_cursor=cursor ? cursor->id : -1;
    if(!p->failed)write_one_datagram(p);

}

void cgp_poll(cgp_engine *e) {
    read_packets(e);
    for (unsigned i = 0; i < CONFIG_CGP_MAX_CONNECTIONS; ++i) {
        cgp_peer *p = &e->peers[i];
        if (!p->quic) continue;
        if (!p->failed && ngtcp2_conn_get_expiry(p->quic) <= cgp_now_ns()) {
            int rc = ngtcp2_conn_handle_expiry(p->quic, cgp_now_ns());
            if (rc) fail_peer(p, rc);
        }
        if (!p->failed) {
            if (p->ready) {
                if (!p->notified) {
                    p->notified = true;
                    if (e->callbacks.connected) e->callbacks.connected(p,cgp_peer_identity(p),e->user);
                }
                if(p->menu_seen) {
                    cgp_stream **link=&p->streams;
                    while(*link) {
                        cgp_stream *stream=*link;
                        if(!stream->pending_menu){link=&stream->next;continue;}
                        uint8_t *bytes=stream->held;size_t length=stream->held_length;bool fin=stream->held_fin;
                        stream->held=NULL;stream->held_length=0;stream->pending_menu=false;stream->replaying=true;
                        int rc=receive_stream(p->quic,fin ? NGTCP2_STREAM_DATA_FLAG_FIN : 0,stream->id,0,bytes,length,p,NULL);
                        stream->replaying=false;free(bytes);
                        if(stream->transport_closed){
                            cgp_binding *binding=cgp_find_binding(p,stream->channel);
                            if(binding && (binding->kind&CGP_PRIVATE_RAW) && e->callbacks.raw_closed)
                                e->callbacks.raw_closed(p,stream->id,e->user);
                            *link=stream->next;free_stream(p,stream);
                        }else link=&stream->next;
                        if(rc){fail_peer(p,rc);break;}
                    }
                }
                if (e->callbacks.tick) e->callbacks.tick(p,e->user);
            }
            write_packets(p);
        }
        if (p->failed) drop_peer(p);
    }
}

unsigned cgp_connections(const cgp_engine *e) {
    unsigned count = 0;
    for (unsigned i = 0; i < CONFIG_CGP_MAX_CONNECTIONS; ++i)
        if (e->peers[i].quic && e->peers[i].ready) ++count;
    return count;
}

void cgp_statistics(const cgp_engine *e) {
    cgp_log("METRICS active_connections=%u capacity=%d", cgp_connections(e), CONFIG_CGP_MAX_CONNECTIONS);
    for (unsigned i = 0; i < CONFIG_CGP_MAX_CONNECTIONS; ++i) {
        const cgp_peer *p = &e->peers[i];
        if (p->quic) cgp_log("METRICS peer=%s tx=%" PRIu64 " rx=%" PRIu64
                            " queued_bytes=%zu suppressed=%" PRIu64,
                            p->name, p->sent_messages, p->received_messages, p->queued, p->suppressed_messages);
    }
}

const char *cgp_identity(const cgp_engine *e) {return e ? e->fingerprint : NULL;}
