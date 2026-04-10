#!/bin/bash
# APEX Watchdog v2 (audit Phase 2A+2C, 2026-04-10)
# Monitors bot, streamlit, rebalancer. Restarts dead processes with crash-loop guard.
# Runs every 5 minutes via crontab.
#
# Phase 2A change: pgrep patterns require canonical "python3 -m bot.main" /
#                  "python3 -m apex.rebalancer" invocations only. Prevents the
#                  Apr 9 dual-bot incident where "python bot/main.py" coexisted.
# Phase 2C change: bot restart now has crash-loop guard. After MAX_RESTARTS_PER_HOUR
#                  failed restarts inside one hour, watchdog stops trying and logs
#                  a critical alert until manual intervention.

LOG="/home/opc/tradingbot/logs/watchdog.log"
DIR="/home/opc/tradingbot"
STREAMLIT="/home/opc/.local/bin/streamlit"
RESTART_STATE="/tmp/apex_bot_restart_state"
MAX_RESTARTS_PER_HOUR=3

timestamp() { date '+%Y-%m-%d %H:%M:%S'; }

# can_restart_bot: returns 0 (OK to restart) or 1 (guard active).
# Tracks restart count + first-attempt timestamp in $RESTART_STATE (one line: "ts count").
can_restart_bot() {
    local now last_ts count age
    now=$(date +%s)
    count=0
    last_ts=0

    if [ -f "$RESTART_STATE" ]; then
        last_ts=$(awk '{print $1}' "$RESTART_STATE" 2>/dev/null)
        count=$(awk '{print $2}' "$RESTART_STATE" 2>/dev/null)
        last_ts=${last_ts:-0}
        count=${count:-0}

        age=$((now - last_ts))
        # Reset counter if window expired (>1 hour since first restart in window)
        if [ "$age" -gt 3600 ]; then
            count=0
            last_ts=$now
        fi
    else
        last_ts=$now
    fi

    if [ "$count" -ge "$MAX_RESTARTS_PER_HOUR" ]; then
        return 1
    fi

    count=$((count + 1))
    echo "$last_ts $count" > "$RESTART_STATE"
    return 0
}

# clear_restart_state: called when bot is alive — resets the crash-loop counter.
clear_restart_state() {
    rm -f "$RESTART_STATE"
}

# Check bot — must be canonical "python3 -m bot.main" (escaped dot, exact prefix)
if ! pgrep -f "python3 -m bot\.main" > /dev/null; then
    if can_restart_bot; then
        attempt=$(awk '{print $2}' "$RESTART_STATE" 2>/dev/null)
        echo "[$(timestamp)] BOT DEAD — restart attempt ${attempt}/${MAX_RESTARTS_PER_HOUR}..." >> "$LOG"
        cd "$DIR"
        nohup python3 -m bot.main > "$DIR/logs/bot.log" 2>&1 &
        echo "[$(timestamp)] BOT restarted (PID $!)" >> "$LOG"
    else
        echo "[$(timestamp)] BOT DEAD but CRASH-LOOP GUARD ACTIVE (>=${MAX_RESTARTS_PER_HOUR} restarts in last hour) — NOT restarting; manual intervention required" >> "$LOG"
    fi
else
    # Bot alive — clear restart counter so future restarts get a fresh window.
    [ -f "$RESTART_STATE" ] && clear_restart_state
    echo "[$(timestamp)] BOT OK" >> "$LOG"
fi

# Check streamlit
if ! pgrep -f "streamlit" > /dev/null; then
    echo "[$(timestamp)] STREAMLIT DEAD — restarting..." >> "$LOG"
    cd "$DIR"
    nohup "$STREAMLIT" run dashboard.py --server.port 8501 --server.address 0.0.0.0 > "$DIR/logs/streamlit.log" 2>&1 &
    echo "[$(timestamp)] STREAMLIT restarted (PID $!)" >> "$LOG"
else
    echo "[$(timestamp)] STREAMLIT OK" >> "$LOG"
fi

# Check rebalancer — must be canonical "python3 -m apex.rebalancer" (escaped dot)
if ! pgrep -f "python3 -m apex\.rebalancer" > /dev/null; then
    echo "[$(timestamp)] REBALANCER DEAD — restarting..." >> "$LOG"
    cd "$DIR"
    nohup python3 -m apex.rebalancer schedule > "$DIR/logs/rebalancer.log" 2>&1 &
    echo "[$(timestamp)] REBALANCER restarted (PID $!)" >> "$LOG"
else
    echo "[$(timestamp)] REBALANCER OK" >> "$LOG"
fi
