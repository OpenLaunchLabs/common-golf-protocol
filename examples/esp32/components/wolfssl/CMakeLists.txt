include(${CMAKE_CURRENT_LIST_DIR}/sources.cmake)
idf_component_register(SRCS ${WOLFSSL_SOURCES}
    INCLUDE_DIRS ${WOLFSSL_INCLUDES} REQUIRES esp_hw_support lwip)
target_compile_definitions(${COMPONENT_LIB} PUBLIC WOLFSSL_USER_SETTINGS)
# Upstream dependencies do not share this example's warning policy.
target_compile_options(${COMPONENT_LIB} PRIVATE -Wno-error)
