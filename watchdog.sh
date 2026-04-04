#!/bin/bash
# APEX Watchdog — monitors bot and streamlit, restarts if dead
# Runs every 5 minutes via crontab

LOG="/home/opc/tradingbot/logs/watchdog.log"
DIR="/home/opc/tradingbot"
STREAMLIT="/home/opc/.local/bin/streamlit"

timestamp() { date '+%Y-%m-%d %H:%M:%S'; }

# Check bot
if ! pgrep -f "bot.main" > /dev/null; then
    echo "[$(timestamp)] BOT DEAD — restarting..." >> $LOG
    cd $DIR
    nohup python3 -m bot.main > $DIR/logs/bot.log 2>&1 &
    echo "[$(timestamp)] BOT restarted (PID $!)" >> $LOG
else
    echo "[$(timestamp)] BOT OK" >> $LOG
fi

# Check streamlit
if ! pgrep -f "streamlit" > /dev/null; then
    echo "[$(timestamp)] STREAMLIT DEAD — restarting..." >> $LOG
    cd $DIR
    nohup $STREAMLIT run dashboard.py --server.port 8501 --server.address 0.0.0.0 > $DIR/logs/streamlit.log 2>&1 &
    echo "[$(timestamp)] STREAMLIT restarted (PID $!)" >> $LOG
else
    echo "[$(timestamp)] STREAMLIT OK" >> $LOG
fi

# Check rebalancer
if ! pgrep -f "apex.rebalancer" > /dev/null; then
    echo "[$(timestamp)] REBALANCER DEAD — restarting..." >> $LOG
    cd $DIR
    nohup python3 -m apex.rebalancer schedule > $DIR/logs/rebalancer.log 2>&1 &
    echo "[$(timestamp)] REBALANCER restarted (PID $!)" >> $LOG
else
    echo "[$(timestamp)] REBALANCER OK" >> $LOG
fi
