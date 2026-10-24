#!/usr/bin/env bash
set -euo pipefail

mkdir -p "${PRIVATE_MEDIA_ROOT:?PRIVATE_MEDIA_ROOT must be set}" "${MEDIA_ROOT:?MEDIA_ROOT must be set}"
exec supervisord -n -c deploy/supervisord.conf
