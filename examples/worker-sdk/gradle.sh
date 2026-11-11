#!/usr/bin/env bash
set -euo pipefail
exec "${GRADLE_BIN:-gradle}" "$@"
