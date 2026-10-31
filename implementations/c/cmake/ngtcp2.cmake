set(NGTCP2_ROOT "${CGP_DEPENDENCIES}/ngtcp2")
if(NOT EXISTS "${NGTCP2_ROOT}/lib/includes/ngtcp2/ngtcp2.h")
    message(FATAL_ERROR "Run python3 implementations/c/tools/fetch_deps.py before configuring")
endif()
file(GLOB NGTCP2_SOURCES "${NGTCP2_ROOT}/lib/*.c")
list(APPEND NGTCP2_SOURCES
    "${NGTCP2_ROOT}/crypto/shared.c"
    "${NGTCP2_ROOT}/crypto/wolfssl/wolfssl.c")
set(PACKAGE_VERSION "1.13.0")
set(PACKAGE_VERSION_NUM "0x010d00")
file(MAKE_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/generated/ngtcp2")
configure_file("${NGTCP2_ROOT}/lib/includes/ngtcp2/version.h.in"
    "${CMAKE_CURRENT_BINARY_DIR}/generated/ngtcp2/version.h" @ONLY)
set(NGTCP2_INCLUDES "${NGTCP2_ROOT}/lib/includes"
    "${NGTCP2_ROOT}/crypto/includes" "${CMAKE_CURRENT_BINARY_DIR}/generated")
set(NGTCP2_PRIVATE_INCLUDES "${NGTCP2_ROOT}/lib" "${NGTCP2_ROOT}/crypto")
