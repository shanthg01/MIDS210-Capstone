# PortalPoint — Frontend

React 18 + TypeScript + Vite SPA. Proxies all `/api` requests to the FastAPI backend at `localhost:8000`.

## Prerequisites

- Node.js 18+
- npm 9+
- Backend running at http://localhost:8000 (see root README)

## Setup

```bash
cd frontend
npm install
npm run dev
```

App runs at http://localhost:5173. The Vite dev server proxies `/api/*` to `localhost:8000` — no CORS configuration needed.

## Available Scripts

```bash
npm run dev        # Development server with HMR (http://localhost:5173)
npm run build      # Production build → dist/
npm run preview    # Preview production build locally
npm run lint       # ESLint
npx tsc --noEmit   # TypeScript type check (no emit)
```

## Stack

| Library | Version | Purpose |
|---|---|---|
| React | 18 | UI framework |
| TypeScript | 5 | Type safety |
| Vite | 6 | Build tool + dev server |
| MUI (Material UI) | 5 | Component library — dark theme |
| React Router | 6 | Client-side routing |
| TanStack Query | 5 | Server state management + caching |
| Axios | 1 | HTTP client with JWT interceptor |

## Design System

Colors, typography, and spacing follow [`PORTALPOINT_DESIGN_PALETTE.md`](../PORTALPOINT_DESIGN_PALETTE.md) at the repo root.

Key tokens:
- Background: `#0D1B2A` (deep navy)
- Primary accent: `#FF6B35` (orange) — CTAs, active nav, section labels
- Secondary accent: `#4A90E2` (blue) — links, info states
- Body text: `#FFFFFF` primary, `#B0C4DE` secondary
- Font: Inter (300 / 400 / 600 / 700 / 900)

## Auth Flow

1. Signup or login → backend returns `{ access_token, user_id }`
2. Both stored in `localStorage` as `pp_token` and `pp_user_id`
3. Axios request interceptor attaches `Authorization: Bearer <token>` to every request
4. Axios response interceptor: on 401, clears localStorage and redirects to `/login`
5. `AuthContext` reads from localStorage on mount; `ProtectedRoute` redirects unauthenticated users

JWT tokens expire after `JWT_EXPIRY_SECONDS` (default 1 hour). Set `JWT_EXPIRY_SECONDS=86400` in the backend `.env` to reduce re-login friction during development.

## Structure

```
src/
├── api/
│   ├── client.ts          # Axios instance — baseURL /api, JWT interceptor, 401 redirect
│   ├── auth.ts            # login(), signup(), logout()
│   ├── players.ts         # searchPlayers(), getPlayer()
│   ├── users.ts           # getShortlist(), addToShortlist(), removeFromShortlist(), getPreferences(), updatePreferences()
│   ├── recommendations.ts # getRecommendations()
│   ├── fitScores.ts       # getFitScore(), getTeamRatingProjection()
│   └── compare.ts         # comparePlayers()
├── components/
│   ├── AppLayout.tsx      # Sidebar nav + AppBar with logo
│   ├── ProtectedRoute.tsx # Redirects to /login when unauthenticated
│   ├── FitScoreBar.tsx    # Labeled LinearProgress with color thresholds (exports scoreColor)
│   └── RecommendationCard.tsx  # Player recommendation card with fit bars + pipeline action
├── context/
│   └── AuthContext.tsx    # isAuthenticated, userId, setSession(), clearSession()
├── pages/
│   ├── LoginPage.tsx
│   ├── SignupPage.tsx
│   ├── DashboardPage.tsx       # /dashboard — recommendations grid
│   ├── PlayerSearchPage.tsx    # /players/search — debounced search
│   ├── PlayerProfilePage.tsx   # /players/:id — full stats + add to pipeline
│   ├── PipelinePage.tsx        # /pipeline — shortlisted players management
│   ├── FitScorePage.tsx        # /fit/:player_id — all 4 fit components + projection
│   ├── ComparePage.tsx         # /compare — side-by-side matrix for 2-4 players
│   ├── SettingsPage.tsx        # /settings — fit weight sliders + priority weights
│   └── PlaceholderPage.tsx     # Generic placeholder (unused routes)
├── types/
│   └── api.ts             # TypeScript interfaces mirroring all backend Pydantic schemas
└── App.tsx                # Theme, QueryClient, BrowserRouter, route tree
```

## Adding a New API Function

1. Add the TypeScript type to `src/types/api.ts` (mirror the backend Pydantic schema exactly)
2. Add the function to the appropriate file in `src/api/` using the `client` instance
3. Call with `useQuery` or `useMutation` from `@tanstack/react-query`

## Stub Data Note

Most API endpoints currently return deterministic stub data (seeded by `player_id` and `school_id`). The fit score and recommendation values displayed in the UI are realistic-looking but not based on real ML inference. See `STATUS.md` for which endpoints are wired to real DB data.
