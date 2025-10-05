#!/usr/bin/env python3
"""
Polymarket Copy Trading Bot GUI
Simple interface to configure and monitor the bot
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import asyncio
import json
import os
import sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv, set_key
import subprocess
import signal
import psutil

from src.core.config import trading_config, polymarket_config, bot_config
from src.core.copy_trading_bot import PolymarketCopyTradingBot
from src.core.market_filters import MarketFilter

class PolymarketBotGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("🤖 Polymarket Copy Trading Bot")
        self.root.geometry("1200x800")
        self.root.configure(bg='#1e1e1e')
        
        # Bot process tracking
        self.bot_process = None
        self.bot_instance = None
        self.bot_running = False
        
        # Load environment variables
        load_dotenv()
        
        # Create GUI
        self.create_widgets()
        self.load_current_settings()
        
        # Start monitoring loop
        self.monitor_bot()
        
    def create_widgets(self):
        """Create all GUI widgets"""
        
        # Main container
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Title
        title_label = ttk.Label(main_frame, text="🤖 Polymarket Copy Trading Bot", 
                               font=('Arial', 16, 'bold'))
        title_label.pack(pady=(0, 20))
        
        # Create notebook for tabs
        notebook = ttk.Notebook(main_frame)
        notebook.pack(fill=tk.BOTH, expand=True)
        
        # Configuration Tab
        self.create_config_tab(notebook)
        
        # Monitoring Tab
        self.create_monitoring_tab(notebook)
        
        # Positions Tab
        self.create_positions_tab(notebook)
        
        # Trades Tab
        self.create_trades_tab(notebook)
        
        # Status Tab
        self.create_status_tab(notebook)
        
        # Control Panel at bottom
        self.create_control_panel(main_frame)
        
    def create_config_tab(self, notebook):
        """Create configuration tab"""
        config_frame = ttk.Frame(notebook)
        notebook.add(config_frame, text="⚙️ Configuration")
        
        # Scrollable frame
        canvas = tk.Canvas(config_frame)
        scrollbar = ttk.Scrollbar(config_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Target Trader Section
        trader_frame = ttk.LabelFrame(scrollable_frame, text="🎯 Target Trader", padding=10)
        trader_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(trader_frame, text="Target Trader Address:").pack(anchor=tk.W)
        self.target_trader_var = tk.StringVar()
        trader_entry = ttk.Entry(trader_frame, textvariable=self.target_trader_var, width=60)
        trader_entry.pack(fill=tk.X, pady=(5, 0))
        
        # Trading Settings Section
        trading_frame = ttk.LabelFrame(scrollable_frame, text="💰 Trading Settings", padding=10)
        trading_frame.pack(fill=tk.X, pady=5)
        
        # Copy Percentage
        copy_frame = ttk.Frame(trading_frame)
        copy_frame.pack(fill=tk.X, pady=2)
        ttk.Label(copy_frame, text="Copy Percentage (%):").pack(side=tk.LEFT)
        self.copy_percentage_var = tk.StringVar()
        ttk.Entry(copy_frame, textvariable=self.copy_percentage_var, width=10).pack(side=tk.RIGHT)
        
        # Max Position Size
        max_pos_frame = ttk.Frame(trading_frame)
        max_pos_frame.pack(fill=tk.X, pady=2)
        ttk.Label(max_pos_frame, text="Max Position Size (USD):").pack(side=tk.LEFT)
        self.max_position_var = tk.StringVar()
        ttk.Entry(max_pos_frame, textvariable=self.max_position_var, width=10).pack(side=tk.RIGHT)
        
        # Min Position Size
        min_pos_frame = ttk.Frame(trading_frame)
        min_pos_frame.pack(fill=tk.X, pady=2)
        ttk.Label(min_pos_frame, text="Min Position Size (USD):").pack(side=tk.LEFT)
        self.min_position_var = tk.StringVar()
        ttk.Entry(min_pos_frame, textvariable=self.min_position_var, width=10).pack(side=tk.RIGHT)
        
        # Max Daily Loss
        max_loss_frame = ttk.Frame(trading_frame)
        max_loss_frame.pack(fill=tk.X, pady=2)
        ttk.Label(max_loss_frame, text="Max Daily Loss (USD):").pack(side=tk.LEFT)
        self.max_daily_loss_var = tk.StringVar()
        ttk.Entry(max_loss_frame, textvariable=self.max_daily_loss_var, width=10).pack(side=tk.RIGHT)
        
        # Max Positions
        max_positions_frame = ttk.Frame(trading_frame)
        max_positions_frame.pack(fill=tk.X, pady=2)
        ttk.Label(max_positions_frame, text="Max Positions:").pack(side=tk.LEFT)
        self.max_positions_var = tk.StringVar()
        ttk.Entry(max_positions_frame, textvariable=self.max_positions_var, width=10).pack(side=tk.RIGHT)
        
        # Trade Delay
        delay_frame = ttk.Frame(trading_frame)
        delay_frame.pack(fill=tk.X, pady=2)
        ttk.Label(delay_frame, text="Trade Delay (seconds):").pack(side=tk.LEFT)
        self.trade_delay_var = tk.StringVar()
        ttk.Entry(delay_frame, textvariable=self.trade_delay_var, width=10).pack(side=tk.RIGHT)
        
        # Market Filter Settings
        filter_frame = ttk.LabelFrame(scrollable_frame, text="🎯 Market Filters", padding=10)
        filter_frame.pack(fill=tk.X, pady=5)
        
        # Initialize market filter
        self.market_filter = MarketFilter()
        
        # Get current filters (from env or defaults)
        current_filters = self.market_filter.get_enabled_patterns()
        
        # Market filter checkboxes
        ttk.Label(filter_frame, text="Only copy trades for these markets:").pack(anchor=tk.W)
        
        # Create variables for checkboxes
        self.filter_vars = {}
        # Add all current filters (from env or defaults)
        for pattern in current_filters:
            var = tk.BooleanVar(value=True)  # These are active filters
            self.filter_vars[pattern] = var
            ttk.Checkbutton(filter_frame, text=pattern + "...", 
                          variable=var).pack(anchor=tk.W, pady=2)
        
        # Custom pattern entry
        custom_frame = ttk.Frame(filter_frame)
        custom_frame.pack(fill=tk.X, pady=(10, 0))
        
        ttk.Label(custom_frame, text="Add custom pattern:").pack(side=tk.LEFT)
        self.custom_pattern_var = tk.StringVar()
        pattern_entry = ttk.Entry(custom_frame, textvariable=self.custom_pattern_var, width=40)
        pattern_entry.pack(side=tk.LEFT, padx=5)
        
        def add_custom_pattern():
            pattern = self.custom_pattern_var.get().strip()
            if pattern:
                if pattern not in self.filter_vars:
                    # Add to filter
                    self.market_filter.add_pattern(pattern)
                    # Add checkbox
                    var = tk.BooleanVar(value=True)
                    self.filter_vars[pattern] = var
                    ttk.Checkbutton(filter_frame, text=pattern + "...", 
                                  variable=var).pack(anchor=tk.W, pady=2)
                    # Clear entry
                    self.custom_pattern_var.set("")
        
        ttk.Button(custom_frame, text="Add", command=add_custom_pattern).pack(side=tk.LEFT)
        
        # Safety Settings
        safety_frame = ttk.LabelFrame(scrollable_frame, text="🛡️ Safety Settings", padding=10)
        safety_frame.pack(fill=tk.X, pady=5)
        
        # Dry Run Mode
        self.dry_run_var = tk.BooleanVar()
        dry_run_check = ttk.Checkbutton(safety_frame, text="🧪 Dry Run Mode (Simulate only, no real trades)", 
                                       variable=self.dry_run_var)
        dry_run_check.pack(anchor=tk.W, pady=2)
        
        # Copy Merge Actions
        self.copy_merge_var = tk.BooleanVar()
        merge_check = ttk.Checkbutton(safety_frame, text="🔄 Copy merge actions from target trader", 
                                     variable=self.copy_merge_var)
        merge_check.pack(anchor=tk.W, pady=2)
        
        # Copy Redeem Actions
        self.copy_redeem_var = tk.BooleanVar()
        redeem_check = ttk.Checkbutton(safety_frame, text="💰 Copy redeem actions from target trader", 
                                      variable=self.copy_redeem_var)
        redeem_check.pack(anchor=tk.W, pady=2)
        
        # Wallet Settings
        wallet_frame = ttk.LabelFrame(scrollable_frame, text="💳 Wallet Settings", padding=10)
        wallet_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(wallet_frame, text="Private Key:").pack(anchor=tk.W)
        self.private_key_var = tk.StringVar()
        private_key_entry = ttk.Entry(wallet_frame, textvariable=self.private_key_var, width=60, show="*")
        private_key_entry.pack(fill=tk.X, pady=(5, 10))
        
        ttk.Label(wallet_frame, text="Funder Address:").pack(anchor=tk.W)
        self.funder_address_var = tk.StringVar()
        funder_entry = ttk.Entry(wallet_frame, textvariable=self.funder_address_var, width=60)
        funder_entry.pack(fill=tk.X, pady=(5, 0))
        
        # Save Settings Button
        save_btn = ttk.Button(scrollable_frame, text="💾 Save Settings", command=self.save_settings)
        save_btn.pack(pady=20)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
    def create_positions_tab(self, notebook):
        """Create positions tab"""
        positions_frame = ttk.Frame(notebook)
        notebook.add(positions_frame, text="📊 Positions")
        
        # Current Positions
        positions_table_frame = ttk.LabelFrame(positions_frame, text="📈 Current Positions", padding=10)
        positions_table_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Positions table
        columns = ("Market", "Side", "Position", "Entry", "Current", "Size", "Value", "P&L", "Trades", "Volume", "Ends")
        self.positions_tree = ttk.Treeview(positions_table_frame, columns=columns, show="headings", height=10)
        
        # Configure columns
        column_widths = {
            "Market": 300,  # Wider for full market questions
            "Side": 80,
            "Position": 80,
            "Entry": 80,
            "Current": 80,
            "Size": 80,
            "Value": 100,
            "P&L": 100,
            "Trades": 70,
            "Volume": 100,
            "Ends": 150
        }
        
        for col, width in column_widths.items():
            self.positions_tree.heading(col, text=col)
            self.positions_tree.column(col, width=width)
        
        # Scrollbars for positions table
        positions_v_scroll = ttk.Scrollbar(positions_table_frame, orient=tk.VERTICAL, command=self.positions_tree.yview)
        positions_h_scroll = ttk.Scrollbar(positions_table_frame, orient=tk.HORIZONTAL, command=self.positions_tree.xview)
        self.positions_tree.configure(yscrollcommand=positions_v_scroll.set, xscrollcommand=positions_h_scroll.set)
        
        # Pack positions table
        self.positions_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        positions_v_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        positions_h_scroll.pack(side=tk.BOTTOM, fill=tk.X)
        
        # Refresh button
        refresh_frame = ttk.Frame(positions_frame)
        refresh_frame.pack(fill=tk.X, pady=5)
        ttk.Button(refresh_frame, text="🔄 Refresh Positions", command=self.refresh_positions).pack(side=tk.LEFT)
        
        # Activity Log
        activity_frame = ttk.LabelFrame(positions_frame, text="📝 Position Activity", padding=10)
        activity_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Activity table
        columns = ("Time", "Market", "Action", "Side", "Position", "Size", "Price", "Amount", "P&L")
        self.activity_tree = ttk.Treeview(activity_frame, columns=columns, show="headings", height=10)
        
        # Configure columns
        column_widths = {
            "Time": 150,
            "Market": 300,
            "Action": 80,
            "Side": 80,
            "Position": 80,
            "Size": 80,
            "Price": 80,
            "Amount": 100,
            "P&L": 100
        }
        
        for col, width in column_widths.items():
            self.activity_tree.heading(col, text=col)
            self.activity_tree.column(col, width=width)
        
        # Scrollbars for activity table
        activity_v_scroll = ttk.Scrollbar(activity_frame, orient=tk.VERTICAL, command=self.activity_tree.yview)
        activity_h_scroll = ttk.Scrollbar(activity_frame, orient=tk.HORIZONTAL, command=self.activity_tree.xview)
        self.activity_tree.configure(yscrollcommand=activity_v_scroll.set, xscrollcommand=activity_h_scroll.set)
        
        # Pack activity table
        self.activity_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        activity_v_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        activity_h_scroll.pack(side=tk.BOTTOM, fill=tk.X)
        
        # Start auto-refresh
        self.refresh_positions()
        self.root.after(30000, self.auto_refresh_positions)  # Refresh every 30 seconds
        
    def refresh_positions(self):
        """Refresh positions display"""
        try:
            # Clear existing items
            for item in self.positions_tree.get_children():
                self.positions_tree.delete(item)
                
            # Load positions from database
            if os.path.exists("trades.db"):
                conn = sqlite3.connect("trades.db")
                cursor = conn.cursor()
                
                # Get current positions with market info
                cursor.execute("""
                    WITH position_summary AS (
                        SELECT 
                            ct.market_id,
                            tt.token_id,
                            tt.side,
                            SUM(CASE WHEN tt.side = 'BUY' THEN ct.copy_size ELSE -ct.copy_size END) as net_size,
                            AVG(tt.price) as avg_price,
                            MAX(ct.execution_timestamp) as last_trade,
                            COUNT(*) as num_trades,
                            SUM(ct.copy_amount_usd) as total_volume
                        FROM copy_trades ct
                        JOIN target_trades tt ON ct.original_trade_id = tt.trade_id
                        WHERE ct.status = 'executed'
                        GROUP BY ct.market_id
                        HAVING ABS(net_size) > 0.0001
                    )
                    SELECT 
                        ps.*,
                        m.title as market_title,
                        m.current_price,
                        m.end_date
                    FROM position_summary ps
                    LEFT JOIN markets m ON ps.market_id = m.market_id
                    ORDER BY ps.last_trade DESC
                """)
                
                positions = cursor.fetchall()
                for pos in positions:
                    (market_id, token_id, side, size, price, timestamp, 
                     num_trades, total_volume, market_title, current_price, end_date) = pos
                    
                    # Use stored current price or fallback to entry price
                    current = float(current_price) if current_price else float(price)
                    
                    # Calculate position details
                    abs_size = abs(float(size))
                    entry_price = float(price)
                    current_value = abs_size * current
                    
                    # Calculate P&L based on position side
                    if float(size) > 0:  # Long position
                        unrealized_pnl = (current - entry_price) * abs_size
                        side_display = "🟢 BUY"
                        position = "YES" if entry_price > 0.5 else "NO"
                    else:  # Short position
                        unrealized_pnl = (entry_price - current) * abs_size
                        side_display = "🔴 SELL"
                        position = "NO" if entry_price > 0.5 else "YES"
                    
                    # Format P&L with color
                    if unrealized_pnl > 0:
                        pnl_display = f"🟢 +${unrealized_pnl:.2f}"
                    elif unrealized_pnl < 0:
                        pnl_display = f"🔴 ${unrealized_pnl:.2f}"
                    else:
                        pnl_display = f"${unrealized_pnl:.2f}"
                    
                    # Format end date
                    end_time = ""
                    if end_date:
                        try:
                            dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                            dt = dt.astimezone()  # Convert to local timezone
                            end_time = dt.strftime("%Y-%m-%d %H:%M")
                        except:
                            end_time = end_date[:16]
                    
                    # Insert into tree
                    self.positions_tree.insert("", tk.END, values=(
                        market_title or market_id,
                        side_display,
                        position,
                        f"${entry_price:.3f}",
                        f"${current:.3f}",
                        f"{abs_size:.2f}",
                        f"${current_value:.2f}",
                        pnl_display,
                        num_trades,
                        f"${float(total_volume):.2f}",
                        end_time
                    ))
                
                # Get recent activity
                cursor.execute("""
                    SELECT 
                        ct.execution_timestamp,
                        m.title as market_title,
                        CASE 
                            WHEN ct.original_trade_id LIKE '%merge%' THEN '🔄 Merge'
                            ELSE tt.side 
                        END as action,
                        tt.side,
                        tt.price,
                        ct.copy_size,
                        ct.copy_amount_usd,
                        ct.status,
                        m.market_id
                    FROM copy_trades ct
                    JOIN target_trades tt ON ct.original_trade_id = tt.trade_id
                    LEFT JOIN markets m ON ct.market_id = m.market_id
                    WHERE ct.status = 'executed'
                    ORDER BY ct.execution_timestamp DESC
                    LIMIT 100
                """)
                
                activity = cursor.fetchall()
                for act in activity:
                    (timestamp, market_title, action, side, price, size, 
                     amount, status, market_id) = act
                    
                    # Format timestamp
                    try:
                        dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                        dt = dt.astimezone()  # Convert to local timezone
                        time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                    except:
                        time_str = timestamp[:19]
                    
                    # Format action and side
                    if action == '🔄 Merge':
                        side_display = "🔄 Merge"
                        position_display = "🔄 Merge"
                        action_display = "🔄 Merge"
                    else:
                        side_display = "🟢 BUY" if side == "BUY" else "🔴 SELL"
                        position_display = "YES" if float(price) > 0.5 else "NO"
                        action_display = "✅ Open" if side == "BUY" else "❌ Close"
                    
                    # Calculate P&L if available
                    pnl = 0.0  # TODO: Calculate actual P&L when available
                    if pnl > 0:
                        pnl_display = f"🟢 +${pnl:.2f}"
                    elif pnl < 0:
                        pnl_display = f"🔴 ${pnl:.2f}"
                    else:
                        pnl_display = f"${pnl:.2f}"
                    
                    # Insert into activity tree
                    self.activity_tree.insert("", tk.END, values=(
                        time_str,
                        market_title or market_id,
                        action_display,
                        side_display,
                        position_display,
                        f"{abs(float(size)):.2f}",
                        f"${float(price):.3f}",
                        f"${float(amount):.2f}",
                        pnl_display
                    ))
                
                conn.close()
                
        except Exception as e:
            print(f"Error refreshing positions: {e}")
            
    def auto_refresh_positions(self):
        """Auto refresh positions every 30 seconds if tab is visible"""
        try:
            # Only refresh if positions tab is visible
            current_tab = self.root.children['!frame'].children['!notebook'].select()
            tab_name = self.root.children['!frame'].children['!notebook'].tab(current_tab, "text")
            if tab_name == "📊 Positions":
                self.refresh_positions()
        except Exception as e:
            print(f"Error in auto refresh: {e}")
            
        # Schedule next refresh
        self.root.after(30000, self.auto_refresh_positions)
        
    def create_monitoring_tab(self, notebook):
        """Create monitoring tab"""
        monitor_frame = ttk.Frame(notebook)
        notebook.add(monitor_frame, text="📊 Monitoring")
        
        # Status indicators
        status_frame = ttk.LabelFrame(monitor_frame, text="🤖 Bot Status", padding=10)
        status_frame.pack(fill=tk.X, pady=5)
        
        # Status grid
        status_grid = ttk.Frame(status_frame)
        status_grid.pack(fill=tk.X)
        
        # Bot Status
        ttk.Label(status_grid, text="Bot Status:").grid(row=0, column=0, sticky=tk.W, padx=5)
        self.bot_status_label = ttk.Label(status_grid, text="🔴 Stopped", foreground="red")
        self.bot_status_label.grid(row=0, column=1, sticky=tk.W, padx=5)
        
        # WebSocket Status
        ttk.Label(status_grid, text="WebSocket:").grid(row=0, column=2, sticky=tk.W, padx=5)
        self.websocket_status_label = ttk.Label(status_grid, text="🔴 Disconnected", foreground="red")
        self.websocket_status_label.grid(row=0, column=3, sticky=tk.W, padx=5)
        
        # Target Trader
        ttk.Label(status_grid, text="Target Trader:").grid(row=1, column=0, sticky=tk.W, padx=5)
        self.target_trader_label = ttk.Label(status_grid, text="Not set")
        self.target_trader_label.grid(row=1, column=1, columnspan=3, sticky=tk.W, padx=5)
        
        # Metrics
        metrics_frame = ttk.LabelFrame(monitor_frame, text="📈 Metrics", padding=10)
        metrics_frame.pack(fill=tk.X, pady=5)
        
        metrics_grid = ttk.Frame(metrics_frame)
        metrics_grid.pack(fill=tk.X)
        
        # Trades Today
        ttk.Label(metrics_grid, text="Trades Today:").grid(row=0, column=0, sticky=tk.W, padx=5)
        self.trades_today_label = ttk.Label(metrics_grid, text="0")
        self.trades_today_label.grid(row=0, column=1, sticky=tk.W, padx=5)
        
        # Success Rate
        ttk.Label(metrics_grid, text="Success Rate:").grid(row=0, column=2, sticky=tk.W, padx=5)
        self.success_rate_label = ttk.Label(metrics_grid, text="0%")
        self.success_rate_label.grid(row=0, column=3, sticky=tk.W, padx=5)
        
        # Total Positions
        ttk.Label(metrics_grid, text="Open Positions:").grid(row=1, column=0, sticky=tk.W, padx=5)
        self.positions_label = ttk.Label(metrics_grid, text="0")
        self.positions_label.grid(row=1, column=1, sticky=tk.W, padx=5)
        
        # Daily P&L
        ttk.Label(metrics_grid, text="Daily P&L:").grid(row=1, column=2, sticky=tk.W, padx=5)
        self.daily_pnl_label = ttk.Label(metrics_grid, text="$0.00")
        self.daily_pnl_label.grid(row=1, column=3, sticky=tk.W, padx=5)
        
        # Log viewer
        log_frame = ttk.LabelFrame(monitor_frame, text="📝 Bot Logs", padding=10)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        self.log_text = scrolledtext.ScrolledText(log_frame, height=15, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        # Log controls
        log_controls = ttk.Frame(log_frame)
        log_controls.pack(fill=tk.X, pady=(5, 0))
        
        ttk.Button(log_controls, text="🔄 Refresh Logs", command=self.refresh_logs).pack(side=tk.LEFT)
        ttk.Button(log_controls, text="🗑️ Clear", command=self.clear_logs).pack(side=tk.LEFT, padx=5)
        
    def create_trades_tab(self, notebook):
        """Create trades tab"""
        trades_frame = ttk.Frame(notebook)
        notebook.add(trades_frame, text="💼 Trades")
        
        # Trade filters
        filter_frame = ttk.LabelFrame(trades_frame, text="🔍 Filters", padding=10)
        filter_frame.pack(fill=tk.X, pady=5)
        
        # Top row - Status and Date filters
        top_filter_controls = ttk.Frame(filter_frame)
        top_filter_controls.pack(fill=tk.X, pady=(0, 5))
        
        # Status filter
        ttk.Label(top_filter_controls, text="Status:").pack(side=tk.LEFT)
        self.status_filter_var = tk.StringVar(value="all")
        status_combo = ttk.Combobox(top_filter_controls, textvariable=self.status_filter_var, 
                                   values=["all", "trades", "merges", "executed", "filtered", "pending"], width=12)
        status_combo.pack(side=tk.LEFT, padx=5)
        
        # Market Filters
        market_frame = ttk.LabelFrame(filter_frame, text="📈 Market Filters", padding=5)
        market_frame.pack(fill=tk.X, pady=5)
        
        # Default market patterns
        self.market_filter_vars = {}
        default_patterns = [
            "Bitcoin Up or Down on",
            "Ethereum Up or Down on",
            "Solana Up or Down on"
        ]
        
        # Create checkboxes for default patterns
        for pattern in default_patterns:
            var = tk.BooleanVar(value=False)
            self.market_filter_vars[pattern] = var
            ttk.Checkbutton(market_frame, text=pattern + "...", variable=var, 
                          command=self.refresh_trades).pack(anchor=tk.W)
        
        # Custom pattern entry
        custom_frame = ttk.Frame(market_frame)
        custom_frame.pack(fill=tk.X, pady=(5, 0))
        
        ttk.Label(custom_frame, text="Custom Pattern:").pack(side=tk.LEFT)
        self.custom_pattern_var = tk.StringVar()
        pattern_entry = ttk.Entry(custom_frame, textvariable=self.custom_pattern_var, width=40)
        pattern_entry.pack(side=tk.LEFT, padx=5)
        
        def add_custom_pattern():
            pattern = self.custom_pattern_var.get().strip()
            if pattern and pattern not in self.market_filter_vars:
                var = tk.BooleanVar(value=True)
                self.market_filter_vars[pattern] = var
                ttk.Checkbutton(market_frame, text=pattern + "...", 
                              variable=var, command=self.refresh_trades).pack(anchor=tk.W)
                self.custom_pattern_var.set("")  # Clear entry
                self.refresh_trades()
        
        ttk.Button(custom_frame, text="Add Filter", command=add_custom_pattern).pack(side=tk.LEFT, padx=5)

        # Date range filter (in top controls)
        ttk.Label(top_filter_controls, text="Time Range:").pack(side=tk.LEFT, padx=(10, 0))
        self.date_filter_var = tk.StringVar(value="all")
        date_combo = ttk.Combobox(top_filter_controls, textvariable=self.date_filter_var, 
                                 values=["all", "today", "yesterday", "this week", "this month", "this year"], width=12)
        date_combo.pack(side=tk.LEFT, padx=5)

        # Custom date range
        self.custom_start_var = tk.StringVar()
        self.custom_end_var = tk.StringVar()
        
        def show_date_picker():
            dialog = tk.Toplevel(self.root)
            dialog.title("Custom Date Range")
            dialog.geometry("300x150")
            
            # Start date
            start_frame = ttk.Frame(dialog)
            start_frame.pack(fill=tk.X, padx=10, pady=5)
            ttk.Label(start_frame, text="Start Date:").pack(side=tk.LEFT)
            start_entry = ttk.Entry(start_frame, textvariable=self.custom_start_var)
            start_entry.pack(side=tk.LEFT, padx=5)
            ttk.Label(start_frame, text="(YYYY-MM-DD)").pack(side=tk.LEFT)
            
            # End date
            end_frame = ttk.Frame(dialog)
            end_frame.pack(fill=tk.X, padx=10, pady=5)
            ttk.Label(end_frame, text="End Date:").pack(side=tk.LEFT)
            end_entry = ttk.Entry(end_frame, textvariable=self.custom_end_var)
            end_entry.pack(side=tk.LEFT, padx=5)
            ttk.Label(end_frame, text="(YYYY-MM-DD)").pack(side=tk.LEFT)
            
            def apply_dates():
                self.date_filter_var.set("custom")
                dialog.destroy()
                self.refresh_trades()
            
            ttk.Button(dialog, text="Apply", command=apply_dates).pack(pady=10)
            
        ttk.Button(top_filter_controls, text="📅 Custom Range", 
                  command=show_date_picker).pack(side=tk.LEFT, padx=5)
        
        ttk.Button(top_filter_controls, text="🔄 Refresh", 
                  command=self.refresh_trades).pack(side=tk.LEFT, padx=5)
        
        # Trades table
        trades_table_frame = ttk.LabelFrame(trades_frame, text="📊 Recent Trades", padding=10)
        trades_table_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Treeview for trades
        columns = ("Time", "Market", "Side", "Position", "Target Amount", "Our Amount", "Status")
        self.trades_tree = ttk.Treeview(trades_table_frame, columns=columns, show="headings", height=15)
        
        # Configure columns
        column_widths = {
            "Time": 150,  # Wider for date + time
            "Market": 300,  # Wider for full market questions
            "Side": 80,
            "Position": 80,
            "Target Amount": 120,
            "Our Amount": 120,
            "Status": 100
        }
        
        for col, width in column_widths.items():
            self.trades_tree.heading(col, text=col)
            self.trades_tree.column(col, width=width)
        
        # Scrollbars for trades table
        trades_v_scroll = ttk.Scrollbar(trades_table_frame, orient=tk.VERTICAL, command=self.trades_tree.yview)
        trades_h_scroll = ttk.Scrollbar(trades_table_frame, orient=tk.HORIZONTAL, command=self.trades_tree.xview)
        self.trades_tree.configure(yscrollcommand=trades_v_scroll.set, xscrollcommand=trades_h_scroll.set)
        
        # Pack trades table
        self.trades_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        trades_v_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        trades_h_scroll.pack(side=tk.BOTTOM, fill=tk.X)
        
    def create_status_tab(self, notebook):
        """Create status tab"""
        status_frame = ttk.Frame(notebook)
        notebook.add(status_frame, text="📋 Status")
        
        # Account info
        account_frame = ttk.LabelFrame(status_frame, text="💳 Account Info", padding=10)
        account_frame.pack(fill=tk.X, pady=5)
        
        account_grid = ttk.Frame(account_frame)
        account_grid.pack(fill=tk.X)
        
        ttk.Label(account_grid, text="USDC Balance:").grid(row=0, column=0, sticky=tk.W, padx=5)
        self.usdc_balance_label = ttk.Label(account_grid, text="Loading...")
        self.usdc_balance_label.grid(row=0, column=1, sticky=tk.W, padx=5)
        
        ttk.Button(account_grid, text="🔄 Check Balance", command=self.check_balance).grid(row=0, column=2, padx=10)
        
        # System info
        system_frame = ttk.LabelFrame(status_frame, text="🖥️ System Info", padding=10)
        system_frame.pack(fill=tk.X, pady=5)
        
        self.system_info_text = scrolledtext.ScrolledText(system_frame, height=10, wrap=tk.WORD)
        self.system_info_text.pack(fill=tk.BOTH, expand=True)
        
        self.update_system_info()
        
    def create_control_panel(self, parent):
        """Create control panel"""
        control_frame = ttk.LabelFrame(parent, text="🎮 Bot Control", padding=10)
        control_frame.pack(fill=tk.X, pady=(10, 0))
        
        # Control buttons
        button_frame = ttk.Frame(control_frame)
        button_frame.pack(fill=tk.X)
        
        self.start_btn = ttk.Button(button_frame, text="🚀 Start Bot", command=self.start_bot)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        
        self.stop_btn = ttk.Button(button_frame, text="🛑 Stop Bot", command=self.stop_bot)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        self.stop_btn.configure(state=tk.DISABLED)
        
        ttk.Button(button_frame, text="📊 Open Dashboard", command=self.open_dashboard).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="💰 Check Balance", command=self.check_balance).pack(side=tk.LEFT, padx=5)
        
        # Status bar
        self.status_var = tk.StringVar(value="Ready to start bot")
        status_bar = ttk.Label(control_frame, textvariable=self.status_var, relief=tk.SUNKEN)
        status_bar.pack(fill=tk.X, pady=(10, 0))
        
    def load_current_settings(self):
        """Load current settings from .env file"""
        try:
            self.target_trader_var.set(os.getenv("TARGET_TRADER_ADDRESS", ""))
            self.copy_percentage_var.set(str(float(os.getenv("COPY_PERCENTAGE", "0.1")) * 100))
            self.max_position_var.set(os.getenv("MAX_POSITION_SIZE_USD", "1000"))
            self.min_position_var.set(os.getenv("MIN_POSITION_SIZE_USD", "1"))
            self.max_daily_loss_var.set(os.getenv("MAX_DAILY_LOSS_USD", "500"))
            self.max_positions_var.set(os.getenv("MAX_POSITIONS", "10"))
            self.trade_delay_var.set(os.getenv("TRADE_DELAY_SECONDS", "5"))
            self.dry_run_var.set(os.getenv("DRY_RUN", "true").lower() == "true")
            self.copy_merge_var.set(os.getenv("COPY_MERGE_ACTIONS", "true").lower() == "true")
            self.copy_redeem_var.set(os.getenv("COPY_REDEEM_ACTIONS", "true").lower() == "true")
            self.private_key_var.set(os.getenv("PRIVATE_KEY", ""))
            self.funder_address_var.set(os.getenv("FUNDER_ADDRESS", ""))
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load settings: {e}")
            
    def update_market_filters(self):
        """Update market filter patterns based on checkboxes"""
        enabled_patterns = []
        for pattern, var in self.filter_vars.items():
            if var.get():  # If checkbox is checked
                enabled_patterns.append(pattern)
        self.market_filter.set_enabled_patterns(enabled_patterns)
    
    def save_settings(self):
        """Save settings to .env file"""
        try:
            env_file = ".env"
            
            # Convert percentage back to decimal
            copy_percentage = float(self.copy_percentage_var.get()) / 100
            
            settings = {
                "TARGET_TRADER_ADDRESS": self.target_trader_var.get(),
                "COPY_PERCENTAGE": str(copy_percentage),
                "MAX_POSITION_SIZE_USD": self.max_position_var.get(),
                "MIN_POSITION_SIZE_USD": self.min_position_var.get(),
                "MAX_DAILY_LOSS_USD": self.max_daily_loss_var.get(),
                "MAX_POSITIONS": self.max_positions_var.get(),
                "TRADE_DELAY_SECONDS": self.trade_delay_var.get(),
                "DRY_RUN": str(self.dry_run_var.get()).lower(),
                "COPY_MERGE_ACTIONS": str(self.copy_merge_var.get()).lower(),
                "COPY_REDEEM_ACTIONS": str(self.copy_redeem_var.get()).lower(),
                "PRIVATE_KEY": self.private_key_var.get(),
                "FUNDER_ADDRESS": self.funder_address_var.get(),
            }
            
            for key, value in settings.items():
                set_key(env_file, key, value)
            
            messagebox.showinfo("Success", "Settings saved successfully!")
            self.status_var.set("Settings saved")
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save settings: {e}")
            
    def start_bot(self):
        """Start the bot"""
        try:
            # Validate settings first
            if not self.target_trader_var.get():
                messagebox.showerror("Error", "Please set a target trader address")
                return
                
            if not self.private_key_var.get():
                messagebox.showerror("Error", "Please set your private key")
                return
            
            # Save current settings
            self.save_settings()
            
            # Start bot process
            # Clear log file and old status
            with open("polymarket_bot.log", "w") as f:
                f.write("")
            self.websocket_status_label.config(text="🔴 Disconnected", foreground="red")
            self.bot_status_label.config(text="🔴 Starting...", foreground="orange")
            
            # Start bot process
            self.bot_process = subprocess.Popen(
                ["python", "start_bot.py"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            
            self.start_btn.configure(state=tk.DISABLED)
            self.stop_btn.configure(state=tk.NORMAL)
            self.status_var.set("Starting bot...")
            
            # Wait a moment for bot to initialize
            self.root.after(2000, self.check_bot_startup)
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to start bot: {e}")
            
    def check_bot_startup(self):
        """Check if bot has started successfully"""
        if os.path.exists("polymarket_bot.log"):
            with open("polymarket_bot.log", "r") as f:
                log_content = f.read()
                if "Bot started successfully" in log_content:
                    self.bot_running = True
                    self.status_var.set("Bot running")
                    messagebox.showinfo("Success", "Bot started successfully!")
                elif "Failed to initialize bot" in log_content:
                    self.bot_running = False
                    self.status_var.set("Failed to start bot")
                    messagebox.showerror("Error", "Failed to start bot. Check logs for details.")
                else:
                    # Still starting, check again in a second
                    self.root.after(1000, self.check_bot_startup)
        else:
            # Log file not created yet, check again in a second
            self.root.after(1000, self.check_bot_startup)
    
    def stop_bot(self):
        """Stop the bot"""
        try:
            if self.bot_process:
                # Try graceful shutdown first
                self.bot_process.terminate()
                
                # Wait a moment for graceful shutdown
                try:
                    self.bot_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # Force kill if needed
                    self.bot_process.kill()
                    
                self.bot_process = None
            
            self.bot_running = False
            self.start_btn.configure(state=tk.NORMAL)
            self.stop_btn.configure(state=tk.DISABLED)
            self.status_var.set("Bot stopped")
            
            messagebox.showinfo("Success", "Bot stopped successfully!")
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to stop bot: {e}")
            
    def monitor_bot(self):
        """Monitor bot status"""
        try:
            if self.bot_running and self.bot_process:
                # Check if process is still running
                if self.bot_process.poll() is not None:
                    # Process has ended
                    self.bot_running = False
                    self.start_btn.configure(state=tk.NORMAL)
                    self.stop_btn.configure(state=tk.DISABLED)
                    self.status_var.set("Bot stopped unexpectedly")
                    
            # Update status displays
            self.update_status_display()
            
        except Exception as e:
            print(f"Error monitoring bot: {e}")
            
        # Schedule next check
        self.root.after(1000, self.monitor_bot)  # Check every second
        
    def update_status_display(self):
        """Update status display elements"""
        try:
            # If bot is not running, show disconnected status
            if not self.bot_running:
                self.websocket_status_label.config(text="🔴 Disconnected", foreground="red")
                self.bot_status_label.config(text="🔴 Stopped", foreground="red")
                return

            # Check log file for latest status
            if os.path.exists("polymarket_bot.log"):
                with open("polymarket_bot.log", "r") as f:
                    lines = f.readlines()
                    # Get the last 100 lines but only from the current bot session
                    recent_lines = []
                    for line in reversed(lines):
                        if "Starting Polymarket Copy Trading Bot" in line:
                            break
                        recent_lines.insert(0, line)
                        if len(recent_lines) >= 100:
                            break
                    
                    # Default to disconnected
                    websocket_status = "🔴 Disconnected"
                    websocket_color = "red"
                    
                    # Check most recent WebSocket status
                    for line in reversed(recent_lines):
                        if "WebSocket connected" in line:
                            websocket_status = "🟢 Connected"
                            websocket_color = "green"
                            break
                        elif "WebSocket error" in line or "WebSocket connection error" in line:
                            websocket_status = "🔴 Disconnected"
                            websocket_color = "red"
                            break
                    
                    self.websocket_status_label.config(text=websocket_status, foreground=websocket_color)
                    
                    # Update bot status
                    for line in reversed(recent_lines):
                        if "Bot started successfully" in line:
                            self.bot_status_label.config(text="🟢 Running", foreground="green")
                            self.status_var.set("Bot running")
                            self.bot_running = True
                            break
                        elif "Bot stopped" in line:
                            self.bot_status_label.config(text="🔴 Stopped", foreground="red")
                            self.status_var.set("Bot stopped")
                            self.bot_running = False
                            break
            
            # Update target trader display
            target = self.target_trader_var.get()
            if target:
                short_address = f"{target[:8]}...{target[-6:]}" if len(target) > 20 else target
                self.target_trader_label.config(text=short_address)
            else:
                self.target_trader_label.config(text="Not set")
                
        except Exception as e:
            print(f"Error updating status display: {e}")
            
    def refresh_logs(self):
        """Refresh bot logs"""
        try:
            if os.path.exists("polymarket_bot.log"):
                with open("polymarket_bot.log", "r") as f:
                    # Get last 100 lines
                    lines = f.readlines()
                    recent_lines = lines[-100:] if len(lines) > 100 else lines
                    
                    self.log_text.delete(1.0, tk.END)
                    self.log_text.insert(tk.END, "".join(recent_lines))
                    self.log_text.see(tk.END)
            else:
                self.log_text.delete(1.0, tk.END)
                self.log_text.insert(tk.END, "No log file found")
                
        except Exception as e:
            self.log_text.delete(1.0, tk.END)
            self.log_text.insert(tk.END, f"Error loading logs: {e}")
            
    def clear_logs(self):
        """Clear log display"""
        self.log_text.delete(1.0, tk.END)
        
    def refresh_trades(self):
        """Refresh trades display"""
        try:
            # Clear existing items
            for item in self.trades_tree.get_children():
                self.trades_tree.delete(item)
                
            # Load trades from database
            if os.path.exists("trades.db"):
                conn = sqlite3.connect("trades.db")
                cursor = conn.cursor()
                
                # Build date filter
                date_filter = self.date_filter_var.get()
                date_condition = ""
                params = []
                
                if date_filter == "today":
                    date_condition = "WHERE DATE(execution_timestamp) = DATE('now', 'localtime')"
                elif date_filter == "yesterday":
                    date_condition = "WHERE DATE(execution_timestamp) = DATE('now', 'localtime', '-1 day')"
                elif date_filter == "this week":
                    date_condition = "WHERE DATE(execution_timestamp) >= DATE('now', 'localtime', '-7 days')"
                elif date_filter == "this month":
                    date_condition = "WHERE DATE(execution_timestamp) >= DATE('now', 'localtime', 'start of month')"
                elif date_filter == "this year":
                    date_condition = "WHERE DATE(execution_timestamp) >= DATE('now', 'localtime', 'start of year')"
                elif date_filter == "custom":
                    if self.custom_start_var.get() and self.custom_end_var.get():
                        date_condition = "WHERE DATE(execution_timestamp) BETWEEN ? AND ?"
                        params = [self.custom_start_var.get(), self.custom_end_var.get()]
                
                # Add status filter
                status_filter = self.status_filter_var.get()
                if status_filter != "all":
                    if status_filter == "trades":
                        condition = "(original_trade_id NOT LIKE '%merge%' AND status NOT LIKE '%merge%')"
                    elif status_filter == "merges":
                        condition = "(original_trade_id LIKE '%merge%' OR status LIKE '%merge%')"
                    else:
                        condition = "status = ?"
                        params.append(status_filter)
                        
                    if date_condition:
                        date_condition += f" AND {condition}"
                    else:
                        date_condition = f"WHERE {condition}"
                
                # Get trades with filters
                query = f"""
                    SELECT execution_timestamp, original_trade_id, copy_size, 
                            copy_amount_usd, status,
                            CASE 
                                WHEN original_trade_id LIKE '%merge%' OR status LIKE '%merge%' THEN 1 
                                ELSE 0 
                            END as is_merge,
                            CASE 
                                WHEN original_trade_id LIKE '%redeem%' OR status LIKE '%redeem%' THEN 1 
                                ELSE 0 
                            END as is_redeem
                    FROM copy_trades
                    {date_condition}
                    ORDER BY execution_timestamp DESC
                    LIMIT 1000
                """
                cursor.execute(query, params)
                
                trades = cursor.fetchall()
                for trade in trades:
                    timestamp, trade_id, size, amount, status, is_merge, is_redeem = trade
                    
                    # Format timestamp
                    try:
                        dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                        dt = dt.astimezone()  # Convert to local timezone
                        time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                    except:
                        time_str = timestamp[:8] if timestamp else "Unknown"
                    
                    # Get original trade details
                    cursor.execute("""
                        SELECT tt.market_id, tt.side, tt.price, tt.amount_usd, tt.market_info
                        FROM target_trades tt
                        WHERE tt.trade_id = ?
                    """, (trade_id,))
                    
                    orig_trade = cursor.fetchone()
                    if orig_trade:
                        market_id, side, price, target_amount, market_info = orig_trade
                        
                        # Update market filters before checking
                        self.update_market_filters()
                        
                        # Set position and side display based on trade type
                        if is_merge:
                            position = "🔄 MERGE"
                            side_display = "🔄 MERGE"
                        elif is_redeem:
                            position = "💰 REDEEM"
                            side_display = "💰 REDEEM"
                        else:
                            # Determine YES/NO position based on price
                            position = "YES" if price > 0.5 else "NO"
                            # Format side with color indicators
                            side_display = "🟢 BUY" if side == "BUY" else "🔴 SELL"
                        
                    # Get market title from market_info
                    market_title = market_id
                    try:
                        if market_info:
                            import json
                            market_data = json.loads(market_info)
                            if 'title' in market_data:
                                market_title = market_data['title']
                            elif 'question' in market_data:
                                market_title = market_data['question']
                    except:
                        # Fallback to formatting market_id
                        try:
                            market_title = market_id.split("_")[-1].replace("-", " ")
                        except:
                            pass
                            
                    # Check if market matches any enabled filters
                    should_show = False
                    if not any(var.get() for var in self.market_filter_vars.values()):
                        should_show = True  # Show all if no filters enabled
                    else:
                        for pattern, var in self.market_filter_vars.items():
                            if var.get() and pattern.lower() in market_title.lower():
                                should_show = True
                                break
                    
                    if not should_show:
                        continue  # Skip this trade if it doesn't match filters
                                
                        # Check if market should be copied based on filters
                        should_copy = self.market_filter.should_copy_market(market_title)
                        if not should_copy:
                            status = "Filtered"  # Show that trade was skipped due to filter
                        
                            # Format amounts based on trade type
                            if is_merge or is_redeem:
                                amount_display = f"${amount:.2f}" if amount else "$0"  # For merges/redeems, show absolute amount only
                            else:
                                amount_display = f"${amount:.2f} ({(amount/target_amount*100):.1f}%)" if amount and target_amount else "$0"
                            
                            # Insert into tree
                            self.trades_tree.insert("", tk.END, values=(
                                time_str,
                                market_title,
                                side_display,
                                position,
                                f"${target_amount:.2f}" if target_amount else "$0",
                                amount_display,
                                "✅ " + status.title() if status.lower() == "executed" else 
                                "⛔ " + status.title() if status.lower() == "filtered" else 
                                "⏳ " + status.title()
                            ))
                    else:
                        # Fallback if original trade not found
                        self.trades_tree.insert("", tk.END, values=(
                            time_str,
                            "Unknown Market",
                            "🔄 MERGE" if is_merge else ("💰 REDEEM" if is_redeem else "Unknown"),
                            "🔄 MERGE" if is_merge else ("💰 REDEEM" if is_redeem else "Unknown"),
                            "$0",
                            "$0",
                            "✅ " + status.title() if status.lower() == "executed" else "⏳ " + status.title()
                        ))
                
                conn.close()
                
        except Exception as e:
            print(f"Error refreshing trades: {e}")
            
    def check_balance(self):
        """Check account balance"""
        try:
            result = subprocess.run(
                ["python", "check_balance.py"],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                # Parse balance from output
                lines = result.stdout.strip().split('\n')
                for line in lines:
                    if "USDC Balance" in line:
                        balance = line.split(":")[-1].strip()
                        self.usdc_balance_label.config(text=balance)
                        break
                else:
                    self.usdc_balance_label.config(text="Could not parse balance")
            else:
                self.usdc_balance_label.config(text="Error checking balance")
                
        except Exception as e:
            self.usdc_balance_label.config(text=f"Error: {e}")
            
    def open_dashboard(self):
        """Open web dashboard"""
        try:
            import webbrowser
            webbrowser.open("http://localhost:8080")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to open dashboard: {e}")
            
    def update_system_info(self):
        """Update system information"""
        try:
            info = []
            info.append(f"Python Version: {os.sys.version}")
            info.append(f"OS: {os.name}")
            info.append(f"Current Directory: {os.getcwd()}")
            info.append(f"Environment File: {'.env' if os.path.exists('.env') else 'Not found'}")
            info.append(f"Database File: {'trades.db' if os.path.exists('trades.db') else 'Not found'}")
            info.append(f"Log File: {'polymarket_bot.log' if os.path.exists('polymarket_bot.log') else 'Not found'}")
            
            self.system_info_text.delete(1.0, tk.END)
            self.system_info_text.insert(tk.END, "\n".join(info))
            
        except Exception as e:
            self.system_info_text.delete(1.0, tk.END)
            self.system_info_text.insert(tk.END, f"Error getting system info: {e}")

def main():
    """Main function"""
    root = tk.Tk()
    
    # Set style
    style = ttk.Style()
    style.theme_use('clam')
    
    app = PolymarketBotGUI(root)
    
    # Handle window closing
    def on_closing():
        if app.bot_running:
            if messagebox.askokcancel("Quit", "Bot is still running. Stop it before closing?"):
                app.stop_bot()
                root.after(1000, root.destroy)  # Give time for cleanup
            else:
                return
        else:
            root.destroy()
    
    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()

if __name__ == "__main__":
    main()
