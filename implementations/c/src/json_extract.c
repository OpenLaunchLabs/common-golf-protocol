/* SPDX-License-Identifier: Apache-2.0 */
#include "cJSON.h"

#include <stdbool.h>
#include <stddef.h>
#include <string.h>

/* The complete object has already passed cgp_json_valid. Avoid constructing a
 * DOM for unknown fields: a legal 65,535-byte object can contain thousands of
 * nodes or nested arrays, which cannot fit cJSON's recursive task stack. The
 * mock only needs a few primitive top-level fields. */
static const char *space(const char *at, const char *end)
{
    while (at < end && (*at == ' ' || *at == '\t' || *at == '\r' || *at == '\n'))
        ++at;
    return at;
}

static const char *string_end(const char *at, const char *end)
{
    if (at == end || *at++ != '"')
        return NULL;
    while (at < end) {
        if (*at == '"')
            return at + 1;
        if (*at++ == '\\') {
            if (at == end)
                return NULL;
            ++at;
        }
    }
    return NULL;
}

static unsigned hex(unsigned char c)
{
    if (c >= '0' && c <= '9')
        return c - '0';
    if (c >= 'a' && c <= 'f')
        return c - 'a' + 10;
    return c - 'A' + 10;
}

/* Known keys and channel types are ASCII. Decode escapes so e.g. \u0074ype
 * works, while embedded escaped NUL cannot alias a known C string key. */
static bool ascii_string(const char *at, const char *end, char *out, size_t capacity)
{
    size_t used = 0;
    if (end - at < 2 || *at++ != '"' || end[-1] != '"')
        return false;
    --end;
    while (at < end) {
        unsigned c = (unsigned char)*at++;
        if (c == '\\') {
            if (at == end)
                return false;
            c = (unsigned char)*at++;
            if (c == 'u') {
                unsigned i;
                if (end - at < 4)
                    return false;
                c = 0;
                for (i = 0; i < 4; ++i)
                    c = (c << 4) | hex((unsigned char)*at++);
            } else if (c == 'b') c = '\b';
            else if (c == 'f') c = '\f';
            else if (c == 'n') c = '\n';
            else if (c == 'r') c = '\r';
            else if (c == 't') c = '\t';
        }
        if (!c || c > 0x7f || used + 1 >= capacity)
            return false;
        out[used++] = (char)c;
    }
    out[used] = '\0';
    return true;
}

static const char *value_end(const char *at, const char *end)
{
    size_t depth = 0;
    if (at == end)
        return NULL;
    if (*at == '"')
        return string_end(at, end);
    if (*at != '{' && *at != '[') {
        while (at < end && *at != ',' && *at != '}' && *at != ']' &&
               *at != ' ' && *at != '\t' && *at != '\r' && *at != '\n')
            ++at;
        return at;
    }
    do {
        if (at == end)
            return NULL;
        if (*at == '"') {
            at = string_end(at, end);
            if (!at)
                return NULL;
        } else {
            if (*at == '[' || *at == '{')
                ++depth;
            else if (*at == ']' || *at == '}')
                --depth;
            ++at;
        }
    } while (depth);
    return at;
}

cJSON *cgp_json_extract(const char *json, size_t length)
{
    static const char *const names[] = {
        "type", "final", "shot_number", "ball_speed_meters_per_second",
        "horizontal_launch_angle_degrees"
    };
    cJSON *object = cJSON_CreateObject();
    const char *end = json + length;
    const char *at = space(json, end);
    unsigned seen = 0;
    if (!object)
        return NULL;
    if (at == end || *at++ != '{')
        goto malformed;
    at = space(at, end);
    while (at < end && *at != '}') {
        char key[64];
        const char *key_end = string_end(at, end);
        const char *start, *finish;
        bool known_key;
        size_t i;
        if (!key_end)
            goto malformed;
        known_key = ascii_string(at, key_end, key, sizeof(key));
        at = space(key_end, end);
        if (at == end || *at++ != ':')
            goto malformed;
        start = space(at, end);
        finish = value_end(start, end);
        if (!finish)
            goto malformed;
        if (known_key) {
            for (i = 0; i < sizeof(names) / sizeof(names[0]); ++i) {
                cJSON *item = NULL;
                size_t size = (size_t)(finish - start);
                if (strcmp(key, names[i]) || (seen & (1U << i)))
                    continue;
                seen |= 1U << i;
                if (i == 0) {
                    char type[32];
                    if (ascii_string(start, finish, type, sizeof(type))) {
                        item = cJSON_CreateString(type);
                        if (!item)
                            goto malformed;
                    }
                } else if (size < 128 && size && *start != '"' &&
                           *start != '{' && *start != '[') {
                    char token[128];
                    memcpy(token, start, size);
                    token[size] = '\0';
                    item = cJSON_ParseWithLength(token, size + 1);
                    if (!item)
                        goto malformed;
                }
                if (item && !cJSON_AddItemToObject(object, names[i], item)) {
                    cJSON_Delete(item);
                    goto malformed;
                }
                break;
            }
        }
        at = space(finish, end);
        if (at < end && *at == ',')
            at = space(at + 1, end);
        else
            break;
    }
    if (at == end || *at != '}')
        goto malformed;
    return object;
malformed:
    cJSON_Delete(object);
    return NULL;
}
