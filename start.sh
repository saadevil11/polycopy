#!/bin/bash

# Smart start script that chooses what to run based on SERVICE_TYPE env var

if [ "$SERVICE_TYPE" = "dashboard" ]; then
    echo "🎨 Starting Legacy Dashboard..."
    python tools/multi_bot_dashboard_api.py
elif [ "$SERVICE_TYPE" = "admin_dashboard" ]; then
    echo "👨‍💼 Starting Admin Dashboard..."
    python tools/admin_dashboard.py
elif [ "$SERVICE_TYPE" = "user_dashboard" ]; then
    echo "🌐 Starting User Dashboard (Next.js)..."
    cd frontend && npm run start
elif [ "$SERVICE_TYPE" = "bot" ]; then
    echo "🤖 Starting Trading Bot..."
    python start_bot.py
else
    # Default to bot if no SERVICE_TYPE is set (backward compatibility)
    echo "🤖 Starting Trading Bot (default)..."
    python start_bot.py
fi

