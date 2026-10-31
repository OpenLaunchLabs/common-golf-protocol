/* SPDX-License-Identifier: Apache-2.0 */
#include <math.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* cJSON deliberately accepts some non-JSON input. Validate the wire grammar
 * before parsing values with cJSON. An explicit stack keeps nested input off
 * the task call stack. Typical golf objects need only the local 64 bytes. */
enum state {
    OBJECT_FIRST = 1, OBJECT_KEY, OBJECT_COLON, OBJECT_VALUE, OBJECT_AFTER,
    ARRAY_FIRST, ARRAY_VALUE, ARRAY_AFTER
};

typedef struct {
    const unsigned char *at, *end;
    unsigned char local[64];
    unsigned char *stack;
    size_t depth, capacity;
} parser;

static void whitespace(parser *p)
{
    while (p->at < p->end && (*p->at == ' ' || *p->at == '\t' ||
                            *p->at == '\r' || *p->at == '\n'))
        ++p->at;
}

static bool push(parser *p, enum state state)
{
    if (p->depth == p->capacity) {
        unsigned char *next;
        size_t capacity;
        if (p->capacity > SIZE_MAX / 2)
            return false;
        capacity = p->capacity * 2;
        next = malloc(capacity);
        if (!next)
            return false;
        memcpy(next, p->stack, p->depth);
        if (p->stack != p->local)
            free(p->stack);
        p->stack = next;
        p->capacity = capacity;
    }
    p->stack[p->depth++] = (unsigned char)state;
    return true;
}

static bool hex_digit(unsigned char c)
{
    return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') ||
           (c >= 'A' && c <= 'F');
}

/* The leading byte has already been consumed. Reject overlong encodings,
 * UTF-16 surrogate code points, and code points above U+10FFFF. */
static bool utf8_tail(parser *p, unsigned char lead)
{
    size_t extra, i;
    unsigned char low = 0x80, high = 0xbf;
    if (lead >= 0xc2 && lead <= 0xdf) {
        extra = 1;
    } else if (lead >= 0xe0 && lead <= 0xef) {
        extra = 2;
        if (lead == 0xe0)
            low = 0xa0;
        if (lead == 0xed)
            high = 0x9f;
    } else if (lead >= 0xf0 && lead <= 0xf4) {
        extra = 3;
        if (lead == 0xf0)
            low = 0x90;
        if (lead == 0xf4)
            high = 0x8f;
    } else {
        return false;
    }
    if ((size_t)(p->end - p->at) < extra || p->at[0] < low || p->at[0] > high)
        return false;
    for (i = 1; i < extra; ++i) {
        if (p->at[i] < 0x80 || p->at[i] > 0xbf)
            return false;
    }
    p->at += extra;
    return true;
}

static bool string_value(parser *p)
{
    if (p->at == p->end || *p->at++ != '"')
        return false;
    while (p->at < p->end) {
        unsigned char c = *p->at++;
        if (c == '"')
            return true;
        if (c < 0x20)
            return false;
        if (c >= 0x80) {
            if (!utf8_tail(p, c))
                return false;
        } else if (c == '\\') {
            if (p->at == p->end)
                return false;
            c = *p->at++;
            if (c == 'u') {
                size_t i;
                if (p->end - p->at < 4)
                    return false;
                for (i = 0; i < 4; ++i) {
                    if (!hex_digit(*p->at++))
                        return false;
                }
            } else if (c != '"' && c != '\\' && c != '/' && c != 'b' &&
                       c != 'f' && c != 'n' && c != 'r' && c != 't') {
                return false;
            }
        }
    }
    return false;
}

static bool digit(unsigned char c)
{
    return c >= '0' && c <= '9';
}

/* Numbers must fit a double (RFC section 3.4); cJSON would otherwise turn
 * 1e999 into infinity. The token has already matched the JSON grammar. Work
 * out its decimal magnitude without copying it: a legal token can be 64 KB.
 * Only a number at the 1e308 boundary is handed to strtod, as its leading
 * significant digits (no decimal point, so the locale does not matter). */
static bool finite_number(const unsigned char *at, const unsigned char *end)
{
    char digits[48];
    size_t kept = 0;
    long magnitude = 0, exponent = 0;
    bool seen = false, fraction = false, negative = false;
    if (*at == '-')
        ++at;
    for (; at < end && *at != 'e' && *at != 'E'; ++at) {
        if (*at == '.') {
            fraction = true;
            continue;
        }
        if (!seen && *at == '0') {
            if (fraction)
                --magnitude;
            continue;
        }
        if (!seen) {
            seen = true;
            if (fraction)
                --magnitude;
        } else if (!fraction) {
            ++magnitude;
        }
        if (kept < 40)
            digits[kept++] = (char)*at;
    }
    if (!seen)
        return true; /* zero */
    if (at < end) {
        ++at;
        if (*at == '+' || *at == '-')
            negative = *at++ == '-';
        for (; at < end; ++at) {
            if (exponent < 100000)
                exponent = exponent * 10 + (*at - '0');
        }
    }
    magnitude += negative ? -exponent : exponent;
    if (magnitude != 308)
        return magnitude < 308;
    snprintf(digits + kept, sizeof(digits) - kept, "e%ld", 308 - (long)(kept - 1));
    return isfinite(strtod(digits, NULL));
}

static bool number_value(parser *p)
{
    const unsigned char *start = p->at;
    if (*p->at == '-')
        ++p->at;
    if (p->at == p->end)
        return false;
    if (*p->at == '0') {
        ++p->at;
    } else {
        if (*p->at < '1' || *p->at > '9')
            return false;
        do {
            ++p->at;
        } while (p->at < p->end && digit(*p->at));
    }
    if (p->at < p->end && *p->at == '.') {
        ++p->at;
        if (p->at == p->end || !digit(*p->at))
            return false;
        do {
            ++p->at;
        } while (p->at < p->end && digit(*p->at));
    }
    if (p->at < p->end && (*p->at == 'e' || *p->at == 'E')) {
        ++p->at;
        if (p->at < p->end && (*p->at == '+' || *p->at == '-'))
            ++p->at;
        if (p->at == p->end || !digit(*p->at))
            return false;
        do {
            ++p->at;
        } while (p->at < p->end && digit(*p->at));
    }
    return finite_number(start, p->at);
}

static bool literal(parser *p, const char *value, size_t length)
{
    if ((size_t)(p->end - p->at) < length || memcmp(p->at, value, length))
        return false;
    p->at += length;
    return true;
}

static bool value(parser *p)
{
    if (p->at == p->end)
        return false;
    switch (*p->at) {
    case '{': ++p->at; return push(p, OBJECT_FIRST);
    case '[': ++p->at; return push(p, ARRAY_FIRST);
    case '"': return string_value(p);
    case 't': return literal(p, "true", 4);
    case 'f': return literal(p, "false", 5);
    case 'n': return literal(p, "null", 4);
    default:
        return (*p->at == '-' || digit(*p->at)) && number_value(p);
    }
}

bool cgp_json_valid(const char *json, size_t length)
{
    parser p;
    bool valid = false;
    if (!json || !length)
        return false;
    p.at = (const unsigned char *)json;
    p.end = p.at + length;
    p.stack = p.local;
    p.depth = 0;
    p.capacity = sizeof(p.local);
    whitespace(&p);
    if (p.at == p.end || *p.at++ != '{' || !push(&p, OBJECT_FIRST))
        goto done;
    while (p.depth) {
        unsigned char state;
        whitespace(&p);
        if (p.at == p.end)
            goto done;
        state = p.stack[p.depth - 1];
        if (state == OBJECT_FIRST && *p.at == '}') {
            ++p.at;
            --p.depth;
        } else if (state == OBJECT_FIRST || state == OBJECT_KEY) {
            if (!string_value(&p))
                goto done;
            p.stack[p.depth - 1] = OBJECT_COLON;
        } else if (state == OBJECT_COLON) {
            if (*p.at++ != ':')
                goto done;
            p.stack[p.depth - 1] = OBJECT_VALUE;
        } else if (state == OBJECT_VALUE || state == ARRAY_VALUE ||
                   (state == ARRAY_FIRST && *p.at != ']')) {
            p.stack[p.depth - 1] = state == OBJECT_VALUE ? OBJECT_AFTER : ARRAY_AFTER;
            if (!value(&p))
                goto done;
        } else if (state == OBJECT_AFTER) {
            if (*p.at == '}') {
                ++p.at;
                --p.depth;
            } else if (*p.at++ == ',') {
                p.stack[p.depth - 1] = OBJECT_KEY;
            } else {
                goto done;
            }
        } else if (state == ARRAY_FIRST || state == ARRAY_AFTER) {
            if (*p.at == ']') {
                ++p.at;
                --p.depth;
            } else if (*p.at++ == ',') {
                p.stack[p.depth - 1] = ARRAY_VALUE;
            } else {
                goto done;
            }
        } else {
            goto done;
        }
    }
    whitespace(&p);
    valid = p.at == p.end;
done:
    if (p.stack != p.local)
        free(p.stack);
    return valid;
}
