#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
role="${1:-}"
case "$role" in
    launch_monitor|control_box|simulator) shift ;;
    *)
        echo "Usage: $0 {launch_monitor|control_box|simulator} [idf.py options/actions]" >&2
        echo "Example: $0 simulator menuconfig" >&2
        echo "Example: $0 simulator -p /dev/cu.SLAB_USBtoUART flash monitor" >&2
        exit 2
        ;;
esac
if ! command -v idf.py >/dev/null 2>&1; then
    echo "Activate ESP-IDF 5.4.2 first: . /path/to/esp-idf/export.sh" >&2
    exit 1
fi
if [[ ! -f "$project_dir/third_party/ngtcp2/lib/ngtcp2_conn.c" ||
      ! -f "$project_dir/third_party/wolfssl/src/ssl.c" ]]; then
    python3 "$project_dir/scripts/fetch_deps.py"
fi
if [[ "$#" -eq 0 ]]; then
    set -- build
fi
exec idf.py -C "$project_dir" -B "$project_dir/build-$role" \
    -D "IDF_TARGET=esp32" \
    -D "SDKCONFIG=$project_dir/sdkconfig.$role" \
    -D "SDKCONFIG_DEFAULTS=$project_dir/sdkconfig.defaults;$project_dir/profiles/$role.defaults" \
    "$@"
