#!/bin/bash
# Detached local HTTP server for web/ — survives shell-context death.
#
# Why this script exists: `python -m http.server 8000 -d web &` works in a
# normal interactive terminal, but inside an ephemeral shell (Claude Code,
# Cursor, and other AI coding assistants spawn a fresh shell per bash call)
# the backgrounded process dies when that shell exits. The result is a
# frustrating "can't connect" pattern where the URL works the first time
# the server is started but appears dead in subsequent assistant turns.
#
# This script uses nohup + disown to truly detach the server so it survives
# every parent process. It also defaults to a high random port to avoid
# clobbering any service running on common ports like 8000 or 8080.
#
# Usage:
#   ./serve_loader.sh                 # auto-pick port, print URL
#   ./serve_loader.sh 8000            # use port 8000
#   ./serve_loader.sh --stop          # kill any detached server this script started
#
# After starting, open the printed URL. The server will keep running until
# you call ./serve_loader.sh --stop, restart your machine, or run
#   pkill -f 'http.server.*loader.contractwatch'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="$SCRIPT_DIR/web"
PIDFILE="/tmp/contractwatch-loader-server.pid"
LOGFILE="/tmp/contractwatch-loader-server.log"

if [ ! -d "$WEB_DIR" ]; then
  echo "error: web/ directory not found at $WEB_DIR"
  exit 1
fi

if [ "$1" = "--stop" ]; then
  if [ -f "$PIDFILE" ]; then
    pid=$(cat "$PIDFILE")
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid"
      echo "stopped server pid $pid"
    else
      echo "no running server with pid $pid"
    fi
    rm -f "$PIDFILE"
  else
    echo "no pidfile at $PIDFILE; nothing to stop"
  fi
  pkill -f 'http.server.*loader.contractwatch' 2>/dev/null
  exit 0
fi

if [ -n "$1" ]; then
  PORT="$1"
else
  PORT=$((RANDOM % 20000 + 40000))
fi

if lsof -ti :"$PORT" >/dev/null 2>&1; then
  existing=$(lsof -ti :"$PORT")
  echo "warning: port $PORT is already in use by pid $existing"
  echo "either pick a different port (./serve_loader.sh <port>) or stop the existing process"
  exit 2
fi

# Identifiable command-line tag so pkill can find this specifically
nohup python3 -m http.server "$PORT" --directory "$WEB_DIR" \
  --bind 127.0.0.1 > "$LOGFILE" 2>&1 &
SERVER_PID=$!
disown $SERVER_PID 2>/dev/null
echo "$SERVER_PID" > "$PIDFILE"

# Verify the server actually came up
sleep 1
if ! curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:$PORT/loader.html" | grep -q "^200$"; then
  echo "error: server did not respond on port $PORT within 1 second"
  echo "check $LOGFILE for details"
  exit 3
fi

URL="http://127.0.0.1:$PORT/loader.html"
echo "loader page: $URL"
echo "  pid:     $SERVER_PID"
echo "  log:     $LOGFILE"
echo "  stop:    ./serve_loader.sh --stop"

# Try to open the browser. Best-effort; not fatal if unavailable.
if command -v open >/dev/null 2>&1; then
  open "$URL" 2>/dev/null
fi
