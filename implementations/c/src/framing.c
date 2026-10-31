#include "framing.h"

#include <stdlib.h>
#include <string.h>

void cgp_rx_init(cgp_rx *rx)
{
    memset(rx, 0, sizeof(*rx));
}

void cgp_rx_free(cgp_rx *rx)
{
    free(rx->payload);
    cgp_rx_init(rx);
}

void cgp_write_u32(uint8_t out[4], uint32_t value)
{
    out[0] = (uint8_t)(value >> 24);
    out[1] = (uint8_t)(value >> 16);
    out[2] = (uint8_t)(value >> 8);
    out[3] = (uint8_t)value;
}

static uint32_t read_u32(const uint8_t bytes[4])
{
    return ((uint32_t)bytes[0] << 24) | ((uint32_t)bytes[1] << 16) |
           ((uint32_t)bytes[2] << 8) | (uint32_t)bytes[3];
}

static int reject(cgp_rx *rx, int status, bool fin)
{
    free(rx->payload);
    rx->payload = NULL;
    rx->status = status;
    rx->ended = rx->ended || fin;
    return status;
}

static int deliver(cgp_rx *rx, cgp_json_callback on_json, void *user)
{
    int result;
    rx->payload[rx->payload_length] = '\0';
    result = on_json ? on_json(user, rx->channel, rx->payload,
                              rx->payload_length) : 0;
    free(rx->payload);
    rx->payload = NULL;
    rx->payload_length = 0;
    rx->payload_used = 0;
    rx->has_length = false;
    return result;
}

int cgp_rx_feed(cgp_rx *rx, const uint8_t *data, size_t length, bool fin,
                cgp_channel_callback on_channel, cgp_json_callback on_json,
                void *user)
{
    if (rx->status || rx->ended) {
        rx->ended = rx->ended || fin;
        return rx->status;
    }

    while (length) {
        if (!rx->has_channel || !rx->has_length) {
            size_t take = sizeof(rx->header) - rx->header_used;
            if (take > length)
                take = length;
            memcpy(rx->header + rx->header_used, data, take);
            rx->header_used += take;
            data += take;
            length -= take;
            if (rx->header_used != sizeof(rx->header))
                continue;
            rx->header_used = 0;

            if (!rx->has_channel) {
                rx->channel = read_u32(rx->header);
                rx->has_channel = true;
                if (on_channel && on_channel(user, rx->channel))
                    return reject(rx, 1, fin);
            } else {
                rx->payload_length = read_u32(rx->header);
                if (rx->payload_length > CGP_MAX_MESSAGE_BYTES)
                    return reject(rx, 2, fin);
                rx->payload = malloc((size_t)rx->payload_length + 1);
                if (!rx->payload)
                    return reject(rx, -1, fin);
                rx->has_length = true;
                if (!rx->payload_length && deliver(rx, on_json, user))
                    return reject(rx, 2, fin);
            }
        } else {
            size_t take = (size_t)rx->payload_length - rx->payload_used;
            if (take > length)
                take = length;
            memcpy(rx->payload + rx->payload_used, data, take);
            rx->payload_used += take;
            data += take;
            length -= take;
            if (rx->payload_used == rx->payload_length &&
                deliver(rx, on_json, user))
                return reject(rx, 2, fin);
        }
    }

    if (fin) {
        rx->ended = true;
        if (!rx->has_channel || rx->header_used || rx->has_length)
            return reject(rx, 2, true);
    }
    return 0;
}
