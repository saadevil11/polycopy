#!/bin/bash

# Smart start script that chooses what to run based on SERVICE_TYPE env var

if [ "$SERVICE_TYPE" = "dashboard" ]; then
    echo "🎨 Starting Dashboard..."
    python tools/multi_bot_dashboard_api.py
else
    echo "🤖 Starting Trading Bot..."
    python start_bot.py
fi

