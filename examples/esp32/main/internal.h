/* Demo-only access to peer state for synthetic data generation. */
#pragma once
#include "../../../implementations/c/src/internal.h"
void cgp_mock_start(cgp_peer *peer);
void cgp_mock_tick(cgp_peer *peer);
void cgp_mock_receive(cgp_peer *peer, unsigned channel, const cJSON *message);
