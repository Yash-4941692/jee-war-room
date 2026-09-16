#!/bin/bash
# ============================================================================
# JEE WAR ROOM — permanent self-healing supervisor
# Keeps BOTH the local Python server and the ngrok public tunnel alive forever,
# restarting anything that dies, and REPAIRING the environment after a sandbox
# reboot (the restore wipes execute bits and can drop files/config).
# Idempotent: safe to launch from many shells (flock-deduped).
# ============================================================================
APP_DIR="/home/user/jee-war-room"
LOG_DIR="$APP_DIR/data/logs"
LOCK="/tmp/jwr-guardian.lock"
DOMAIN="matrimony-reminder-relic.ngrok-free.dev"
PUBLIC_URL="https://$DOMAIN/"
LOCAL_PORT=8080
BIN_DIR="/tmp/tools"   # large CLI binaries live outside the persisted workspace snapshot
mkdir -p "$BIN_DIR"
NGROK="$BIN_DIR/ngrok"
NGROK_URL="https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz"
NGROK_CFG="/home/user/.config/ngrok/ngrok.yml"
NGROK_CFG_BAK="$APP_DIR/deploy/ngrok.yml"
APP_PIDF="/tmp/jwr-app.pid"; NG_PIDF="/tmp/jwr-ngrok.pid"
mkdir -p "$LOG_DIR" "$BIN_DIR" "$(dirname "$NGROK_CFG")"
LOG="$LOG_DIR/guardian.log"
exec 9>"$LOCK"
if ! flock -n 9; then exit 0; fi   # another guardian is already running
echo "[$(date '+%F %T')] guardian started (pid $$)" >> "$LOG"

alive_pid(){ [ -f "$1" ] && kill -0 "$(cat "$1" 2>/dev/null)" 2>/dev/null; }
port_listening(){ ss -ltn 2>/dev/null | grep -q ":$LOCAL_PORT "; }

# Make sure the ngrok binary exists, is executable, and has its authtoken config.
# Sandbox restores are known to reset the exec bit and occasionally drop files.
ensure_ngrok(){
  if [ ! -x "$NGROK" ]; then
    if [ -f "$NGROK" ]; then
      chmod +x "$NGROK" 2>/dev/null
      echo "[$(date '+%F %T')] repaired ngrok execute permission" >> "$LOG"
    else
      echo "[$(date '+%F %T')] ngrok missing — downloading" >> "$LOG"
      curl -fsSL "$NGROK_URL" -o /tmp/ngrok.tgz 2>>"$LOG" \
        && tar -xzf /tmp/ngrok.tgz -C "$BIN_DIR" 2>>"$LOG" \
        && chmod +x "$NGROK" 2>>"$LOG" && rm -f /tmp/ngrok.tgz
    fi
  fi
  if [ ! -s "$NGROK_CFG" ] && [ -s "$NGROK_CFG_BAK" ]; then
    cp "$NGROK_CFG_BAK" "$NGROK_CFG"
    echo "[$(date '+%F %T')] restored ngrok.yml from project backup" >> "$LOG"
  fi
  [ -x "$NGROK" ] || chmod +x "$NGROK" 2>/dev/null
  "$NGROK" version >/dev/null 2>&1
}

start_app(){
  echo "[$(date '+%F %T')] starting app server" >> "$LOG"
  ( cd "$APP_DIR" && exec python3 server.py ) >> "$LOG_DIR/server.log" 2>&1 9>&- &
  echo $! > "$APP_PIDF"; sleep 4
}

tunnel_proc_alive(){ pgrep -f "$NGROK http $LOCAL_PORT" >/dev/null 2>&1; }
edge_healthy(){
  local c
  c=$(curl -s -m 10 -H "ngrok-skip-browser-warning: true" -o /dev/null -w "%{http_code}" "$PUBLIC_URL" 2>/dev/null)
  [ "$c" = "200" ] || [ "$c" = "302" ] || [ "$c" = "401" ]
}
# Blue/green attach: endpoint pooling allows a SECOND agent to register while
# the old one stays up, so restarts never open an ERR_NGROK_3200 gap. The old
# agent is killed only after the newcomer is verified healthy on the edge.
start_tunnel(){
  ensure_ngrok || { echo "[$(date '+%F %T')] ngrok binary unusable" >> "$LOG"; sleep 10; return; }
  echo "[$(date '+%F %T')] starting ngrok tunnel (pooled blue/green)" >> "$LOG"
  local old=""; [ -f "$NG_PIDF" ] && old="$(cat "$NG_PIDF" 2>/dev/null)"
  nohup "$NGROK" http "$LOCAL_PORT" --domain="$DOMAIN" --region=in \
      --pooling-enabled --log=stdout >> "$LOG_DIR/ngrok.log" 2>&1 9>&- &
  local new=$!; echo "$new" > "$NG_PIDF"; sleep 8
  if kill -0 "$new" 2>/dev/null && edge_healthy; then
    local p
    for p in $(pgrep -x ngrok); do [ "$p" != "$new" ] && kill "$p" 2>/dev/null && echo "[$(date '+%F %T')] retired old ngrok agent $p" >> "$LOG"
    done
  else
    echo "[$(date '+%F %T')] new agent failed to become healthy — will retry" >> "$LOG"
    if [ -n "$old" ] && kill -0 "$old" 2>/dev/null; then kill "$new" 2>/dev/null; echo "$old" > "$NG_PIDF"; fi
  fi
}

# boot sequence: repair environment, then bring up both pieces immediately
ensure_ngrok
port_listening || start_app
tunnel_proc_alive || start_tunnel

while true; do
  sleep 5
  # 1) local app must be listening
  if ! port_listening; then
    echo "[$(date '+%F %T')] app port $LOCAL_PORT down — restarting" >> "$LOG"
    if alive_pid "$APP_PIDF"; then kill "$(cat "$APP_PIDF")" 2>/dev/null; sleep 2; fi
    start_app
  fi
  # 2) ngrok process must exist
  if ! tunnel_proc_alive; then
    echo "[$(date '+%F %T')] ngrok process missing — restarting" >> "$LOG"
    start_tunnel; continue
  fi
  # 3) public domain must serve the app (two consecutive checks)
  code=$(curl -s -m 10 -H "ngrok-skip-browser-warning: true" -o /dev/null -w "%{http_code}" "$PUBLIC_URL" 2>/dev/null)
  if [ "$code" != "200" ] && [ "$code" != "302" ] && [ "$code" != "401" ]; then
    sleep 5
    code2=$(curl -s -m 10 -H "ngrok-skip-browser-warning: true" -o /dev/null -w "%{http_code}" "$PUBLIC_URL" 2>/dev/null)
    if [ "$code2" != "200" ] && [ "$code2" != "302" ] && [ "$code2" != "401" ]; then
      echo "[$(date '+%F %T')] public tunnel unhealthy ($code/$code2) — restarting" >> "$LOG"
      start_tunnel
    fi
  fi
done
