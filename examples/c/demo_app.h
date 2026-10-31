/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include "cgp.h"
typedef struct {
    bool simulator, connected;
    unsigned received;
} cgp_demo_app;
cgp_callbacks cgp_demo_callbacks(void);
