set(WOLFSSL_ROOT "${CMAKE_CURRENT_LIST_DIR}/../../third_party/wolfssl")
if(NOT EXISTS "${WOLFSSL_ROOT}/wolfssl/ssl.h")
    message(FATAL_ERROR "Run python3 scripts/fetch_deps.py before configuring")
endif()
set(WOLFSSL_SOURCES
    ${WOLFSSL_ROOT}/src/ssl.c
    ${WOLFSSL_ROOT}/src/internal.c
    ${WOLFSSL_ROOT}/src/keys.c
    ${WOLFSSL_ROOT}/src/tls.c
    ${WOLFSSL_ROOT}/src/tls13.c
    ${WOLFSSL_ROOT}/src/quic.c
    ${WOLFSSL_ROOT}/src/wolfio.c
)
foreach(source aes asn coding cpuid curve25519 ecc ed25519 error fe_low_mem
        ge_low_mem hash hmac kdf logging memory random sha sha256 sha512
        signature sp_int wc_encrypt wc_port wolfmath)
    list(APPEND WOLFSSL_SOURCES "${WOLFSSL_ROOT}/wolfcrypt/src/${source}.c")
endforeach()
set(WOLFSSL_INCLUDES "${CMAKE_CURRENT_LIST_DIR}/include" "${WOLFSSL_ROOT}")
