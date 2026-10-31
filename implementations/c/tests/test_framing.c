#include "framing.h"

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    uint32_t channel;
    const char *body;
    size_t body_length;
    unsigned channels;
    unsigned messages;
    int reject_channel;
    int reject_json;
} observer;

static int on_channel(void *user, uint32_t channel)
{
    observer *o = user;
    assert(channel == o->channel);
    ++o->channels;
    return o->reject_channel;
}

static int on_json(void *user, uint32_t channel, const char *body, size_t length)
{
    observer *o = user;
    assert(channel == o->channel);
    assert(length == o->body_length);
    assert(!memcmp(body, o->body, length));
    assert(body[length] == '\0');
    ++o->messages;
    return o->reject_json;
}

static int feed(cgp_rx *rx, observer *o, const uint8_t *data, size_t size, bool fin)
{
    return cgp_rx_feed(rx, data, size, fin, on_channel, on_json, o);
}

static observer object_observer(void)
{
    observer o = {1, "{}", 2, 0, 0, 0, 0};
    return o;
}

static void test_every_split(void)
{
    static const uint8_t message[] = {0, 0, 0, 1, 0, 0, 0, 2, '{', '}'};
    size_t split;
    for (split = 0; split <= sizeof(message); ++split) {
        cgp_rx rx;
        observer o = object_observer();
        cgp_rx_init(&rx);
        assert(feed(&rx, &o, message, split, false) == 0);
        assert(feed(&rx, &o, message + split, sizeof(message) - split, true) == 0);
        assert(o.channels == 1 && o.messages == 1);
        assert(rx.channel == 1 && rx.ended && !rx.payload && !rx.status);
        /* An already completed stream must not deliver messages again. */
        assert(feed(&rx, &o, message, sizeof(message), false) == 0);
        assert(o.channels == 1 && o.messages == 1);
        cgp_rx_free(&rx);
    }
}

static void test_coalesced_and_fragmented(void)
{
    static const uint8_t messages[] = {
        0, 0, 0, 1,
        0, 0, 0, 2, '{', '}',
        0, 0, 0, 2, '{', '}',
        0, 0, 0, 2, '{', '}'
    };
    size_t chunk;
    for (chunk = 1; chunk <= sizeof(messages); ++chunk) {
        size_t offset = 0;
        cgp_rx rx;
        observer o = object_observer();
        cgp_rx_init(&rx);
        while (offset < sizeof(messages)) {
            size_t n = sizeof(messages) - offset;
            if (n > chunk)
                n = chunk;
            assert(feed(&rx, &o, messages + offset, n, false) == 0);
            offset += n;
        }
        assert(feed(&rx, &o, NULL, 0, true) == 0);
        assert(o.channels == 1 && o.messages == 3 && rx.ended);
        cgp_rx_free(&rx);
    }
}

static void test_fin_boundaries(void)
{
    static const uint8_t message[] = {0, 0, 0, 1, 0, 0, 0, 2, '{', '}'};
    size_t length;
    for (length = 0; length <= sizeof(message); ++length) {
        const int expected = length == 4 || length == sizeof(message) ? 0 : 2;
        cgp_rx rx;
        observer o = object_observer();
        cgp_rx_init(&rx);
        assert(feed(&rx, &o, message, length, true) == expected);
        assert(rx.ended && !rx.payload && rx.status == expected);
        assert(o.messages == (length == sizeof(message) ? 1U : 0U));
        cgp_rx_free(&rx);
    }
}

static void test_rejections(void)
{
    static const uint8_t message[] = {0, 0, 0, 1, 0, 0, 0, 2, '{', '}'};
    cgp_rx rx;
    observer o = object_observer();
    cgp_rx_init(&rx);
    o.reject_channel = 1;
    assert(feed(&rx, &o, message, sizeof(message), false) == 1);
    assert(o.channels == 1 && o.messages == 0 && !rx.payload && !rx.ended);
    o.reject_channel = 0;
    assert(feed(&rx, &o, message, sizeof(message), true) == 1);
    assert(o.channels == 1 && o.messages == 0 && rx.ended);
    cgp_rx_free(&rx);

    o = object_observer();
    o.reject_json = 2;
    assert(feed(&rx, &o, message, sizeof(message), false) == 2);
    assert(o.channels == 1 && o.messages == 1 && !rx.payload);
    o.reject_json = 0;
    assert(feed(&rx, &o, message, sizeof(message), true) == 2);
    assert(o.channels == 1 && o.messages == 1 && rx.ended);
    cgp_rx_free(&rx);
    /* Resetting after a rejected stream allows the object to be reused. */
    o = object_observer();
    assert(feed(&rx, &o, message, sizeof(message), true) == 0);
    assert(o.channels == 1 && o.messages == 1);
    cgp_rx_free(&rx);
}

static void test_lengths(void)
{
    uint8_t header[8] = {0, 0, 0, 1, 0, 0, 0, 0};
    const uint32_t bad_lengths[] = {65536, UINT32_MAX};
    size_t i;
    cgp_rx rx;
    observer o = object_observer();
    cgp_rx_init(&rx);
    o.body = "";
    o.body_length = 0;
    o.reject_json = 2; /* An empty frame is not a JSON object. */
    assert(feed(&rx, &o, header, sizeof(header), true) == 2);
    assert(o.messages == 1 && !rx.payload);
    cgp_rx_free(&rx);
    for (i = 0; i < sizeof(bad_lengths) / sizeof(bad_lengths[0]); ++i) {
        o = object_observer();
        cgp_write_u32(header + 4, bad_lengths[i]);
        assert(feed(&rx, &o, header, sizeof(header), false) == 2);
        assert(o.channels == 1 && !o.messages && !rx.payload);
        assert(feed(&rx, &o, NULL, 0, true) == 2 && rx.ended);
        cgp_rx_free(&rx);
    }
}

static void test_maximum_frame(void)
{
    const size_t body_size = CGP_MAX_MESSAGE_BYTES;
    const size_t total = 8 + body_size;
    const size_t chunk_sizes[] = {1, 7, 4096, 65543};
    uint8_t *bytes = malloc(total);
    size_t i;
    assert(bytes);
    cgp_write_u32(bytes, UINT32_MAX);
    cgp_write_u32(bytes + 4, (uint32_t)body_size);
    memcpy(bytes + 8, "{\"x\":\"", 6);
    memset(bytes + 14, 'a', body_size - 8);
    memcpy(bytes + total - 2, "\"}", 2);
    for (i = 0; i < sizeof(chunk_sizes) / sizeof(chunk_sizes[0]); ++i) {
        cgp_rx rx;
        size_t offset = 0;
        observer o = {UINT32_MAX, (const char *)bytes + 8, body_size, 0, 0, 0, 0};
        cgp_rx_init(&rx);
        while (offset < total) {
            size_t n = total - offset;
            if (n > chunk_sizes[i])
                n = chunk_sizes[i];
            assert(feed(&rx, &o, bytes + offset, n, offset + n == total) == 0);
            offset += n;
        }
        assert(o.channels == 1 && o.messages == 1 && rx.ended && !rx.payload);
        cgp_rx_free(&rx);
    }
    free(bytes);
}

static void test_free_partial(void)
{
    static const uint8_t message[] = {0, 0, 0, 1, 0, 0, 0, 2, '{'};
    cgp_rx rx;
    observer o = object_observer();
    cgp_rx_init(&rx);
    assert(feed(&rx, &o, message, sizeof(message), false) == 0);
    assert(rx.payload && rx.payload_used == 1 && !o.messages);
    cgp_rx_free(&rx);
    assert(!rx.payload && !rx.has_channel && !rx.status && !rx.ended);
    cgp_rx_free(&rx); /* Cleanup can safely be repeated. */
}

int main(void)
{
    test_every_split();
    test_coalesced_and_fragmented();
    test_fin_boundaries();
    test_rejections();
    test_lengths();
    test_maximum_frame();
    test_free_partial();
    puts("framing: all tests passed");
    return 0;
}
