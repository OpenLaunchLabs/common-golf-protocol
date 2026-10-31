/* Test the actual firmware role code with a deterministic clock and RNG. */
#include "internal.h"
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

static uint64_t now;
static cgp_draft draft = CGP_DRAFT_3;
static uint32_t random_state = UINT32_C(0x8a07b32f);
static unsigned counts[CGP_CHANNELS];
static bool arrow_held;
static char arrow[16];
static const char *fail_event;
static unsigned publish_failures;

uint64_t cgp_now_ns(void) { return now; }

void cgp_random(void *destination, size_t length)
{
    unsigned char *bytes = destination;
    while (length--) {
        random_state ^= random_state << 13;
        random_state ^= random_state >> 17;
        random_state ^= random_state << 5;
        *bytes++ = (unsigned char)random_state;
    }
}

void cgp_log(const char *format, ...) { (void)format; }

bool cgp_publish(cgp_peer *peer, unsigned channel, const char *json)
{
    cJSON *parsed;
    assert(channel < CGP_CHANNELS);
    if (peer->tx[channel] == -2)
        return true;
    assert(channel == 0 || peer->started);
    assert(cgp_json_valid(json, strlen(json)));
    parsed = cJSON_Parse(json);
    assert(cJSON_IsObject(parsed));
    if (channel == 5) {
        const char *command = cJSON_GetObjectItemCaseSensitive(parsed, "command")->valuestring;
        const char *event = cJSON_GetObjectItemCaseSensitive(parsed, "event")->valuestring;
        if (fail_event && !strcmp(event, fail_event)) {
            fail_event = NULL;
            ++publish_failures;
            cJSON_Delete(parsed);
            return false;
        }
        if (!strcmp(event, "down")) {
            assert(!arrow_held);
            assert(!strcmp(command, "left") || !strcmp(command, "right") ||
                   !strcmp(command, "up") || !strcmp(command, "down"));
            arrow_held = true;
            snprintf(arrow, sizeof(arrow), "%s", command);
        } else if (!strcmp(event, "up")) {
            assert(arrow_held && !strcmp(arrow, command));
            arrow_held = false;
        } else {
            assert(!strcmp(event, "press") && !arrow_held);
        }
    }
    ++counts[channel];
    printf("%u\t%s\n", channel, json);
    cJSON_Delete(parsed);
    return true;
}

static cgp_peer *initialize(cgp_engine *engine, cgp_role role)
{
    unsigned i;
    memset(engine, 0, sizeof(*engine));
    engine->role = role;
    engine->draft = draft;
    snprintf(engine->name, sizeof(engine->name), "Demo \"quoted\" \\ name\n");
    engine->peers[0].engine = engine;
    for (i = 0; i < CGP_CHANNELS; ++i)
        engine->peers[0].tx[i] = -1;
    return &engine->peers[0];
}

int main(int argc, char **argv)
{
    if (argc > 1) draft = (cgp_draft)atoi(argv[1]);
    assert(draft >= CGP_DRAFT_1 && draft <= CGP_DRAFT_3);
    cgp_engine engine;
    cgp_peer *peer = initialize(&engine, CGP_LAUNCH_MONITOR);
    cJSON *message;
    unsigned i;
    cgp_mock_start(peer);
    cgp_mock_start(peer);
    assert(counts[0] == 1);
    for (i = 0; i < 10; ++i) {
        now = peer->next_action;
        cgp_mock_tick(peer);
        assert(peer->shot_number == i + 1);
    }
    assert(counts[1] == 10 && counts[3] == 10 && counts[8] == 10);
    peer->tx[1] = -2;
    now = peer->next_action;
    cgp_mock_tick(peer);
    assert(counts[1] == 10 && peer->shot_number == 11);

    peer = initialize(&engine, CGP_CONTROL_BOX);
    cgp_mock_start(peer);
    fail_event = "down";
    for (i = 0; i < 20000; ++i) {
        now += UINT64_C(50000000);
        cgp_mock_tick(peer);
        if (publish_failures == 1 && !fail_event)
            fail_event = "up";
    }
    if (peer->holding) {
        now = peer->release_at;
        peer->next_action = UINT64_MAX;
        cgp_mock_tick(peer);
    }
    assert(!arrow_held);
    assert(publish_failures == 2); /* Failed down ignored, failed up retried. */

    peer = initialize(&engine, CGP_SIMULATOR);
    message = cJSON_Parse("{\"type\":\"shot\",\"shot_number\":7,"
                          "\"ball_speed_meters_per_second\":60,"
                          "\"vertical_launch_angle_degrees\":12,"
                          "\"horizontal_launch_angle_degrees\":-2,\"final\":false}");
    assert(message);
    if (draft == CGP_DRAFT_3) {
        cJSON_AddStringToObject(message, "shot_uuid", "017f22e2-79b0-7cc3-98c4-dc0c0c07398f");
        cgp_mock_receive(peer, 1, message);
        assert(peer->started && counts[0] == 3 && counts[6] == 1 && counts[7] == 1);
        cJSON_ReplaceItemInObjectCaseSensitive(message, "shot_uuid", cJSON_CreateString("invalid"));
        cgp_mock_receive(peer, 1, message);
        assert(counts[7] == 1);
        cJSON_Delete(message);
        return 0;
    }
    /* Incoming streams may arrive before the info stream. */
    cgp_mock_receive(peer, 1, message);
    assert(peer->started && counts[0] == 3 && counts[6] == 1 && counts[7] == 0);
    cJSON_ReplaceItemInObjectCaseSensitive(message, "final", cJSON_CreateTrue());
    cgp_mock_receive(peer, 1, message);
    assert(counts[7] == 1);
    cJSON_DeleteItemFromObjectCaseSensitive(message, "final");
    cgp_mock_receive(peer, 1, message);
    assert(counts[7] == 2);
    cJSON_ReplaceItemInObjectCaseSensitive(message, "shot_number", cJSON_CreateNumber(-1));
    cgp_mock_receive(peer, 1, message);
    assert(counts[7] == 2);
    cJSON_Delete(message);
    return 0;
}
