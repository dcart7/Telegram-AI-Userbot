#!/usr/bin/env bash
set -euo pipefail
pids=$(ps -ax | grep -E "[P]ython.*main\.py" | awk '{print $1}' || true)
if [ -z "${pids}" ]; then
  echo "main.py not running"
  exit 0
fi
kill ${pids}
echo "stopped: ${pids}"
