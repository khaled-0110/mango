#!/bin/bash
set -e

BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOT_SCRIPT="bot_main.py"
LOG_FILE="$BOT_DIR/bot.log"
PID_FILE="$BOT_DIR/bot.pid"

if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if ps -p "$OLD_PID" > /dev/null 2>&1; then
        echo "⚠️ Bot is already running (PID: $OLD_PID)."
        exit 0
    else
        rm -f "$PID_FILE"
    fi
fi

echo "🚀 Starting Mango Man..."
cd "$BOT_DIR"
> "$LOG_FILE" 

nohup python3 "$BOT_SCRIPT" >> "$LOG_FILE" 2>&1 &
NEW_PID=$!

echo "$NEW_PID" > "$PID_FILE"
echo "✅ Bot started (PID: $NEW_PID)"
sleep 3
tail -n 5 "$LOG_FILE"
