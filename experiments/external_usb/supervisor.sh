#!/bin/bash
# spdif-supervisor.sh
# Switches between Snapcast and JACK S/PDIF passthrough based on signal detection.
#
# Modes:
#   SNAPCAST  - snapclient runs as systemd service; polls ALSA for S/PDIF signal
#   JACK      - jackd runs for S/PDIF passthrough; watches for carrier loss and
#               Snapcast server activity
#
# Always starts in SNAPCAST mode.

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CARD=3
CARD_NAME="ICUSBAUDIO7D"
CAPTURE_DEVICE="hw:${CARD_NAME},0"
PLAYBACK_DEVICE="hw:sndrpimerusamp,0"

SNAPCAST_SERVICE="snapclient"
SNAPCAST_SERVER="evolution"
SNAPCAST_PORT=1780
SNAPCAST_STREAM="default"
SNAPCAST_POLL_INTERVAL=5       # seconds between Snapcast API polls in JACK mode

RT_PRIORITY=80
CPU_CORES="2,3"
RATE=48000
PERIOD_SIZE=1024
NUM_PERIODS=2

SILENCE_THRESHOLD=0.001        # RMS below this = no S/PDIF signal
SILENCE_PROBE_DURATION=1       # seconds per arecord probe
SILENCE_PROBE_DEVICE="hw:${CARD},0"
SILENCE_PROBE_FILE="/tmp/spdif_probe.wav"

SPDIF_DEBOUNCE=3               # consecutive active reads before switching to JACK
SNAPCAST_DEBOUNCE=3            # consecutive 'playing' reads before switching to Snapcast

CARRIER_STATUS_FILE="/proc/asound/card${CARD}/pcm0c/sub0/status"

LOG_TAG="spdif-supervisor"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [$LOG_TAG] $*" | tee -a /var/log/spdif-supervisor.log; }

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

STATE="snapcast"
JACK_PID=""

# ---------------------------------------------------------------------------
# Snapcast control
# ---------------------------------------------------------------------------

start_snapcast() {
    log "Starting snapclient"
    systemctl start "$SNAPCAST_SERVICE" || log "WARNING: failed to start $SNAPCAST_SERVICE"
}

stop_snapcast() {
    log "Stopping snapclient"
    systemctl stop "$SNAPCAST_SERVICE" || log "WARNING: failed to stop $SNAPCAST_SERVICE"
}

# ---------------------------------------------------------------------------
# JACK control
# ---------------------------------------------------------------------------

start_jack() {
    log "Starting JACK passthrough"
    cpufreq-set -g performance || true

    JACK_NO_AUDIO_RESERVATION=1 \
    taskset -c "$CPU_CORES" \
        chrt -f "$RT_PRIORITY" \
        jackd -R -P "$RT_PRIORITY" -d alsa \
            -C "$CAPTURE_DEVICE" \
            -P "$PLAYBACK_DEVICE" \
            -r "$RATE" -p "$PERIOD_SIZE" -n "$NUM_PERIODS" \
        >> /var/log/spdif-supervisor.log 2>&1 &
    JACK_PID=$!
    log "jackd started (PID $JACK_PID)"

    # Wait for JACK to be ready
    local retries=10
    while ! jack_lsp > /dev/null 2>&1; do
        sleep 0.5
        retries=$((retries - 1))
        if [ "$retries" -le 0 ]; then
            log "ERROR: JACK did not become ready in time"
            stop_jack
            return 1
        fi
    done

    log "Connecting JACK ports"
    jack_connect system:capture_1 system:playback_1
    jack_connect system:capture_2 system:playback_2
    log "JACK pipeline active"
}

stop_jack() {
    if [ -n "$JACK_PID" ] && kill -0 "$JACK_PID" 2>/dev/null; then
        log "Stopping JACK (PID $JACK_PID)"
        kill "$JACK_PID"
        wait "$JACK_PID" 2>/dev/null || true
    fi
    JACK_PID=""
    cpufreq-set -g ondemand || true
}

# ---------------------------------------------------------------------------
# Signal detection helpers
# ---------------------------------------------------------------------------

# Returns 0 (true) if S/PDIF signal is active, 1 if silent
check_spdif_signal() {
    echo "check_spdif_signal()"
    arecord -D "$SILENCE_PROBE_DEVICE" -f S16_LE -r 48000 -c 2 \
        -d "$SILENCE_PROBE_DURATION" -q "$SILENCE_PROBE_FILE" 2>/dev/null || return 1
    local rms
    rms=$(sox "$SILENCE_PROBE_FILE" -n stat 2>&1 | awk '/RMS\s+amplitude/ {print $3}')
    awk -v r="$rms" -v t="$SILENCE_THRESHOLD" 'BEGIN { exit !(r+0 > t+0) }'
}

# Returns the trigger_time field from the ALSA PCM status file
get_trigger_time() {
    awk '/trigger_time:/ { print $2 }' "$CARRIER_STATUS_FILE" 2>/dev/null
}

# Returns 0 (true) if Snapcast default stream is playing
check_snapcast_playing() {
    echo "check_snapcast_playing()"
    local status
    status=$(curl -sf --max-time 3 \
        "http://${SNAPCAST_SERVER}:${SNAPCAST_PORT}/jsonrpc" \
        -d '{"id":1,"jsonrpc":"2.0","method":"Server.GetStatus"}' \
        | python3 -c "
import json,sys
data = json.load(sys.stdin)
streams = {s['id']: s['status'] for s in data['result']['server']['streams']}
print(streams.get('${SNAPCAST_STREAM}', 'idle'))
" 2>/dev/null) || return 1
    [ "$status" = "playing" ]
}

# ---------------------------------------------------------------------------
# Mode: SNAPCAST
# Polls for S/PDIF signal. Switches to JACK after SPDIF_DEBOUNCE hits.
# ---------------------------------------------------------------------------

run_snapcast_mode() {
    log "Entering SNAPCAST mode"
    start_snapcast
    local active_count=0

    while true; do
        if check_spdif_signal; then
            active_count=$((active_count + 1))
            log "S/PDIF signal detected (${active_count}/${SPDIF_DEBOUNCE})"
            if [ "$active_count" -ge "$SPDIF_DEBOUNCE" ]; then
                log "S/PDIF stable — switching to JACK"
                stop_snapcast
                return 0  # caller switches to JACK mode
            fi
        else
            if [ "$active_count" -gt 0 ]; then
                log "S/PDIF signal lost (was ${active_count}) — resetting debounce"
            fi
            active_count=0
        fi
        # No extra sleep: arecord probe itself takes SILENCE_PROBE_DURATION seconds
    done
}

# ---------------------------------------------------------------------------
# Mode: JACK
# Watches for carrier loss (trigger_time change) and Snapcast activity.
# Switches back to Snapcast on either condition.
# ---------------------------------------------------------------------------

run_jack_mode() {
    log "Entering JACK mode"
    start_jack || { log "JACK failed to start — falling back to Snapcast"; return 0; }

    local last_trigger
    last_trigger=$(get_trigger_time)
    local snapcast_count=0
    local last_snapcast_check=0

    while true; do
        # --- Carrier loss check (fast, every 0.2s) ---
        local current_trigger
        current_trigger=$(get_trigger_time)
        if [ "$current_trigger" != "$last_trigger" ]; then
            log "Carrier loss detected (trigger_time changed) — switching to Snapcast"
            stop_jack
            return 0
        fi

        # --- JACK process health check ---
        if [ -n "$JACK_PID" ] && ! kill -0 "$JACK_PID" 2>/dev/null; then
            log "jackd died unexpectedly — switching to Snapcast"
            JACK_PID=""
            return 0
        fi

        # --- Snapcast activity check (every SNAPCAST_POLL_INTERVAL seconds) ---
        local now
        now=$(date +%s)
        if [ $((now - last_snapcast_check)) -ge "$SNAPCAST_POLL_INTERVAL" ]; then
            last_snapcast_check=$now
            if check_snapcast_playing; then
                snapcast_count=$((snapcast_count + 1))
                log "Snapcast stream active (${snapcast_count}/${SNAPCAST_DEBOUNCE})"
                if [ "$snapcast_count" -ge "$SNAPCAST_DEBOUNCE" ]; then
                    log "Snapcast stable — switching back"
                    stop_jack
                    return 0
                fi
            else
                if [ "$snapcast_count" -gt 0 ]; then
                    log "Snapcast went idle — resetting debounce"
                fi
                snapcast_count=0
            fi
        fi

        sleep 0.2
    done
}

# ---------------------------------------------------------------------------
# Cleanup on exit
# ---------------------------------------------------------------------------

cleanup() {
    log "Supervisor exiting — cleaning up"
    stop_jack
    start_snapcast
}
trap cleanup EXIT INT TERM

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

log "Supervisor started (PID $$)"
STATE="snapcast"

while true; do
    case "$STATE" in
        snapcast)
            run_snapcast_mode
            STATE="jack"
            ;;
        jack)
            run_jack_mode
            STATE="snapcast"
            ;;
    esac
done
