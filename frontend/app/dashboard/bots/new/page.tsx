'use client';

import Link from 'next/link';
import { useState } from 'react';
import { useRouter } from 'next/navigation';

export default function CreateBot() {
  const router = useRouter();
  const [formData, setFormData] = useState({
    name: '',
    targetTrader: '',
    copyPercentage: 10,
    maxPositionSize: 100,
    maxDailyLoss: 500,
    polymarketPrivateKey: ''
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    // TODO: Connect to backend API
    console.log('Creating bot:', formData);
    alert('Bot created successfully! (Demo mode)');
    router.push('/dashboard');
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-purple-900 to-slate-900">
      {/* Navigation */}
      <nav className="border-b border-white/10 backdrop-blur-sm">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex justify-between items-center h-16">
            <Link href="/dashboard" className="flex items-center gap-2">
              <div className="w-8 h-8 bg-gradient-to-br from-blue-500 to-purple-600 rounded-lg"></div>
              <span className="text-xl font-bold text-white">TICKET</span>
            </Link>
            <Link
              href="/dashboard"
              className="text-white/60 hover:text-white transition"
            >
              ← Back to Dashboard
            </Link>
          </div>
        </div>
      </nav>

      <div className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-white mb-2">Create New Bot</h1>
          <p className="text-white/60">Set up your copy trading bot in a few simple steps</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-6">
          {/* Bot Name */}
          <div className="bg-white/5 backdrop-blur-sm border border-white/10 rounded-xl p-6">
            <label className="block text-sm font-medium text-white mb-2">
              Bot Name
            </label>
            <input
              type="text"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              className="w-full px-4 py-3 bg-white/5 border border-white/10 rounded-lg text-white placeholder-white/40 focus:outline-none focus:border-blue-500"
              placeholder="My BTC Bot"
              required
            />
          </div>

          {/* Target Trader */}
          <div className="bg-white/5 backdrop-blur-sm border border-white/10 rounded-xl p-6">
            <label className="block text-sm font-medium text-white mb-2">
              Target Trader Address
            </label>
            <input
              type="text"
              value={formData.targetTrader}
              onChange={(e) => setFormData({ ...formData, targetTrader: e.target.value })}
              className="w-full px-4 py-3 bg-white/5 border border-white/10 rounded-lg text-white placeholder-white/40 focus:outline-none focus:border-blue-500 font-mono text-sm"
              placeholder="0x7485d66..."
              required
            />
            <p className="text-sm text-white/40 mt-2">
              Find top traders on the Polymarket leaderboard
            </p>
          </div>

          {/* Copy Percentage */}
          <div className="bg-white/5 backdrop-blur-sm border border-white/10 rounded-xl p-6">
            <label className="block text-sm font-medium text-white mb-2">
              Copy Percentage: {formData.copyPercentage}%
            </label>
            <input
              type="range"
              min="1"
              max="100"
              value={formData.copyPercentage}
              onChange={(e) => setFormData({ ...formData, copyPercentage: parseInt(e.target.value) })}
              className="w-full h-2 bg-white/10 rounded-lg appearance-none cursor-pointer"
            />
            <div className="flex justify-between text-sm text-white/40 mt-2">
              <span>1%</span>
              <span>50%</span>
              <span>100%</span>
            </div>
            <div className="mt-4 p-3 bg-blue-500/10 border border-blue-500/20 rounded-lg">
              <p className="text-sm text-blue-300">
                💡 Platform fee: 0.5% per trade (automatically deducted)
              </p>
            </div>
          </div>

          {/* Risk Settings */}
          <div className="bg-white/5 backdrop-blur-sm border border-white/10 rounded-xl p-6">
            <h3 className="text-lg font-semibold text-white mb-4">Risk Settings</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-white mb-2">
                  Max Position Size
                </label>
                <div className="relative">
                  <span className="absolute left-3 top-3 text-white/40">$</span>
                  <input
                    type="number"
                    value={formData.maxPositionSize}
                    onChange={(e) => setFormData({ ...formData, maxPositionSize: parseFloat(e.target.value) })}
                    className="w-full pl-8 pr-4 py-3 bg-white/5 border border-white/10 rounded-lg text-white focus:outline-none focus:border-blue-500"
                    required
                  />
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-white mb-2">
                  Max Daily Loss
                </label>
                <div className="relative">
                  <span className="absolute left-3 top-3 text-white/40">$</span>
                  <input
                    type="number"
                    value={formData.maxDailyLoss}
                    onChange={(e) => setFormData({ ...formData, maxDailyLoss: parseFloat(e.target.value) })}
                    className="w-full pl-8 pr-4 py-3 bg-white/5 border border-white/10 rounded-lg text-white focus:outline-none focus:border-blue-500"
                    required
                  />
                </div>
              </div>
            </div>
          </div>

          {/* Polymarket Private Key */}
          <div className="bg-yellow-500/10 border-2 border-yellow-500/30 rounded-xl p-6">
            <label className="block text-sm font-medium text-white mb-2">
              Polymarket Proxy Wallet Private Key
            </label>
            <input
              type="password"
              value={formData.polymarketPrivateKey}
              onChange={(e) => setFormData({ ...formData, polymarketPrivateKey: e.target.value })}
              className="w-full px-4 py-3 bg-white/5 border border-white/10 rounded-lg text-white placeholder-white/40 focus:outline-none focus:border-yellow-500 font-mono text-sm"
              placeholder="0x..."
              required
            />
            
            <div className="mt-4 space-y-3 text-sm text-white/80">
              <p className="font-semibold text-white">📋 How to get your private key:</p>
              <ol className="list-decimal list-inside space-y-1 ml-2">
                <li>Go to <a href="https://polymarket.com" target="_blank" rel="noopener noreferrer" className="text-blue-400 underline">Polymarket.com</a></li>
                <li>Click your profile → Settings</li>
                <li>Click "Export Private Key"</li>
                <li>Copy the key and paste it here</li>
              </ol>
              
              <div className="mt-3 p-3 bg-white/5 border border-yellow-500/30 rounded-lg">
                <p className="font-semibold text-yellow-300 mb-2">⚠️ Security Notes:</p>
                <ul className="list-disc list-inside space-y-1 text-white/70">
                  <li>This is your Polymarket proxy wallet (not Phantom)</li>
                  <li>Your key is encrypted with AES-256</li>
                  <li>We never share or expose your key</li>
                  <li>Only use a wallet dedicated for copy trading</li>
                  <li>You can withdraw funds anytime from Polymarket</li>
                </ul>
              </div>
            </div>
          </div>

          {/* Submit */}
          <div className="flex gap-4">
            <Link
              href="/dashboard"
              className="flex-1 border border-white/20 hover:border-white/40 text-white text-center px-8 py-4 rounded-lg font-semibold transition"
            >
              Cancel
            </Link>
            <button
              type="submit"
              className="flex-1 bg-blue-600 hover:bg-blue-700 text-white px-8 py-4 rounded-lg font-semibold transition"
            >
              Create Bot & Start Trading
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

