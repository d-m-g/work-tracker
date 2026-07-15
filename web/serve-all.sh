#!/usr/bin/env bash
#
# Start the work-tracker viewer twice, from one command:
#
#   * on loopback, always -- the local viewer, which never depends on Tailscale;
#   * on this Mac's Tailscale address, only when Tailscale is connected -- so your
#     phone can reach it over the tailnet, and nowhere else can.
#
# If Tailscale is off, logged out, or not installed, the local viewer still comes
# up exactly as before; the tailnet one is simply skipped, with a line saying so.
# A missing tailnet is never an error here -- it is Tuesday. Both servers are plain
# callers of the one writer (see the README): two front doors, one tracker, and
# the atomic writes mean two of them cannot corrupt a session between them.
#
# Environment overrides (all optional):
#   PORT       port to listen on            (default 8765)
#   ROOT       data directory               (default: the repository)
#   PYTHON     interpreter                  (default python3)
#   TAILSCALE  path to the tailscale CLI    (default: autodetected)

set -u

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"

port="${PORT:-8765}"
root="${ROOT:-$repo}"
python="${PYTHON:-python3}"

# Locate the Tailscale CLI: an explicit override, then PATH, then the Mac app
# bundle -- which does not put itself on PATH, so it must be looked for by hand.
tailscale="${TAILSCALE:-}"
if [ -z "$tailscale" ]; then
  if command -v tailscale >/dev/null 2>&1; then
    tailscale="tailscale"
  elif [ -x "/Applications/Tailscale.app/Contents/MacOS/Tailscale" ]; then
    tailscale="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
  fi
fi

# This Mac's tailnet IPv4 -- empty if Tailscale is missing, logged out, or down.
# The failure is swallowed on purpose: no address simply means no tailnet viewer.
ts_ip=""
if [ -n "$tailscale" ]; then
  ts_ip="$("$tailscale" ip -4 2>/dev/null | head -n1 || true)"
fi

# Track children as a plain string, not an array: macOS still ships bash 3.2,
# where an empty array under `set -u` is itself an "unbound variable" error.
pids=""
cleanup() {
  [ -n "$pids" ] && kill $pids 2>/dev/null
}
trap cleanup INT TERM EXIT

# 1) The local viewer -- always, and independent of everything below it.
"$python" "$repo/web/server.py" --host 127.0.0.1 --port "$port" --root "$root" &
pids="$pids $!"
echo "local   -> http://127.0.0.1:$port"

# 2) The tailnet viewer -- only when Tailscale handed us an address.
if [ -n "$ts_ip" ]; then
  "$python" "$repo/web/server.py" --host "$ts_ip" --port "$port" --root "$root" \
    --allow-origin "http://$ts_ip:$port" &
  pids="$pids $!"
  echo "tailnet -> http://$ts_ip:$port   (open this on your phone)"
else
  echo "tailnet -> skipped: Tailscale is not connected (the local viewer is unaffected)"
fi

echo "press ctrl-c to stop"
wait
