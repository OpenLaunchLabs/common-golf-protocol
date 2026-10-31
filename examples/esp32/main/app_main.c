/* SPDX-License-Identifier: Apache-2.0 */
#include "demo.h"
#include "cpu_stats.h"

#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_random.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "lwip/inet.h"
#include "mdns.h"
#include "nvs_flash.h"
#include "sdkconfig.h"

#define WIFI_CONNECTED BIT0
#define DISCOVERY_QUEUE_LENGTH 12

static const char *TAG = "cgp-demo";
static EventGroupHandle_t wifi_events;
static QueueHandle_t discovered_peers;
static portMUX_TYPE wifi_lock = portMUX_INITIALIZER_UNLOCKED;
static uint32_t wifi_generation;
static struct in_addr wifi_address;

typedef struct {
    struct sockaddr_in address;
    char name[128];
    uint32_t generation;
} discovered_peer;

uint64_t cgp_now_ns(void)
{
    return (uint64_t)esp_timer_get_time() * UINT64_C(1000);
}

void cgp_random(void *destination, size_t length)
{
    esp_fill_random(destination, length);
}

void cgp_log(const char *format, ...)
{
    va_list args;
    printf("I (%" PRIi64 ") cgp: ", esp_timer_get_time() / 1000);
    va_start(args, format);
    vprintf(format, args);
    va_end(args);
    putchar('\n');
}

static uint32_t network_snapshot(struct in_addr *address)
{
    portENTER_CRITICAL(&wifi_lock);
    uint32_t generation = wifi_generation;
    *address = wifi_address;
    portEXIT_CRITICAL(&wifi_lock);
    return generation;
}

static void wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        ESP_ERROR_CHECK(esp_wifi_connect());
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(wifi_events, WIFI_CONNECTED);
        portENTER_CRITICAL(&wifi_lock);
        ++wifi_generation;
        wifi_address.s_addr = 0;
        portEXIT_CRITICAL(&wifi_lock);
        ESP_LOGI(TAG, "Wi-Fi disconnected; reconnecting");
        esp_err_t error = esp_wifi_connect();
        if (error != ESP_OK) ESP_LOGW(TAG, "Reconnect: %s", esp_err_to_name(error));
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = data;
        portENTER_CRITICAL(&wifi_lock);
        wifi_address.s_addr = event->ip_info.ip.addr;
        ++wifi_generation;
        portEXIT_CRITICAL(&wifi_lock);
        ESP_LOGI(TAG, "Wi-Fi ready; address " IPSTR, IP2STR(&event->ip_info.ip));
        xEventGroupSetBits(wifi_events, WIFI_CONNECTED);
    }
}

#if CONFIG_CGP_DEMO_PEERS_ONLY
static bool is_demo_peer(const mdns_result_t *result)
{
    static const char *const vendors[] = {
        "Common Golf example", "CGP hardware test", "Example"
    };
    for (size_t i = 0; i < result->txt_count; ++i) {
        const mdns_txt_item_t *item = &result->txt[i];
        if (!item->key || strcmp(item->key, "vendor") || !item->value) continue;
        size_t length = result->txt_value_len ? result->txt_value_len[i] : strlen(item->value);
        for (size_t j = 0; j < sizeof(vendors) / sizeof(vendors[0]); ++j) {
            if (length == strlen(vendors[j]) && !memcmp(item->value, vendors[j], length))
                return true;
        }
    }
    return false;
}
#endif

/* Resolution can wait for multicast replies, so it never runs in the QUIC
 * task. Results are copied, tagged with the network generation, then freed. */
static void discover_task(void *argument)
{
    (void)argument;
    while (true) {
        xEventGroupWaitBits(wifi_events, WIFI_CONNECTED, pdFALSE, pdTRUE,
                            portMAX_DELAY);
        struct in_addr ignored;
        uint32_t generation = network_snapshot(&ignored);
        mdns_result_t *results = NULL;
        esp_err_t error = mdns_query_ptr("_common-golf", "_udp", 2000,
                                         DISCOVERY_QUEUE_LENGTH, &results);
        if (error == ESP_OK) {
            for (mdns_result_t *result = results; result; result = result->next) {
#if CONFIG_CGP_DEMO_PEERS_ONLY
                if (!is_demo_peer(result)) continue;
#endif
                if (!result->port) continue;
                for (mdns_ip_addr_t *ip = result->addr; ip; ip = ip->next) {
                    if (ip->addr.type != ESP_IPADDR_TYPE_V4) continue;
                    discovered_peer peer = {
                        .address = {
                            .sin_family = AF_INET,
                            .sin_port = htons(result->port),
                            .sin_addr.s_addr = ip->addr.u_addr.ip4.addr,
                        },
                        .generation = generation,
                    };
                    snprintf(peer.name, sizeof(peer.name), "%s",
                             result->instance_name ? result->instance_name :
                             (result->hostname ? result->hostname : "Common Golf peer"));
                    /* A full queue is harmless: the next browse retries. */
                    xQueueSend(discovered_peers, &peer, 0);
                    break;
                }
            }
        } else if (error != ESP_ERR_NOT_FOUND) {
            ESP_LOGW(TAG, "Discovery query: %s", esp_err_to_name(error));
        }
        mdns_query_results_free(results);
        vTaskDelay(pdMS_TO_TICKS(3000));
    }
}

static cgp_role configured_role(void)
{
#if CONFIG_CGP_ROLE_CONTROL_BOX
    return CGP_CONTROL_BOX;
#elif CONFIG_CGP_ROLE_SIMULATOR
    return CGP_SIMULATOR;
#else
    return CGP_LAUNCH_MONITOR;
#endif
}

static const char *role_name(cgp_role role)
{
    switch (role) {
    case CGP_CONTROL_BOX: return "control-box";
    case CGP_SIMULATOR: return "simulator";
    default: return "launch-monitor";
    }
}

static void report_resources(cgp_engine *engine)
{
    const uint32_t caps = MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT;
    wifi_ap_record_t access_point;
    int rssi = esp_wifi_sta_get_ap_info(&access_point) == ESP_OK ? access_point.rssi : 0;
    ESP_LOGI(TAG, "resources peers=%u heap=%u minimum=%u largest=%u stack_free=%u rssi=%d",
             engine ? cgp_connections(engine) : 0,
             (unsigned)heap_caps_get_free_size(caps),
             (unsigned)heap_caps_get_minimum_free_size(caps),
             (unsigned)heap_caps_get_largest_free_block(caps),
             (unsigned)uxTaskGetStackHighWaterMark(NULL), rssi);
    if (engine) cgp_statistics(engine);
}

void app_main(void)
{
    esp_err_t error = nvs_flash_init();
    if (error != ESP_OK) {
        /* Do not erase NVS automatically: it contains the persistent identity. */
        ESP_LOGE(TAG, "NVS initialization failed (%s). See README recovery instructions.",
                 esp_err_to_name(error));
        return;
    }
    /* Keep the complete demo linked even in a build with no credentials, so
     * size reports are representative. The volatile read prevents folding
     * the empty default into an unconditional early return. */
    const volatile char *configured_ssid = CONFIG_CGP_WIFI_SSID;
    if (configured_ssid[0] == '\0') {
        ESP_LOGE(TAG, "Configure Wi-Fi under 'Common Golf Protocol demo' in menuconfig");
        return;
    }
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_t *station = esp_netif_create_default_wifi_sta();
    if (!station) {
        ESP_LOGE(TAG, "Cannot allocate Wi-Fi interface");
        return;
    }
    wifi_events = xEventGroupCreate();
    discovered_peers = xQueueCreate(DISCOVERY_QUEUE_LENGTH, sizeof(discovered_peer));
    if (!wifi_events || !discovered_peers) {
        ESP_LOGE(TAG, "Cannot allocate network task state");
        return;
    }
    cgp_role role = configured_role();
    uint8_t mac[6];
    ESP_ERROR_CHECK(esp_read_mac(mac, ESP_MAC_WIFI_STA));
    char serial[13], hostname[64], name[96];
    snprintf(serial, sizeof(serial), "%02x%02x%02x%02x%02x%02x",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    snprintf(hostname, sizeof(hostname), "cgp-%s-%s", role_name(role), serial);
    snprintf(name, sizeof(name), "ESP32 %s %s", role_name(role), serial);
    ESP_ERROR_CHECK(esp_netif_set_hostname(station, hostname));

    wifi_init_config_t wifi_init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&wifi_init));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event, NULL));
    wifi_config_t wifi_config = {0};
    size_t ssid_length = strlen(CONFIG_CGP_WIFI_SSID);
    size_t password_length = strlen(CONFIG_CGP_WIFI_PASSWORD);
    if (ssid_length > sizeof(wifi_config.sta.ssid) ||
        password_length > sizeof(wifi_config.sta.password)) {
        ESP_LOGE(TAG, "Wi-Fi SSID or password exceeds the Wi-Fi field size");
        return;
    }
    memcpy(wifi_config.sta.ssid, CONFIG_CGP_WIFI_SSID, ssid_length);
    memcpy(wifi_config.sta.password, CONFIG_CGP_WIFI_PASSWORD, password_length);
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    cgp_cpu_stats_start();
    ESP_ERROR_CHECK(mdns_init());
    ESP_ERROR_CHECK(mdns_hostname_set(hostname));
    ESP_ERROR_CHECK(mdns_instance_name_set(name));
    ESP_ERROR_CHECK(esp_wifi_start());
    /* Keep timing predictable while measuring a QUIC proof of concept. */
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    if (role == CGP_SIMULATOR &&
        xTaskCreate(discover_task, "cgp-discovery", 4096, NULL, 3, NULL) != pdPASS) {
        ESP_LOGE(TAG, "Cannot start discovery task");
        return;
    }
    ESP_LOGI(TAG, "%s; original ESP32, maximum peers %d", name, CONFIG_CGP_MAX_CONNECTIONS);

    cgp_engine *engine = NULL;
    uint32_t engine_generation = 0;
    uint64_t next_stats = 0, retry_after = 0;
    bool advertised = false;
    while (true) {
        struct in_addr local_ip;
        uint32_t generation = network_snapshot(&local_ip);
        bool connected = (xEventGroupGetBits(wifi_events) & WIFI_CONNECTED) != 0;
        if (engine && (!connected || generation != engine_generation)) {
            if (advertised) {
                mdns_service_remove("_common-golf", "_udp");
                advertised = false;
            }
            cgp_destroy(engine);
            engine = NULL;
            retry_after = 0;
            ESP_LOGI(TAG, "Network changed; restarting connections");
        }
        uint64_t now = cgp_now_ns();
        if (!engine && connected && local_ip.s_addr && now >= retry_after) {
            engine = cgp_create(role, name, local_ip);
            if (engine) {
                engine_generation = generation;
                if (role != CGP_SIMULATOR) {
                    mdns_txt_item_t txt[] = {
                        {"vendor", "Common Golf example"},
                        {"model", role_name(role)},
                        {"serial", serial},
                    };
                    error = mdns_service_add(name, "_common-golf", "_udp", cgp_port(engine),
                                             txt, sizeof(txt) / sizeof(txt[0]));
                    if (error == ESP_OK) {
                        advertised = true;
                        ESP_LOGI(TAG, "Advertising _common-golf._udp on ephemeral port %u",
                                 cgp_port(engine));
                    } else {
                        ESP_LOGE(TAG, "mDNS advertisement failed: %s", esp_err_to_name(error));
                        cgp_destroy(engine);
                        engine = NULL;
                    }
                }
            }
            retry_after = now + UINT64_C(5000000000);
            if (!engine) ESP_LOGW(TAG, "Engine setup failed; retrying in five seconds");
        }
        discovered_peer peer;
        while (xQueueReceive(discovered_peers, &peer, 0) == pdTRUE) {
            if (engine && peer.generation == engine_generation) {
                cgp_connect(engine, &peer.address, peer.name);
            }
        }
        if (engine) cgp_poll(engine);
        if (now >= next_stats) {
            report_resources(engine);
            next_stats = now + UINT64_C(10000000000);
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}
