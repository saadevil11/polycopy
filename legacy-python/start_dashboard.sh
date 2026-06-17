#!/bin/bash

# Multi-Bot Dashboard Launcher
# This script starts the unified dashboard for monitoring multiple bots

echo "🚀 Starting Multi-Bot Dashboard..."
echo "=" 
echo ""

# Check if config file exists
if [ ! -f "multi_bot_config.json" ]; then
    echo "❌ Configuration file not found: multi_bot_config.json"
    echo "📝 Please create it or copy from the example in docs/"
    exit 1
fi

# Check if Railway CLI is needed
if grep -q '"type": "railway"' multi_bot_config.json; then
    echo "🚂 Railway bot detected - checking Railway CLI..."
    if ! command -v railway &> /dev/null; then
        echo "❌ Railway CLI not found"
        echo "📦 Install it from: https://docs.railway.app/develop/cli"
        echo "   Or run: npm i -g @railway/cli"
        exit 1
    fi
    
    echo "✅ Railway CLI found"
    
    # Check if logged in
    if ! railway whoami &> /dev/null; then
        echo "❌ Not logged in to Railway"
        echo "🔐 Please run: railway login"
        exit 1
    fi
    
    echo "✅ Railway authentication OK"
fi

# Check Python dependencies
echo "📦 Checking Python dependencies..."
if ! python -c "import flask" 2>/dev/null; then
    echo "❌ Flask not installed"
    echo "📦 Installing required packages..."
    pip install flask
fi

echo "✅ Dependencies OK"
echo ""

# Determine which dashboard to run
if grep -q '"type": "railway"' multi_bot_config.json; then
    DASHBOARD_SCRIPT="tools/multi_bot_dashboard_railway.py"
    echo "🌐 Starting Railway-enabled dashboard..."
else
    DASHBOARD_SCRIPT="tools/multi_bot_dashboard.py"
    echo "🏠 Starting local dashboard..."
fi

# Check if script exists
if [ ! -f "$DASHBOARD_SCRIPT" ]; then
    echo "❌ Dashboard script not found: $DASHBOARD_SCRIPT"
    exit 1
fi

# Get port from config
PORT=$(grep -o '"port": [0-9]*' multi_bot_config.json | grep -o '[0-9]*')
if [ -z "$PORT" ]; then
    PORT=8080
fi

echo ""
echo "=" 
echo "📊 Dashboard starting on port $PORT"
echo "🌐 Open in browser: http://localhost:$PORT"
echo "🔄 Press Ctrl+C to stop"
echo "=" 
echo ""

# Run the dashboard
python "$DASHBOARD_SCRIPT"

