#ifndef CGP_IDENTITY_H
#define CGP_IDENTITY_H
#include <stdbool.h>
#include <wolfssl/ssl.h>


/* NVS must be initialized first on ESP32. Caller owns the returned context. */
WOLFSSL_CTX *cgp_tls_context(bool server, const char *directory, char fingerprint[65]);
/* 0: authenticated Ed25519 key, 1: no certificate, -1: unusable/wrong key.
 * Call after TLS has completed CertificateVerify, before using application data. */
int cgp_peer_fingerprint(WOLFSSL *tls, char out[65]);
#endif
