include(${CMAKE_CURRENT_LIST_DIR}/sources.cmake)
idf_component_register(SRCS ${NGTCP2_SOURCES} INCLUDE_DIRS ${NGTCP2_INCLUDES}
    PRIV_INCLUDE_DIRS ${NGTCP2_PRIVATE_INCLUDES} REQUIRES wolfssl lwip)
target_compile_definitions(${COMPONENT_LIB} PUBLIC NGTCP2_STATICLIB)
target_compile_definitions(${COMPONENT_LIB} PRIVATE BUILDING_NGTCP2
    HAVE_ARPA_INET_H=1 HAVE_NETINET_IN_H=1 HAVE_UNISTD_H=1)
# Xtensa's uint32_t is unsigned long; upstream's diagnostic %u emits a warning.
target_compile_options(${COMPONENT_LIB} PRIVATE -Wno-error -Wno-error=format)
