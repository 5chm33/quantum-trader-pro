#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

echo "Quantum Trader Pro - Offline Demo"
echo "No broker connection, credentials, paper orders, or live orders are used."
echo

python_command=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
      python_command="$candidate"
      break
    fi
  fi
done

if [[ -z "$python_command" ]]; then
  echo "Python 3.11 or newer was not found." >&2
  echo "Install a current Python release from https://www.python.org/downloads/" >&2
  exit 2
fi

"$python_command" launch_demo.py "$@"
echo
echo "Demo completed. Open the newest folder under quantum-trader-demo-runs."
