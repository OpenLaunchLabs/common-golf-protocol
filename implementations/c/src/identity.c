#include "identity.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifndef ESP_PLATFORM
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#endif
#include <ngtcp2/ngtcp2_crypto_wolfssl.h>
#include <wolfssl/wolfcrypt/asn_public.h>
#include <wolfssl/wolfcrypt/ed25519.h>
#include <wolfssl/wolfcrypt/random.h>
#ifdef ESP_PLATFORM
#include "nvs.h"
#endif

#define CERT_CAPACITY 1536
#define KEY_CAPACITY 128

static void clear_secret(void *ptr, size_t length)
{
    volatile unsigned char *p = ptr;
    while (length--) *p++ = 0;
}

static void hex_key(const unsigned char key[32], char out[65])
{
    static const char hex[] = "0123456789abcdef";
    for (size_t i = 0; i < 32; ++i) {
        out[2 * i] = hex[key[i] >> 4];
        out[2 * i + 1] = hex[key[i] & 15];
    }
    out[64] = '\0';
}

static int make_identity(unsigned char *key_der, size_t *key_size,
                         unsigned char *cert_der, size_t *cert_size)
{
    int result = -1, length;
    WC_RNG rng;
    ed25519_key key;
    Cert *cert = NULL;
    if (wc_InitRng(&rng) != 0) return -1;
    if (wc_ed25519_init(&key) != 0) {
        wc_FreeRng(&rng);
        return -1;
    }
    if (wc_ed25519_make_key(&rng, 32, &key) != 0) goto done;
    length = wc_Ed25519KeyToDer(&key, key_der, (word32)*key_size);
    if (length <= 0) goto done;
    *key_size = (size_t)length;
    cert = calloc(1, sizeof(*cert));
    if (!cert || wc_InitCert(cert) != 0) goto done;
    cert->sigType = CTC_ED25519;
    cert->keyType = ED25519_KEY;
    strcpy(cert->subject.commonName, "Common Golf reference");
    /* Fixed syntactically valid dates avoid needing a wall clock. CGP peers
     * ignore validity dates, including these dates when they expire. */
    static const unsigned char before[] = "\x17\x0d" "240101000000Z";
    static const unsigned char after[] = "\x17\x0d" "490101000000Z";
    memcpy(cert->beforeDate, before, sizeof(before) - 1);
    cert->beforeDateSz = sizeof(before) - 1;
    memcpy(cert->afterDate, after, sizeof(after) - 1);
    cert->afterDateSz = sizeof(after) - 1;
    length = wc_MakeCert_ex(cert, cert_der, (word32)*cert_size,
                            ED25519_TYPE, &key, &rng);
    if (length <= 0) goto done;
    length = wc_SignCert_ex(length, CTC_ED25519, cert_der, (word32)*cert_size,
                            ED25519_TYPE, &key, &rng);
    if (length <= 0) goto done;
    *cert_size = (size_t)length;
    result = 0;
done:
    free(cert);
    wc_ed25519_free(&key);
    wc_FreeRng(&rng);
    return result;
}

static int load_identity(unsigned char *key, size_t *key_size,
                         unsigned char *cert, size_t *cert_size, const char *directory)
{
#ifdef ESP_PLATFORM
    (void)directory;
    nvs_handle_t handle;
    if (nvs_open("cgp-identity", NVS_READWRITE, &handle) != ESP_OK) return -1;
    esp_err_t kr = nvs_get_blob(handle, "key", key, key_size);
    esp_err_t cr = nvs_get_blob(handle, "cert", cert, cert_size);
    if (kr == ESP_OK && cr == ESP_OK) {
        nvs_close(handle);
        return 0;
    }
    /* A damaged identity must not silently turn into a different device. */
    if (kr != ESP_ERR_NVS_NOT_FOUND || cr != ESP_ERR_NVS_NOT_FOUND) {
        nvs_close(handle);
        return -1;
    }
    *key_size = KEY_CAPACITY;
    *cert_size = CERT_CAPACITY;
    int result = make_identity(key, key_size, cert, cert_size);
    if (result == 0 &&
        (nvs_set_blob(handle, "key", key, *key_size) != ESP_OK ||
         nvs_set_blob(handle, "cert", cert, *cert_size) != ESP_OK ||
         nvs_commit(handle) != ESP_OK)) result = -1;
    nvs_close(handle);
    return result;
#else
    if (!directory) return make_identity(key, key_size, cert, cert_size);
    if (mkdir(directory, 0700) && errno != EEXIST) return -1;
    char path[4096];
    if (snprintf(path, sizeof(path), "%s/identity.der", directory) >= (int)sizeof(path)) return -1;
    int fd = open(path, O_RDONLY);
    if (fd >= 0) {
        unsigned char lengths[4];
        int result = -1;
        if (read(fd, lengths, 4) != 4) goto loaded;
        size_t ks = ((size_t)lengths[0] << 8) | lengths[1];
        size_t cs = ((size_t)lengths[2] << 8) | lengths[3];
        if (!ks || ks > *key_size || !cs || cs > *cert_size) goto loaded;
        if (read(fd, key, ks) != (ssize_t)ks || read(fd, cert, cs) != (ssize_t)cs) goto loaded;
        unsigned char extra;
        if (read(fd, &extra, 1) != 0) goto loaded;
        *key_size = ks; *cert_size = cs; result = 0;
loaded:
        close(fd); return result;
    }
    if (errno != ENOENT || make_identity(key, key_size, cert, cert_size)) return -1;
    fd = open(path, O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (fd < 0) return -1;
    unsigned char lengths[4] = {(unsigned char)(*key_size >> 8), (unsigned char)*key_size,
                               (unsigned char)(*cert_size >> 8), (unsigned char)*cert_size};
    bool ok = write(fd, lengths, 4) == 4 && write(fd, key, *key_size) == (ssize_t)*key_size &&
              write(fd, cert, *cert_size) == (ssize_t)*cert_size && fsync(fd) == 0;
    close(fd);
    return ok ? 0 : -1;
#endif
}

static int accept_maker_certificate(int preverify_ok, WOLFSSL_X509_STORE_CTX *ctx)
{
    (void)preverify_ok;
    (void)ctx;
    /* CA trust and validity dates are intentionally outside CGP identity.
     * TLS still verifies the CertificateVerify signature with the peer key.
     * cgp_peer_fingerprint enforces the mandatory Ed25519 key type. */
    return 1;
}

WOLFSSL_CTX *cgp_tls_context(bool server, const char *directory, char fingerprint[65])
{
    unsigned char key_der[KEY_CAPACITY];
    unsigned char cert_der[CERT_CAPACITY];
    size_t key_size = sizeof(key_der), cert_size = sizeof(cert_der);
    WOLFSSL_CTX *ctx = NULL;
    if (wolfSSL_Init() != WOLFSSL_SUCCESS) return NULL;
    if (load_identity(key_der, &key_size, cert_der, &cert_size, directory) != 0) goto fail;
    ctx = wolfSSL_CTX_new(server ? wolfTLSv1_3_server_method() : wolfTLSv1_3_client_method());
    if (!ctx) goto fail;
    if ((server ? ngtcp2_crypto_wolfssl_configure_server_context(ctx)
                : ngtcp2_crypto_wolfssl_configure_client_context(ctx)) != 0) goto fail;
    if (wolfSSL_CTX_set_cipher_list(ctx, "TLS13-AES128-GCM-SHA256") != WOLFSSL_SUCCESS ||
        wolfSSL_CTX_set1_curves_list(ctx, "X25519") != WOLFSSL_SUCCESS ||
        wolfSSL_CTX_set1_sigalgs_list(ctx, "ED25519") != WOLFSSL_SUCCESS ||
        wolfSSL_CTX_use_certificate_buffer(ctx, cert_der, (long)cert_size,
                                           WOLFSSL_FILETYPE_ASN1) != WOLFSSL_SUCCESS ||
        wolfSSL_CTX_use_PrivateKey_buffer(ctx, key_der, (long)key_size,
                                          WOLFSSL_FILETYPE_ASN1) != WOLFSSL_SUCCESS ||
        wolfSSL_CTX_check_private_key(ctx) != WOLFSSL_SUCCESS)
        goto fail;
    /* Request optional client identity; lack of a cert remains maker tier. */
    wolfSSL_CTX_set_verify(ctx, WOLFSSL_VERIFY_PEER, accept_maker_certificate);
    wolfSSL_CTX_set_session_cache_mode(ctx, WOLFSSL_SESS_CACHE_OFF);
    /* ALPN is configured with wolfSSL_UseALPN on each connection. */
    ed25519_key key;
    word32 index = 0, raw_size = 32;
    unsigned char raw[32];
    fingerprint[0]=0;
    if (wc_ed25519_init(&key) == 0) {
        if (wc_Ed25519PrivateKeyDecode(key_der, &index, &key, (word32)key_size) == 0 &&
            wc_ed25519_export_public(&key, raw, &raw_size) == 0 && raw_size == 32) {
            hex_key(raw, fingerprint);

        }
        wc_ed25519_free(&key);
    }
    if (!fingerprint[0]) goto fail;
    clear_secret(key_der, sizeof(key_der));
    return ctx;
fail:
    clear_secret(key_der, sizeof(key_der));
    if (ctx) wolfSSL_CTX_free(ctx);
    fprintf(stderr, "Failed to initialize persistent Ed25519 QUIC identity\n");
    return NULL;
}

int cgp_peer_fingerprint(WOLFSSL *tls, char out[65])
{
    out[0] = '\0';
    WOLFSSL_X509 *cert = wolfSSL_get_peer_certificate(tls);
    if (!cert) return 1;
    unsigned char spki[64], raw[32];
    int spki_size = sizeof(spki), result = -1;
    if (wolfSSL_X509_get_pubkey_type(cert) == ED25519k &&
        wolfSSL_X509_get_pubkey_buffer(cert, spki, &spki_size) == WOLFSSL_SUCCESS) {
        ed25519_key key;
        word32 index = 0, raw_size = sizeof(raw);
        if (wc_ed25519_init(&key) == 0) {
            if (wc_Ed25519PublicKeyDecode(spki, &index, &key, (word32)spki_size) == 0 &&
                wc_ed25519_export_public(&key, raw, &raw_size) == 0 && raw_size == 32) {
                hex_key(raw, out);
                result = 0;
            }
            wc_ed25519_free(&key);
        }
    }
    wolfSSL_X509_free(cert);
    return result;
}
