#include "cJSON.h"
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

bool cgp_json_valid(const char *json, size_t length);
cJSON *cgp_json_extract(const char *json, size_t length);

static cJSON *extract(const char *json, size_t length)
{
    cJSON *object;
    assert(cgp_json_valid(json, length));
    object = cgp_json_extract(json, length);
    assert(cJSON_IsObject(object));
    return object;
}

static void assert_type(const cJSON *object, const char *type)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(object, "type");
    assert(cJSON_IsString(item) && !strcmp(item->valuestring, type));
}

int main(void)
{
    static const char escaped[] = "{\"t\\u0079pe\":\"sh\\u006ft\",\"shot_number\":42,"
                                  "\"final\":true,\"ball_speed_meters_per_second\":60.0,"
                                  "\"horizontal_launch_angle_degrees\":-2,"
                                  "\"ignored\":{\"type\":\"wrong\",\"quote\":\"\\\"[]{}\"}}";
    static const char nul_key[] = "{\"type\\u0000junk\":\"wrong\",\"type\":\"info\"}";
    static const char nul_type[] = "{\"type\":\"shot\\u0000junk\"}";
    static const char duplicate[] = "{\"type\":\"info\",\"type\":\"shot\"}";
    static const char deep_prefix[] = "{\"type\":\"shot\",\"shot_number\":42,"
        "\"ball_speed_meters_per_second\":60,\"horizontal_launch_angle_degrees\":-2,\"ignored\":";
    static const char flat_prefix[] = "{\"type\":\"info\",\"ignored\":\"";
    static const char many_prefix[] = "{\"type\":\"status\"";
    static const char number_prefix[] = "{\"type\":\"shot\",\"shot_number\":";
    const size_t size = 65535;
    char *buffer = malloc(size + 1);
    size_t at, depth;
    cJSON *object = extract(escaped, sizeof(escaped) - 1);
    assert_type(object, "shot");
    assert(cJSON_GetArraySize(object) == 5);
    assert(cJSON_GetObjectItemCaseSensitive(object, "shot_number")->valuedouble == 42);
    assert(cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(object, "final")));
    assert(!cJSON_HasObjectItem(object, "ignored"));
    cJSON_Delete(object);
    object = extract(nul_key, sizeof(nul_key) - 1);
    assert_type(object, "info");
    cJSON_Delete(object);
    object = extract(nul_type, sizeof(nul_type) - 1);
    assert(!cJSON_HasObjectItem(object, "type"));
    cJSON_Delete(object);
    object = extract(duplicate, sizeof(duplicate) - 1);
    assert_type(object, "info");
    assert(cJSON_GetArraySize(object) == 1);
    cJSON_Delete(object);

    assert(buffer);
    /* Exactly 65,535 bytes with over 32,000 nested arrays in an unknown field. */
    at = sizeof(deep_prefix) - 1;
    memcpy(buffer, deep_prefix, at);
    depth = (size - at - 2) / 2;
    memset(buffer + at, '[', depth); at += depth;
    buffer[at++] = '0';
    memset(buffer + at, ']', depth); at += depth;
    while (at < size - 1)
        buffer[at++] = ' ';
    buffer[at++] = '}';
    buffer[at] = '\0';
    assert(at == size);
    object = extract(buffer, size);
    assert_type(object, "shot");
    assert(cJSON_GetArraySize(object) == 4);
    cJSON_Delete(object);

    /* A maximum-size flat string likewise never becomes a cJSON allocation. */
    at = sizeof(flat_prefix) - 1;
    memcpy(buffer, flat_prefix, at);
    memset(buffer + at, 'a', size - at - 2);
    memcpy(buffer + size - 2, "\"}", 2);
    object = extract(buffer, size);
    assert_type(object, "info");
    assert(cJSON_GetArraySize(object) == 1);
    cJSON_Delete(object);

    /* Thousands of short unknown fields cannot allocate thousands of nodes. */
    at = sizeof(many_prefix) - 1;
    memcpy(buffer, many_prefix, at);
    while (at + 6 < size) {
        memcpy(buffer + at, ",\"x\":0", 6);
        at += 6;
    }
    memset(buffer + at, ' ', size - at - 1);
    buffer[size - 1] = '}';
    object = extract(buffer, size);
    assert_type(object, "status");
    assert(cJSON_GetArraySize(object) == 1);
    cJSON_Delete(object);

    /* A number too large for a double is malformed (RFC section 3.4). */
    at = sizeof(number_prefix) - 1;
    memcpy(buffer, number_prefix, at);
    memset(buffer + at, '1', size - at - 1);
    buffer[size - 1] = '}';
    assert(!cgp_json_valid(buffer, size));

    /* Long numeric tokens that fit a double remain valid JSON, but are not
     * useful counters. */
    memcpy(buffer + at, "0.", 2);
    memset(buffer + at + 2, '0', size - at - 4);
    buffer[size - 2] = '1';
    object = extract(buffer, size);
    assert_type(object, "shot");
    assert(!cJSON_HasObjectItem(object, "shot_number"));
    cJSON_Delete(object);
    free(buffer);
    puts("JSON extraction: full-size flat/deep objects accepted with bounded DOM");
    return 0;
}
