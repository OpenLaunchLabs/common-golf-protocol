/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include "../../../implementations/c/include/cgp.h"
/* Demo adapter installs synthetic application callbacks on the shared engine. */
cgp_engine *cgp_create(cgp_role role, const char *name, struct in_addr local_ip);
