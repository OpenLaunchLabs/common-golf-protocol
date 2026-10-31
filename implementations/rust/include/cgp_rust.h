#ifndef CGP_RUST_H
#define CGP_RUST_H
#include <stdint.h>
#ifdef _WIN32
#define CGP_API __declspec(dllimport)
#define CGP_CALL __cdecl
#else
#define CGP_API
#define CGP_CALL
#endif
#ifdef __cplusplus
extern "C" {
#endif
/* ABI 1: opaque owned endpoint, UTF-8 NUL-terminated input strings.
 * All handle calls must be serialized, including destroy. No callback reentry.
 * Callbacks run synchronously on the calling thread, only inside discover/poll;
 * bytes are borrowed until callback return. Copy them; never free them.
 * Callbacks must not throw/unwind. Handles/pointers must be valid for each call.
 * Return: 0 success, 1 poll timeout, -1 error. Error text is thread-local,
 * borrowed until the next failed call on that thread. Copy it immediately.
 * Blocking calls belong on a worker thread. No callbacks survive call return.
 * destroy(NULL) is safe; destroying twice or using a destroyed handle is not.
 */
typedef struct CgpEndpoint CgpEndpoint;
typedef void (CGP_CALL *cgp_callback)(void *context, uint32_t channel,
                                   const uint8_t *bytes, uint32_t length);
CGP_API uint32_t CGP_CALL cgp_abi_version(void);
CGP_API const char *CGP_CALL cgp_last_error(void);
/* config: {identity_directory, role, draft, info}; role: simulator/device/
 * controller/device_controller; draft: 2 only; info: protocol info object. */
CGP_API int32_t CGP_CALL cgp_create(const char *config, CgpEndpoint **out);
/* Bounded discovery (1..60000 ms). Callback gets a JSON device descriptor.
 * Results arrive after the browse interval. No continuous background browse. */
CGP_API int32_t CGP_CALL cgp_discover(CgpEndpoint *, uint32_t ms, cgp_callback, void *);
/* Pass the discovered JSON descriptor and an independently trusted lowercase
 * 64-character Ed25519 key fingerprint. Overall deadline: 10 seconds.
 * Pin verified before info/application data. One connection per endpoint. */
CGP_API int32_t CGP_CALL cgp_connect(CgpEndpoint *, const char *device, const char *fingerprint);
/* Standard JSON channels 0, 1, 3..8 only; length 1..65535. A successful send means
 * accepted by transport, not processed by peer. Timeout disconnects to avoid
 * reusing a partially written frame. Channel 2 is not exposed by this ABI; channels 9..999 are reserved.
 * Custom channels are not exposed in ABI v1. */
CGP_API int32_t CGP_CALL cgp_send(CgpEndpoint *, uint32_t channel, const uint8_t *, uint32_t length, uint32_t ms);
/* Delivers at most one complete JSON object; 1 means no event before deadline. */
CGP_API int32_t CGP_CALL cgp_poll(CgpEndpoint *, uint32_t ms, cgp_callback, void *);
CGP_API int32_t CGP_CALL cgp_peer_fingerprint(CgpEndpoint *, char *out, uint32_t capacity);
CGP_API int32_t CGP_CALL cgp_disconnect(CgpEndpoint *);
CGP_API int32_t CGP_CALL cgp_destroy(CgpEndpoint *);
#ifdef __cplusplus
}
#endif
#endif
