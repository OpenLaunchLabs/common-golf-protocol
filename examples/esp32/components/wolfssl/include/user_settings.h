#ifndef CGP_WOLFSSL_USER_SETTINGS_H
#define CGP_WOLFSSL_USER_SETTINGS_H
#include <unistd.h>
#include <sys/time.h>

/* One deliberately narrow TLS build shared by ESP32 and the native harness. */
#define WOLFSSL_TLS13
#define WOLFSSL_QUIC
#define HAVE_TLS_EXTENSIONS
#define HAVE_SUPPORTED_CURVES
#define HAVE_HKDF
#define HAVE_AESGCM
#define WOLFSSL_AES_DIRECT
#define WOLFSSL_AES_COUNTER
#define HAVE_AES_ECB
#define HAVE_ALPN
#define HAVE_SESSION_TICKET
/* TLS 1.3 permits ticket nonces up to 255 bytes (rustls emits 32). */
#define WOLFSSL_TICKET_NONCE_MALLOC
#define OPENSSL_EXTRA
#define HAVE_EX_DATA
#define WOLFSSL_RAND
#define WOLFSSL_SHA512
#define HAVE_CURVE25519
#define HAVE_ED25519
/* wolfSSL 5.8.2's OpenSSL helpers require one traditional key algorithm to
 * compile their shared RNG. The negotiated group/signature stay X25519/Ed25519. */
#define HAVE_ECC
#define ECC_TIMING_RESISTANT
#define CURVED25519_SMALL
#define WOLFSSL_CERT_GEN
#define WOLFSSL_KEY_GEN
#define KEEP_PEER_CERT
#define WOLFSSL_SMALL_STACK
#define WOLFSSL_SP_MATH_ALL
#define WOLFSSL_SP_SMALL
#define SMALL_SESSION_CACHE
#define NO_OLD_TLS
#define NO_RSA
#define NO_DH
#define NO_DSA
#define NO_MD4
#define NO_MD5
#define NO_RC4
#define NO_DES3
#define NO_PSK
#define NO_PWDBASED
#define WOLFSSL_USER_IO
#define NO_WRITEV
#define HAVE_UNISTD_H 1
#define SINGLE_THREADED

#ifdef ESP_PLATFORM
#include "sdkconfig.h"
#include "esp_random.h"
#define NO_FILESYSTEM
#define WOLFSSL_ESPIDF
#define WOLFSSL_ESP32
#define NO_ESPIDF_DEFAULT
/* Start with the portable crypto path: no PSRAM or chip-specific accelerator
 * assumptions. ESP-IDF's hardware RNG remains the entropy source. */
#define NO_ESP32_CRYPT
#define NO_WOLFSSL_ESP32_CRYPT_HASH
#define NO_WOLFSSL_ESP32_CRYPT_AES
#define NO_WOLFSSL_ESP32_CRYPT_RSA_PRI
#define WOLFSSL_LWIP
#define NO_WOLFSSL_DIR
#define WOLFSSL_NO_CURRDIR
#endif

#endif
