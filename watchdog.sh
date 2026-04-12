#!/bin/bash
# APEX Watchdog v3 (audit Phase 2A+2C+2G, 2026-04-11)
# Monitors bot, streamlit, rebalancer. Restarts dead processes with crash-loop guard.
# DETECTS AND KILLS DUPLICATE bot.main processes (Phase 2G).
# Runs every 5 minutes via crontab.
#
# Phase 2A change: pgrep patterns require canonical "python3 -m bot.main" /
#                  "python3 -m apex.rebalancer" invocations only. Prevents the
#                  Apr 9 dual-bot incident where "python bot/main.py" coexisted.
# Phase 2C change: bot restart now has crash-loop guard. After MAX_RESTARTS_PER_HOUR
#                  failed restarts inside one hour, watchdog stops trying and logs
#                  a critical alert until manual intervention.
# Phase 2G change: detect duplicate bot processes. If 2+ canonical bot.main processes
#                  exist, identify which one is writing to the active bot.log via
#                  /proc/<pid>/fd/1 and kill the others. Same for the apex.rebalancer
#                  daemon. Closes the recurrence risk where a watchdog auto-spawn
#                  during a deploy window can leave a zombie bot running for hours
#                  alongside the recovery bot (caught 2026-04-11 morning).

LOG="/home/opc/tradingbot/logs/watchdog.log"
DIR="/home/opc/tradingbot"
STREAMLIT="/home/opc/.local/bin/streamlit"
RESTART_STATE="/tmp/apex_bot_restart_state"
MAX_RESTARTS_PER_HOUR=3

timestamp() { date '+%Y-%m-%d %H:%M:%S'; }

# can_restart_bot: returns 0 (OK to restart) or 1 (guard active).
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

clear_restart_state() {
    rm -f "$RESTART_STATE"
}

# kill_duplicates: given a space-separated PID list and an expected log path,
# kill all PIDs whose stdout fd does NOT point to $expected_log. If no PID is
# writing to $expected_log, fall back to keeping the highest PID (newest).
# This converges multiple-process state to single-process without guessing.
#
# Args: $1 = space-separated PIDs, $2 = expected stdout path, $3 = service name (for logs)
kill_duplicates() {
    local pids="$1"
    local expected_log="$2"
    local svc="$3"
    local active_pid=""
    local pid log_fd

    # Find which PID is writing to the expected log
    for pid in $pids; do
        log_fd=$(readlink "/proc/$pid/fd/1" 2>/dev/null)
        if [ "$log_fd" = "$expected_log" ]; then
            active_pid=$pid
            break
        fi
    done

    # Fallback: no PID is writing to expected_log → keep highest PID (newest start)
    if [ -z "$active_pid" ]; then
        active_pid=$(echo "$pids" | tr ' ' '\n' | sort -n | tail -1)
        echo "[$(timestamp)] DUP $svc: no PID writing to $expected_log; keeping newest PID $active_pid" >> "$LOG"
    else
        echo "[$(timestamp)] DUP $svc: keeping active PID $active_pid (writing to $expected_log)" >> "$LOG"
    fi

    # Kill the others
    for pid in $pids; do
        if [ "$pid" != "$active_pid" ]; then
            kill -TERM "$pid" 2>>"$LOG"
            echo "[$(timestamp)] DUP $svc: killed PID $pid (SIGTERM)" >> "$LOG"
        fi
    done
}

# ----------------------------------------------------------------
# Check bot — must be canonical "python3 -m bot.main"
# ----------------------------------------------------------------
# Phase NAVIK coexistence: filter by cwd so each watchdog only manages its own bot
BOT_PIDS=""
for _pid in $(pgrep -f "python3 -m bot\.main"); do
    _cwd=$(readlink /proc/$_pid/cwd 2>/dev/null)
    [ "$_cwd" = "$DIR" ] && BOT_PIDS="$BOT_PIDS $_pid"
done
BOT_PIDS=$(echo "$BOT_PIDS" | xargs)
BOT_COUNT=$(echo "$BOT_PIDS" | wc -w)
[ -z "$BOT_PIDS" ] && BOT_COUNT=0

if [ "$BOT_COUNT" -eq 0 ]; then
    if can_restart_bot; then
        attempt=$(awk '{print $2}' "$RESTART_STATE" 2>/dev/null)
        echo "[$(timestamp)] BOT DEAD — restart attempt ${attempt}/${MAX_RESTARTS_PER_HOUR}..." >> "$LOG"
        cd "$DIR"
        nohup python3 -m bot.main > "$DIR/logs/bot.log" 2>&1 &
        echo "[$(timestamp)] BOT restarted (PID $!)" >> "$LOG"
    else
        echo "[$(timestamp)] BOT DEAD but CRASH-LOOP GUARD ACTIVE (>=${MAX_RESTARTS_PER_HOUR} restarts in last hour) — NOT restarting; manual intervention required" >> "$LOG"
    fi
elif [ "$BOT_COUNT" -eq 1 ]; then
    [ -f "$RESTART_STATE" ] && clear_restart_state
    echo "[$(timestamp)] BOT OK (PID $BOT_PIDS)" >> "$LOG"
else
    # Phase 2G: multiple bots — converge to one
    echo "[$(timestamp)] DUP BOT detected — $BOT_COUNT processes: $(echo $BOT_PIDS | tr '\n' ' ')" >> "$LOG"
    kill_duplicates "$BOT_PIDS" "$DIR/logs/bot.log" "BOT"
fi

# ----------------------------------------------------------------
# Check streamlit (no dup detection — read-only, less critical)
# ----------------------------------------------------------------
if ! pgrep -f "streamlit" > /dev/null; then
    echo "[$(timestamp)] STREAMLIT DEAD — restarting..." >> "$LOG"
    cd "$DIR"
    nohup "$STREAMLIT" run dashboard.py --server.port 8501 --server.address 0.0.0.0 > "$DIR/logs/streamlit.log" 2>&1 &
    echo "[$(timestamp)] STREAMLIT restarted (PID $!)" >> "$LOG"
else
    echo "[$(timestamp)] STREAMLIT OK" >> "$LOG"
fi

# ----------------------------------------------------------------
# Check rebalancer — must be canonical "python3 -m apex.rebalancer"
# Phase 2G: also dup-detect, but special-case: if a manual `rebalancer rebalance`
# is running alongside the daemon `rebalancer schedule`, that's INTENTIONAL and
# should NOT be killed. We only kill duplicates of the SAME subcommand.
# ----------------------------------------------------------------
REB_SCHED_PIDS=$(pgrep -f "python3 -m apex\.rebalancer schedule")
REB_SCHED_COUNT=$(echo "$REB_SCHED_PIDS" | grep -c .)

if [ "$REB_SCHED_COUNT" -eq 0 ]; then
    echo "[$(timestamp)] REBALANCER DEAD — restarting..." >> "$LOG"
    cd "$DIR"
    nohup python3 -m apex.rebalancer schedule > "$DIR/logs/rebalancer.log" 2>&1 &
    echo "[$(timestamp)] REBALANCER restarted (PID $!)" >> "$LOG"
elif [ "$REB_SCHED_COUNT" -eq 1 ]; then
    echo "[$(timestamp)] REBALANCER OK (PID $REB_SCHED_PIDS)" >> "$LOG"
else
    # Phase 2G: multiple rebalancer schedule daemons — converge to one
    echo "[$(timestamp)] DUP REBALANCER detected — $REB_SCHED_COUNT schedule daemons: $(echo $REB_SCHED_PIDS | tr '\n' ' ')" >> "$LOG"
    kill_duplicates "$REB_SCHED_PIDS" "$DIR/logs/rebalancer.log" "REBALANCER"
fi
