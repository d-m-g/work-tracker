#!/usr/bin/env bash
#
# Start everything, from one command:
#
#   ./start.sh
#
#   * the widget, if it is not already running (built first if it never was);
#   * the web app -- Today, Calendar, Insights -- on http://127.0.0.1:8790, and on
#     this Mac's Tailscale address too when Tailscale is connected;
#   * the page, opened in your browser once the server answers.
#
# If the web app is already up, it only opens the page. Ctrl-C stops the web app;
# the widget keeps running (quit it from its menu-bar item).
#
# Environment overrides (all optional):
#   PORT     port for the web app     (default 8790)
#   NO_OPEN  set to 1 to not open the browser

set -u

repo="$(cd "$(dirname "$0")" && pwd)"
port="${PORT:-8790}"
url="http://127.0.0.1:$port"

answers() { curl -s -o /dev/null --max-time 1 "$url/api/status"; }

# 1) The widget.
if ! pgrep -x WorkWidget >/dev/null; then
  if [ ! -d "$repo/widget/WorkWidget.app" ]; then
    echo "building the widget (first run only)..."
    sh "$repo/widget/make-app.sh" >/dev/null || echo "widget: build failed -- see widget/make-app.sh"
  fi
  open "$repo/widget/WorkWidget.app" && echo "widget  -> started"
else
  echo "widget  -> already running"
fi

# 2) The page, built once (it is not committed).
if [ ! -f "$repo/web/ui/dist/index.html" ]; then
  echo "building the web app (first run only)..."
  (cd "$repo/web/ui" && npm install --silent && npm run build --silent) || echo "web app: build failed"
fi

# Already serving: just show it.
if answers; then
  echo "web app -> already running at $url"
  [ "${NO_OPEN:-}" = "1" ] || open "$url/calendar"
  exit 0
fi

# 3) Open the page as soon as the server answers, without holding the server up.
if [ "${NO_OPEN:-}" != "1" ]; then
  (for _ in $(seq 1 40); do answers && { open "$url/calendar"; exit 0; }; sleep 0.25; done) &
fi

# 4) The web app itself, in the foreground until Ctrl-C.
PORT="$port" exec "$repo/web/serve-all.sh"
