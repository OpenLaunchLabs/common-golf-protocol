/* SPDX-License-Identifier: Apache-2.0 */
#include "internal.h"

#include <limits.h>
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#define MILLISECOND UINT64_C(1000000)
#define SECOND UINT64_C(1000000000)

static const char *const commands[] = {
    "left", "right", "up", "down", "reset_aim", "club_up", "club_down",
    "putter_toggle", "mulligan", "rehit", "drop", "tee_left", "tee_right",
    "switch_hand", "flyover", "shot_cam", "scorecard", "heatmap", "map",
    "green_grid", "pin", "hide_ui", "select", "back"
};

static uint32_t random_u32(void)
{
    uint32_t n;
    cgp_random(&n, sizeof(n));
    return n;
}

static unsigned between(unsigned low, unsigned high)
{
    return low + random_u32() % (high - low + 1);
}

static double tenth(int low, int high)
{
    return (low + (int)between(0, (unsigned)(high - low))) / 10.0;
}

static bool publish(cgp_peer *peer, unsigned channel, const char *format, ...)
{
    char json[2048];
    va_list args;
    int n;
    va_start(args, format);
    n = vsnprintf(json, sizeof(json), format, args);
    va_end(args);
    if (n < 0 || (size_t)n >= sizeof(json)) {
        cgp_log("mock message exceeds transmit buffer on channel %u", channel);
        return false;
    }
    if (cgp_peer_draft(peer) == CGP_DRAFT_1 && (channel == 1 || channel == 3)) {
        cJSON *object = cJSON_Parse(json);
        if (!object) return false;
        bool estimated = cJSON_HasObjectItem(object,"estimated");
        cJSON_DeleteItemFromObjectCaseSensitive(object,"estimated");
        if (estimated && channel == 1) cJSON_AddBoolToObject(object,"reduced_accuracy",true);
        char *converted = cJSON_PrintUnformatted(object);
        bool ok = converted && cgp_publish(peer,channel,converted);
        cJSON_free(converted); cJSON_Delete(object); return ok;
    }
    if (cgp_peer_draft(peer) == CGP_DRAFT_3) {
        cJSON *object = cJSON_Parse(json);
        if (!object) return false;
        if (channel == 1 || channel == 3 || channel == 7 || channel == 8) {
            cJSON_DeleteItemFromObjectCaseSensitive(object, "shot_number");
            cJSON_DeleteItemFromObjectCaseSensitive(object, "final");
            cJSON_DeleteItemFromObjectCaseSensitive(object, "measurement_id");
            cJSON_DeleteItemFromObjectCaseSensitive(object, "shot_kind");
            if (!cJSON_HasObjectItem(object, "shot_uuid"))
                cJSON_AddStringToObject(object, "shot_uuid", peer->shot_uuid);
            if (cJSON_HasObjectItem(object, "tee_position")) {
                cJSON_DeleteItemFromObjectCaseSensitive(object, "tee_position");
                cJSON_AddStringToObject(object, "ball_lie", "tee");
            }
        } else if (channel == 4) {
            cJSON_ReplaceItemInObjectCaseSensitive(object, "type", cJSON_CreateString("device_status"));
            cJSON_AddStringToObject(object, "capture_mode", "full");
        } else if (channel == 6) {
            cJSON_ReplaceItemInObjectCaseSensitive(object, "type", cJSON_CreateString("simulator_state"));
            cJSON_DeleteItemFromObjectCaseSensitive(object, "mode");
            cJSON_AddStringToObject(object, "requested_capture_mode", "full");
        }
        char *converted = cJSON_PrintUnformatted(object);
        bool ok = converted && cgp_publish(peer,channel,converted);
        cJSON_free(converted); cJSON_Delete(object); return ok;
    }
    return cgp_publish(peer, channel, json);
}

/* Engine names are at most 63 bytes; six output bytes per input byte is the
 * largest JSON escape. Keep strings valid even for a custom quoted name. */
static void escaped_name(char out[384], const char *name)
{
    size_t i, used = 0;
    const char *hex = "0123456789abcdef";
    for (i = 0; i < 63 && name[i]; ++i) {
        unsigned char c = (unsigned char)name[i];
        if (c < 0x20) {
            out[used++] = '\\'; out[used++] = 'u';
            out[used++] = '0'; out[used++] = '0';
            out[used++] = hex[c >> 4]; out[used++] = hex[c & 15];
        } else {
            if (c == '"' || c == '\\')
                out[used++] = '\\';
            out[used++] = (char)c;
        }
    }
    out[used] = '\0';
}

void cgp_mock_start(cgp_peer *peer)
{
    char name[384];
    const char *channels;
    if (peer->started)
        return;
    channels = peer->engine->role == CGP_LAUNCH_MONITOR ? "[0,1,3,4,8]" :
               peer->engine->role == CGP_CONTROL_BOX ? "[0,5]" : "[0,6,7]";
    escaped_name(name, peer->engine->name);
    if (!publish(peer, 0, "{\"type\":\"info\",\"protocol_version\":\"%s\","
                 "\"name\":\"%s\",\"vendor\":\"CGP demo\",\"model\":\"ESP-WROOM-32\","
                 "\"firmware_version\":\"0.1.0\",\"channels\":%s}", cgp_protocol_version(cgp_peer_draft(peer)), name, channels))
        return;
    peer->started = true;
    peer->next_action = cgp_now_ns() + between(500, 3000) * MILLISECOND;
    if (peer->engine->role == CGP_LAUNCH_MONITOR) {
        publish(peer, 4, "{\"type\":\"status\",\"ready\":true,\"ball_detected\":false,"
                "\"uptime_seconds\":%.3f,\"message\":\"Synthetic launch monitor\"}",
                cgp_now_ns() / (double)SECOND);
        peer->next_action = cgp_now_ns() + between(3000, 7000) * MILLISECOND;
    } else if (peer->engine->role == CGP_SIMULATOR) {
        publish(peer, 6, "{\"type\":\"state\",\"ready\":true,\"mode\":\"range\","
                "\"player\":{\"name\":\"Demo player\",\"dexterity\":\"right\"}}");
    }
}

static void shot(cgp_peer *peer)
{
    const double speed = tenth(350, 750);
    const double vla = tenth(80, 190), hla = tenth(-50, 50);
    const double spin = between(1500, 4200), axis = tenth(-100, 100);
    const double path = tenth(-40, 40), face = hla * 0.8;
    const double attack = tenth(-50, 30);
    unsigned n;
    /* Preserve monotonic counters on channel reopen; disconnect naturally
     * starts a fresh peer. A demo cannot usefully produce UINT_MAX shots. */
    if (peer->shot_number == UINT_MAX)
        return;
    n = ++peer->shot_number;
    if (cgp_peer_draft(peer) == CGP_DRAFT_3) {
        time_t utc = time(NULL);
        if (!cgp_shot_uuid(utc > 0 ? (uint64_t)utc * 1000 : 0, peer->shot_uuid)) return;
    }
    publish(peer, 4, "{\"type\":\"status\",\"ball_detected\":true}");
    publish(peer, 1, "{\"type\":\"shot\",\"shot_number\":%u,\"final\":true,"
            "\"measurement_id\":\"%08x%08x-%u\",\"player_dexterity\":\"right\","
            "\"tee_position\":\"tee\",\"shot_kind\":\"full\","
            "\"ball_speed_meters_per_second\":%.1f,"
            "\"vertical_launch_angle_degrees\":%.1f,"
            "\"horizontal_launch_angle_degrees\":%.1f,"
            "\"total_spin_revolutions_per_minute\":%.0f,\"spin_axis_degrees\":%.1f,"
            "\"estimated\":[\"ball_speed_meters_per_second\",\"vertical_launch_angle_degrees\","
            "\"horizontal_launch_angle_degrees\",\"total_spin_revolutions_per_minute\",\"spin_axis_degrees\"]}",
            n, (unsigned)random_u32(), (unsigned)random_u32(), n, speed, vla, hla, spin, axis);
    publish(peer, 3, "{\"type\":\"club\",\"shot_number\":%u,\"final\":true,"
            "\"club_speed_meters_per_second\":%.1f,\"angle_of_attack_degrees\":%.1f,"
            "\"club_path_degrees\":%.1f,\"face_angle_degrees\":%.1f,"
            "\"face_to_path_degrees\":%.1f,\"smash_factor\":1.45,"
            "\"estimated\":[\"club_speed_meters_per_second\",\"angle_of_attack_degrees\","
            "\"club_path_degrees\",\"face_angle_degrees\",\"face_to_path_degrees\",\"smash_factor\"]}",
            n, speed / 1.45, attack, path, face, face - path);
    publish(peer, 8, "{\"type\":\"flight\",\"shot_number\":%u,\"final\":true,"
            "\"carry_distance_meters\":%.1f,\"total_distance_meters\":%.1f,"
            "\"carry_side_meters\":%.1f,\"total_side_meters\":%.1f,"
            "\"max_height_meters\":%.1f,\"curve_meters\":%.1f,"
            "\"landing_angle_degrees\":42.0,\"hang_time_seconds\":5.5}",
            n, speed * 2.55, speed * 2.7, hla * 1.9, hla * 2.1, speed * 0.4, axis * 0.8);
    publish(peer, 4, "{\"type\":\"status\",\"ball_detected\":false,\"uptime_seconds\":%.3f}",
            cgp_now_ns() / (double)SECOND);
    cgp_log("mock launch monitor sent shot %u", n);
}

void cgp_mock_tick(cgp_peer *peer)
{
    uint64_t now = cgp_now_ns();
    unsigned command;
    if (!peer->started)
        return;
    if (peer->engine->role == CGP_CONTROL_BOX && peer->holding) {
        if (now < peer->release_at)
            return;
        if (!publish(peer, 5, "{\"type\":\"control\",\"command\":\"%s\",\"event\":\"up\"}",
                     commands[peer->held_arrow]))
            return; /* Retry a release until queued; never leave a held key. */
        peer->holding = false;
    }
    if (now < peer->next_action)
        return;
    if (peer->engine->role == CGP_LAUNCH_MONITOR) {
        shot(peer);
        peer->next_action = now + between(3000, 7000) * MILLISECOND;
    } else if (peer->engine->role == CGP_CONTROL_BOX) {
        command = between(0, sizeof(commands) / sizeof(commands[0]) - 1);
        if (command < 4 && between(0, 1)) {
            if (publish(peer, 5, "{\"type\":\"control\",\"command\":\"%s\",\"event\":\"down\"}", commands[command])) {
                peer->held_arrow = command;
                peer->holding = true;
                peer->release_at = now + between(100, 500) * MILLISECOND;
            }
        } else {
            publish(peer, 5, "{\"type\":\"control\",\"command\":\"%s\",\"event\":\"press\"}", commands[command]);
        }
        peer->next_action = now + between(500, 3000) * MILLISECOND;
    }
}

void cgp_mock_receive(cgp_peer *peer, unsigned channel, const cJSON *message)
{
    const cJSON *n, *speed, *angle, *final, *type;
    cgp_mock_start(peer);
    if (!peer->started || peer->engine->role != CGP_SIMULATOR || channel != 1)
        return;
    type = cJSON_GetObjectItemCaseSensitive(message, "type");
    final = cJSON_GetObjectItemCaseSensitive(message, "final");
    n = cJSON_GetObjectItemCaseSensitive(message,
            cgp_peer_draft(peer) == CGP_DRAFT_3 ? "shot_uuid" : "shot_number");
    speed = cJSON_GetObjectItemCaseSensitive(message, "ball_speed_meters_per_second");
    angle = cJSON_GetObjectItemCaseSensitive(message, "horizontal_launch_angle_degrees");
    if (!cJSON_IsString(type) || strcmp(type->valuestring, "shot") ||
        (cgp_peer_draft(peer) != CGP_DRAFT_3 && ((final && !cJSON_IsTrue(final)) ||
        !cJSON_IsNumber(n) || !isfinite(n->valuedouble) || n->valuedouble < 1 ||
        n->valuedouble > UINT_MAX || floor(n->valuedouble) != n->valuedouble)) ||
        !cJSON_IsNumber(speed) || !isfinite(speed->valuedouble) || speed->valuedouble < 0 ||
        !cJSON_IsNumber(angle) || !isfinite(angle->valuedouble))
        return;
    if (cgp_peer_draft(peer) == CGP_DRAFT_3) {
        if (!cJSON_IsString(n) || strlen(n->valuestring) != 36 ||
                n->valuestring[14] != '7' || !strchr("89ab", n->valuestring[19])) return;
        for (unsigned i = 0; i < 36; ++i) {
            bool separator = i == 8 || i == 13 || i == 18 || i == 23;
            if (separator ? n->valuestring[i] != '-' : !strchr("0123456789abcdef", n->valuestring[i])) return;
        }
        memcpy(peer->shot_uuid, n->valuestring, 37);
    }
    publish(peer, 7, "{\"type\":\"result\",\"shot_number\":%u,\"accepted\":true,"
            "\"carry_distance_meters\":%.1f,\"total_distance_meters\":%.1f,"
            "\"carry_side_meters\":%.1f,\"total_side_meters\":%.1f,\"max_height_meters\":%.1f}",
            cgp_peer_draft(peer) == CGP_DRAFT_3 ? 0 : (unsigned)n->valuedouble, speed->valuedouble * 2.6, speed->valuedouble * 2.75,
            angle->valuedouble * 2.0, angle->valuedouble * 2.2, speed->valuedouble * 0.4);
}

#ifndef CGP_MOCK_SAMPLES
static void demo_connected(cgp_peer *peer, const char *fingerprint, void *user) {
    (void)fingerprint; (void)user; cgp_mock_start(peer);
}
static void demo_tick(cgp_peer *peer, void *user) {
    (void)user; cgp_mock_start(peer); cgp_mock_tick(peer);
}
static void demo_message(cgp_peer *peer,uint32_t channel,const char *json,size_t length,void *user) {
    (void)user;
#if !defined(ESP_PLATFORM) || CONFIG_CGP_LOG_PAYLOADS
    cgp_log("RX peer=%s channel=%u %.*s%s",peer->name,(unsigned)channel,
            (int)(length>1200 ? 1200 : length),json,length>1200 ? " ... [log truncated; full object accepted]" : "");
#endif
    cJSON *object = cgp_json_extract(json,length);
    if (object) { cgp_mock_receive(peer,channel,object); cJSON_Delete(object); }
}
static int demo_accept(cgp_peer *peer,uint32_t channel,void *user) {
    (void)peer; (void)user;
    return channel==2 || channel>=1000 ? CGP_NOT_INTERESTED : 0;
}
cgp_engine *cgp_create(cgp_role role,const char *name,struct in_addr local_ip) {
#ifndef CONFIG_CGP_DRAFT
#define CONFIG_CGP_DRAFT 3
#endif
    cgp_options options={.role=role,.draft=CONFIG_CGP_DRAFT,.is_server=role!=CGP_SIMULATOR,
        .name=name,.local_ip=local_ip,.callbacks={.connected=demo_connected,.tick=demo_tick,
        .message=demo_message,.accept_channel=demo_accept}};
    return cgp_create_options(&options);
}

#endif
