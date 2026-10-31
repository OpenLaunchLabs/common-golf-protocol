#ifndef CGP_FRAMING_H
#define CGP_FRAMING_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define CGP_MAX_MESSAGE_BYTES UINT32_C(65535)

/* Return 0 to accept this stream's channel, or 1 to decline it. */
typedef int (*cgp_channel_callback)(void *user, uint32_t channel);
/* Return 0 for one valid JSON object, or 2 for malformed content.
 * The payload has a convenience NUL terminator; length excludes it and must
 * be used to reject embedded NULs or trailing content. The pointer is valid
 * only during the callback. Framing itself does not validate JSON. */
typedef int (*cgp_json_callback)(void *user, uint32_t channel,
                               const char *payload, size_t length);

typedef struct {
    uint32_t channel;
    bool ended;
    int status;
    uint8_t header[4];
    size_t header_used;
    bool has_channel;
    bool has_length;
    uint32_t payload_length;
    size_t payload_used;
    char *payload;
} cgp_rx;

/* Initialize before first use. Free before reinitializing an active parser. */
void cgp_rx_init(cgp_rx *rx);
/* Release an incomplete frame and reset the object to its initial state. */
void cgp_rx_free(cgp_rx *rx);
/* Feed ordered bytes from one QUIC stream, including its channel prefix.
 * Returns 0 on success, 1 if declined, 2 if malformed, -1 on allocation
 * failure. Errors latch and further bytes are ignored. FIN marks ended;
 * FIN before a complete channel prefix or in a frame is malformed. Bytes
 * delivered after FIN are ignored. NULL callbacks accept all content.
 * Stream ownership, JSON validation, and STOP_SENDING are the caller's job.
 */
int cgp_rx_feed(cgp_rx *rx, const uint8_t *data, size_t length, bool fin,
                cgp_channel_callback on_channel, cgp_json_callback on_json,
                void *user);

void cgp_write_u32(uint8_t out[4], uint32_t value);

#endif
