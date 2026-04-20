#!/bin/bash
BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$BOT_DIR/bot.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "🛑 Stopping Mango Man (PID: $PID)..."
        kill "$PID"
        sleep 2
        if ps -p "$PID" > /dev/null 2>&1; then
            kill -9 "$PID"
        fi
        echo "✅ Bot stopped."
    else
        echo "⚠️ Process not found. Cleaning up PID file."
    fi
    rm -f "$PID_FILE"
else
    echo "⚠️ No PID file found. Trying to find process by name..."
    pkill -f "bot_main.py" || echo "✅ No running bot found."
fi
