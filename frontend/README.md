# TICKET - Copy Trading Platform Frontend

Beautiful, modern Next.js frontend for the Polymarket copy trading platform.

## 🚀 Quick Start

```bash
# Install dependencies
npm install

# Run development server
npm run dev

# Build for production
npm run build

# Start production server
npm start
```

Open [http://localhost:3000](http://localhost:3000) to see the app.

## 📁 Project Structure

```
frontend/
├── app/
│   ├── page.tsx                    # Landing page
│   ├── login/page.tsx              # Login page
│   ├── dashboard/
│   │   ├── page.tsx                # User dashboard
│   │   └── bots/
│   │       └── new/page.tsx        # Create bot page
│   ├── layout.tsx                  # Root layout
│   └── globals.css                 # Global styles
├── lib/
│   └── mock-data.ts                # Mock data for development
└── public/                         # Static assets
```

## 🎨 Features

### Landing Page
- ✅ Hero section with CTA
- ✅ Features showcase
- ✅ How it works section
- ✅ Pricing information
- ✅ Responsive design

### User Dashboard
- ✅ Portfolio overview
- ✅ Bot management
- ✅ Recent trades
- ✅ Open positions
- ✅ Real-time stats (mock data)

### Bot Creation
- ✅ Step-by-step form
- ✅ Target trader selection
- ✅ Copy percentage slider
- ✅ Risk settings
- ✅ Private key input with instructions

## 🎯 Current Status

**✅ COMPLETED:**
- Landing page with beautiful design
- User dashboard with mock data
- Bot creation flow
- Login page
- Responsive layout
- Dark theme

**🚧 TODO (Connect to Backend):**
- User authentication (JWT)
- Real API integration
- Wallet connection (Phantom)
- Real-time data updates
- Bot CRUD operations

## 🔌 Connecting to Backend

When backend is ready, update these files:

1. **Create API client** (`lib/api.ts`):
```typescript
const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export async function createBot(botData) {
  const response = await fetch(`${API_URL}/api/bots`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(botData)
  });
  return response.json();
}
```

2. **Replace mock data** with real API calls
3. **Add authentication** with NextAuth.js
4. **Add Phantom wallet** integration

## 🎨 Design System

**Colors:**
- Primary: Blue (#3B82F6)
- Secondary: Purple (#9333EA)
- Background: Dark gradient (slate-900 → purple-900)
- Text: White with opacity variants

**Typography:**
- Font: System fonts (sans-serif)
- Headings: Bold, large sizes
- Body: Regular weight

## 📱 Responsive Breakpoints

- Mobile: < 768px
- Tablet: 768px - 1024px
- Desktop: > 1024px

## 🚀 Deployment

### Vercel (Recommended)

```bash
# Install Vercel CLI
npm i -g vercel

# Deploy
vercel
```

### Environment Variables

Create `.env.local`:

```
NEXT_PUBLIC_API_URL=https://your-api.com
NEXT_PUBLIC_PHANTOM_NETWORK=mainnet-beta
```

## 📝 Notes

- Currently using **mock data** for development
- All forms are functional but don't persist data yet
- Ready to connect to backend API
- Optimized for performance and SEO

## 🎯 Next Steps

1. Connect to Management API
2. Add user authentication
3. Integrate Phantom wallet
4. Add real-time updates
5. Deploy to Vercel

---

Built with ❤️ using Next.js 14, TypeScript, and Tailwind CSS
