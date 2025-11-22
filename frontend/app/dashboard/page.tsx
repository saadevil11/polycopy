'use client';

import Link from 'next/link';
import { mockBots, mockUserStats, mockTrades, mockPositions } from '@/lib/mock-data';
import { useState } from 'react';

export default function Dashboard() {
  const [selectedBot, setSelectedBot] = useState<string | null>(null);

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-purple-900 to-slate-900">
      {/* Navigation */}
      <nav className="border-b border-white/10 backdrop-blur-sm">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex justify-between items-center h-16">
            <div className="flex items-center gap-8">
              <Link href="/" className="flex items-center gap-2">
                <div className="w-8 h-8 bg-gradient-to-br from-blue-500 to-purple-600 rounded-lg"></div>
                <span className="text-xl font-bold text-white">TICKET</span>
              </Link>
              <div className="hidden md:flex items-center gap-6">
                <Link href="/dashboard" className="text-white font-medium">
                  Dashboard
                </Link>
                <Link href="/dashboard/bots" className="text-white/60 hover:text-white transition">
                  My Bots
                </Link>
                <Link href="/dashboard/wallet" className="text-white/60 hover:text-white transition">
                  Wallet
                </Link>
              </div>
            </div>
            <div className="flex items-center gap-4">
              <div className="text-right">
                <div className="text-sm text-white/60">Total Balance</div>
                <div className="text-lg font-bold text-white">${mockUserStats.totalBalance.toFixed(2)}</div>
              </div>
              <div className="w-10 h-10 bg-blue-600 rounded-full flex items-center justify-center text-white font-semibold">
                U
              </div>
            </div>
          </div>
        </div>
      </nav>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* Welcome */}
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-white mb-2">Welcome back! 👋</h1>
          <p className="text-white/60">Here's what's happening with your bots today.</p>
        </div>

        {/* Stats Grid */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
          <div className="bg-white/5 backdrop-blur-sm border border-white/10 rounded-xl p-6">
            <div className="text-sm text-white/60 mb-1">Total Balance</div>
            <div className="text-2xl font-bold text-white">${mockUserStats.totalBalance.toFixed(2)}</div>
          </div>
          <div className="bg-white/5 backdrop-blur-sm border border-white/10 rounded-xl p-6">
            <div className="text-sm text-white/60 mb-1">Daily P&L</div>
            <div className={`text-2xl font-bold ${mockUserStats.dailyPnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {mockUserStats.dailyPnl >= 0 ? '+' : ''}${mockUserStats.dailyPnl.toFixed(2)}
            </div>
          </div>
          <div className="bg-white/5 backdrop-blur-sm border border-white/10 rounded-xl p-6">
            <div className="text-sm text-white/60 mb-1">All-Time P&L</div>
            <div className={`text-2xl font-bold ${mockUserStats.allTimePnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {mockUserStats.allTimePnl >= 0 ? '+' : ''}${mockUserStats.allTimePnl.toFixed(2)}
            </div>
          </div>
          <div className="bg-white/5 backdrop-blur-sm border border-white/10 rounded-xl p-6">
            <div className="text-sm text-white/60 mb-1">Active Bots</div>
            <div className="text-2xl font-bold text-white">{mockUserStats.activeBots}/{mockUserStats.totalBots}</div>
          </div>
        </div>

        {/* My Bots */}
        <div className="mb-8">
          <div className="flex justify-between items-center mb-6">
            <h2 className="text-2xl font-bold text-white">My Bots</h2>
            <Link
              href="/dashboard/bots/new"
              className="bg-blue-600 hover:bg-blue-700 text-white px-6 py-2 rounded-lg font-semibold transition flex items-center gap-2"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
              Create Bot
            </Link>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {mockBots.map((bot) => (
              <div
                key={bot.id}
                className="bg-white/5 backdrop-blur-sm border border-white/10 rounded-xl p-6 hover:border-white/20 transition cursor-pointer"
                onClick={() => setSelectedBot(bot.id)}
              >
                <div className="flex justify-between items-start mb-4">
                  <div>
                    <h3 className="text-lg font-bold text-white mb-1">{bot.name}</h3>
                    <div className="text-sm text-white/60">Copy: {bot.copyPercentage}%</div>
                  </div>
                  <div className={`px-3 py-1 rounded-full text-xs font-semibold ${
                    bot.status === 'running' 
                      ? 'bg-green-500/20 text-green-400' 
                      : 'bg-yellow-500/20 text-yellow-400'
                  }`}>
                    {bot.status === 'running' ? '● Running' : '⏸ Paused'}
                  </div>
                </div>

                <div className="space-y-3 mb-4">
                  <div className="flex justify-between">
                    <span className="text-white/60 text-sm">Balance</span>
                    <span className="text-white font-semibold">${bot.balance.toFixed(2)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-white/60 text-sm">Daily P&L</span>
                    <span className={`font-semibold ${bot.dailyPnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {bot.dailyPnl >= 0 ? '+' : ''}${bot.dailyPnl.toFixed(2)}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-white/60 text-sm">Success Rate</span>
                    <span className="text-white font-semibold">{bot.successRate}%</span>
                  </div>
                </div>

                <div className="pt-4 border-t border-white/10">
                  <div className="text-xs text-white/40 mb-2">Target Trader</div>
                  <div className="text-sm text-white/80 font-mono">{bot.targetTraderShort}</div>
                </div>

                <div className="flex gap-2 mt-4">
                  <button className="flex-1 bg-white/10 hover:bg-white/20 text-white px-4 py-2 rounded-lg text-sm font-semibold transition">
                    Edit
                  </button>
                  <button className="flex-1 bg-white/10 hover:bg-white/20 text-white px-4 py-2 rounded-lg text-sm font-semibold transition">
                    {bot.status === 'running' ? 'Pause' : 'Resume'}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Recent Activity */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Recent Trades */}
          <div className="bg-white/5 backdrop-blur-sm border border-white/10 rounded-xl p-6">
            <h3 className="text-xl font-bold text-white mb-4">Recent Trades</h3>
            <div className="space-y-3">
              {mockTrades.slice(0, 5).map((trade) => (
                <div key={trade.id} className="flex justify-between items-start p-3 bg-white/5 rounded-lg">
                  <div className="flex-1">
                    <div className="text-white font-medium text-sm mb-1">{trade.market}</div>
                    <div className="text-white/60 text-xs">{trade.botName}</div>
                  </div>
                  <div className="text-right">
                    <div className={`text-sm font-semibold ${trade.side === 'BUY' ? 'text-green-400' : 'text-red-400'}`}>
                      {trade.side}
                    </div>
                    <div className="text-white/60 text-xs">${trade.amount.toFixed(2)}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Open Positions */}
          <div className="bg-white/5 backdrop-blur-sm border border-white/10 rounded-xl p-6">
            <h3 className="text-xl font-bold text-white mb-4">Open Positions</h3>
            <div className="space-y-3">
              {mockPositions.map((position) => (
                <div key={position.id} className="flex justify-between items-start p-3 bg-white/5 rounded-lg">
                  <div className="flex-1">
                    <div className="text-white font-medium text-sm mb-1">{position.market}</div>
                    <div className="text-white/60 text-xs">{position.botName} • {position.side}</div>
                  </div>
                  <div className="text-right">
                    <div className={`text-sm font-semibold ${position.unrealizedPnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {position.unrealizedPnl >= 0 ? '+' : ''}${position.unrealizedPnl.toFixed(2)}
                    </div>
                    <div className="text-white/60 text-xs">{position.size} shares</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

