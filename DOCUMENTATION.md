# TfNSW Live Departures — Project Documentation
**Project Management INF304 | Academic Project**
> Not for commercial use. Transport data © Transport for NSW. Built with AI integrations.

---

## Overview

A real-time public transport web application for the Sydney, NSW network. Users can track live departures, plan trips, view nearby stops on a map, monitor live vehicle positions, and save favourite stops and routes — all backed by the official Transport for NSW API.

**Live URL:** https://transportsystemnswau-production.up.railway.app

---

## Features

### 1. Live Departure Board
- Displays real-time departures for any selected stop
- Shows line number, destination, departure time, countdown, platform, and delay
- Groups departures by mode (Train, Metro, Bus, Ferry, Light Rail)
- Auto-refreshes every **30 seconds**
- Filter by line (T1, T2, 144X, etc.), status (On time / Delayed / Cancelled)

### 2. Nearby Stops
- Loads stops within 2km of the user's location on startup
- Falls back to Sydney CBD if location is denied
- Stop list shows mode icon, line codes, and distance
- Clicking a stop loads its departure board instantly
- Search bar filters stops by name in real time

### 3. Interactive Map (Leaflet + OpenStreetMap)
- Shows user's live GPS location as a pulsing blue pin
- Continuously updates position via `watchPosition`
- Nearby stop pins colour-coded by transport mode
- **Live vehicle tracking** — 1,600+ buses, trains, ferries moving in real time (updates every 20s)
- Click any vehicle pin to see route number and vehicle label
- **Fullscreen mode** — expand the map to full screen with one click

### 4. Trip Planner
- Search any two stops with autocomplete
- Returns up to 5 journey options sorted by duration
- Clicking "Plan Trip" opens fullscreen map and draws the route
- Each journey card shows: departure → arrival, total duration, number of changes
- **Expandable leg detail** — click a journey to see:
  - Which line to board and at which stop
  - Departure and arrival time per leg
  - Walking segments with distance and duration
- Route drawn as coloured polylines on the map per mode

### 5. Fullscreen Map with Floating Panel
- Fullscreen button (⛶) expands map to full viewport
- Google Maps–style floating panel appears on the left
- Supports its own From / To search with autocomplete
- Journey results shown in the panel, route drawn on map
- Exit with ✕ button or Escape key

### 6. User Authentication
- Sign up / Sign in via the landing page (`/landing.html`)
- Email + password auth powered by **Supabase Auth**
- Email confirmation required on sign up
- Session persists across page reloads (JWT-based)
- Sign out from the user menu in the top bar
- Protected — main app redirects to landing if not logged in

### 7. Favourite Stops & Routes
- Star (☆) button on every stop in the left panel
- Star button on the fastest trip result
- Favourites saved to Supabase database (per user)
- Favourites panel in the left sidebar — collapsible
- Click a favourite stop → loads its departure board
- Click a favourite route → opens trip planner pre-filled and plans immediately
- Remove favourites with the ✕ button

### 8. Live Stats & Alerts
- Right panel shows: On-time %, Delayed count, Cancelled count
- Computed live from actual departure data — no fake numbers
- Service Alerts section lists real cancelled/delayed services at the selected stop
- Alert count badge updates with each refresh

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3, FastAPI |
| Frontend | Vanilla JavaScript (ES Modules), HTML, CSS |
| Map | Leaflet.js v1.9.4 + OpenStreetMap tiles |
| Database | Supabase (PostgreSQL) |
| Auth | Supabase Auth (JWT) |
| Transport Data | Transport for NSW Open API |
| Vehicle Tracking | TfNSW GTFS Realtime (protobuf) |
| Deployment | Railway |

---

## Database Schema

### Supabase Project
- **Project ID:** `zpgrbvgawmkrdmaxanbv`
- **Region:** ap-northeast-1 (Tokyo)
- **Auth:** Built-in Supabase Auth (`auth.users` table — managed automatically)

---

### Table: `favourite_stops`
Stores stops that a user has starred.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` | Primary key, auto-generated |
| `user_id` | `uuid` | Foreign key → `auth.users(id)`, cascades on delete |
| `stop_id` | `text` | TfNSW stop ID (e.g. `"10101100"`) |
| `stop_name` | `text` | Display name (e.g. `"Central Station"`) |
| `stop_mode` | `text` | `"train"`, `"bus"`, `"ferry"`, `"lrt"`, `"metro"` |
| `created_at` | `timestamptz` | Auto-set on insert |

**Constraints:** `UNIQUE(user_id, stop_id)` — a user can't star the same stop twice.
**RLS Policy:** Users can only read, insert, update, delete their own rows (`auth.uid() = user_id`).

---

### Table: `favourite_routes`
Stores trip planner routes that a user has starred.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` | Primary key, auto-generated |
| `user_id` | `uuid` | Foreign key → `auth.users(id)`, cascades on delete |
| `label` | `text` | Auto-generated label e.g. `"Burwood Station → Manly Wharf"` |
| `from_stop_id` | `text` | TfNSW stop ID for origin |
| `from_stop_name` | `text` | Display name for origin |
| `to_stop_id` | `text` | TfNSW stop ID for destination |
| `to_stop_name` | `text` | Display name for destination |
| `created_at` | `timestamptz` | Auto-set on insert |

**RLS Policy:** Users can only read, insert, update, delete their own rows (`auth.uid() = user_id`).

---

## Backend API Reference

Base URL (local): `http://localhost:3400`
Base URL (production): `https://transportsystemnswau-production.up.railway.app`

| Method | Endpoint | Parameters | Description |
|---|---|---|---|
| GET | `/api/config` | — | Returns Supabase URL + anon key for frontend auth init |
| GET | `/api/health` | — | Checks API key status and TfNSW connectivity |
| GET | `/api/stop` | `q` (string) | Search stops by name — returns up to 8 matches with coordinates |
| GET | `/api/departures` | `stopId`, `limit` (max 50) | Live departures for a stop — line, mode, time, delay, platform, occupancy |
| GET | `/api/nearby` | `lat`, `lon`, `radius` (max 5000m) | Stops within radius — sorted by distance, with mode and lines |
| GET | `/api/trip` | `fromId` or `fromLat`+`fromLon`, `toId` | Journey planning — returns up to 5 options with leg coordinates |
| GET | `/api/vehicles` | `mode` (`bus`/`train`/`ferry`/`metro`/`lrt`) | Live vehicle positions from GTFS Realtime feed |

---

## Environment Variables

| Variable | Purpose |
|---|---|
| `TFNSW_API_KEY` | Transport for NSW API key — all transport data |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_ANON_KEY` | Supabase public anon key (safe for browser) |
| `PORT` | Set automatically by Railway |

---

## External Services

| Service | Used For | Key Required |
|---|---|---|
| Transport for NSW API | Stops, departures, trips | Yes — `TFNSW_API_KEY` |
| TfNSW GTFS Realtime | Live vehicle positions | Same key |
| Supabase | Database + Auth | Anon key (frontend), project credentials |
| OpenStreetMap | Map tiles | No |
| Leaflet.js | Map rendering library | No |

---

## Deployment

The app is deployed on **Railway** and auto-deploys on every push to the `main` branch of the GitHub repository.

**Repository:** https://github.com/bishalkha12121/Transport_System_NSWAU

**To run locally:**
1. Install Python 3.10+
2. Clone the repository
3. Double-click `setup.bat` (Windows)
4. Fill in `TFNSW_API_KEY` in the generated `.env` file
5. Access at `http://localhost:3400`
