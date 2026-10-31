#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

bool cgp_json_valid(const char *json, size_t length);

int main(void)
{
    static const char *const valid[] = {
        "{}", " \t\n\r{\"type\":\"info\"}\r\n",
        "{\"n\":-0,\"f\":1.25,\"exponent\":-1.2E+3,\"tiny\":3e-12}",
        "{\"a\":[true,false,null,[],{},[1,2]],\"nested\":{\"x\":0}}",
        "{\"escaped\":\"\\\"\\\\\\/\\b\\f\\n\\r\\t\\u0000\\uD83C\\uDFCC\"}",
        "{\"unicode\":\"\xc2\xa3\xe2\x82\xac\xf0\x9f\x8f\x8c\"}",
        "{\"maxunicode\":\"\xf4\x8f\xbf\xbf\"}",
        "{\"duplicate\":1,\"duplicate\":2}"
    };
    static const char *const invalid[] = {
        "", " ", "[]", "null", "true", "1", "\"string\"", "{}{}", "{}x",
        "{", "}", "{1:2}", "{\"a\"}", "{\"a\":}", "{\"a\":1,}",
        "{\"a\":1 \"b\":2}", "{\"a\":,}", "{\"a\":1:2}",
        "{\"a\":[,]}", "{\"a\":[1,]}", "{\"a\":[1 2]}", "{\"a\":[}",
        "{\"n\":01}", "{\"n\":+1}", "{\"n\":.1}", "{\"n\":1.}",
        "{\"n\":1e}", "{\"n\":1e+}", "{\"n\":--1}", "{\"n\":NaN}",
        "{\"n\":Infinity}", "{\"n\":truefalse}", "{\"n\":True}",
        "{\"s\":\"unterminated}", "{\"s\":\"raw\nnewline\"}",
        "{\"s\":\"\\x12\"}", "{\"s\":\"\\u123\"}", "{\"s\":\"\\uZZZZ\"}",
        "{\"s\":\"\xc0\xaf\"}", "{\"s\":\"\xc1\xbf\"}",
        "{\"s\":\"\xe0\x80\x80\"}", "{\"s\":\"\xed\xa0\x80\"}",
        "{\"s\":\"\xf0\x80\x80\x80\"}", "{\"s\":\"\xf4\x90\x80\x80\"}",
        "{\"s\":\"\xf5\x80\x80\x80\"}", "{\"s\":\"\x80\"}",
        "{\"s\":\"\xc2\"}", "{\"s\":\"\xe2\x82\"}",
        "{\"s\":\"\xf0\x9f\x8f\"}", "{\"s\":\"\xff\"}",
        "\xef\xbb\xbf{}", "{\v\"x\":1}", "{/* comment */}"
    };
    static const char embedded_nul[] = "{\"a\":\"x\0y\"}";
    static const char trailing_nul[] = "{}\0{}";
    const size_t depth = 32000;
    const size_t nested_size = 2 * depth + 7;
    char *nested = malloc(nested_size);
    size_t i;
    assert(!cgp_json_valid(NULL, 0));
    assert(!cgp_json_valid(NULL, 4));
    for (i = 0; i < sizeof(valid) / sizeof(valid[0]); ++i)
        assert(cgp_json_valid(valid[i], strlen(valid[i])));
    for (i = 0; i < sizeof(invalid) / sizeof(invalid[0]); ++i)
        assert(!cgp_json_valid(invalid[i], strlen(invalid[i])));
    assert(!cgp_json_valid(embedded_nul, sizeof(embedded_nul) - 1));
    assert(!cgp_json_valid(trailing_nul, sizeof(trailing_nul) - 1));
    /* No recursion: a legal 64 KB object can have very deep nested arrays. */
    assert(nested);
    memcpy(nested, "{\"a\":", 5);
    memset(nested + 5, '[', depth);
    nested[5 + depth] = '0';
    memset(nested + 6 + depth, ']', depth);
    nested[nested_size - 1] = '}';
    assert(cgp_json_valid(nested, nested_size));
    assert(!cgp_json_valid(nested, nested_size - 1));
    free(nested);
    puts("JSON grammar and UTF-8: all tests passed");
    return 0;
}
