idf_component_register(
    SRCS "app_main.c" "cpu_stats.c" "transport.c" "mock.c" "json_guard.c" "json_extract.c" "framing.c" "identity.c" "../../../implementations/c/src/protocol.c"
    INCLUDE_DIRS "." "../../../implementations/c/include"
    REQUIRES ngtcp2 wolfssl nvs_flash esp_wifi esp_netif esp_event esp_timer
             esp_system heap freertos lwip mdns json)
