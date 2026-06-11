# Code Explained — TfNSW Live Departures

A plain-English walkthrough of how the codebase works, file by file.

---

## `app.py` — The Backend

This is the entire Python backend. It uses **FastAPI**, a modern Python web framework that automatically handles routing, async requests, and JSON responses.

### How it starts
```python
load_dotenv()       # reads .env file into environment variables
app = FastAPI()     # creates the web app
```
At the bottom:
```python
app.mount("/", StaticFiles(directory="public", html=True))
```
This serves everything in the `public/` folder as static files — so when you visit `/landing.html` or `/index.html`, FastAPI just sends those HTML files directly. The API routes (`/api/...`) are defined before this line so they take priority.

---

### Helper Functions

**`hdrs()`**
Builds the auth header that every TfNSW API call needs:
```python
{"Authorization": "apikey YOUR_KEY", "Accept": "application/json"}
```

**`to_hhmm(iso)`**
TfNSW returns times like `"2026-06-05T14:32:00+10:00"`. This slices characters 11–16 to get just `"14:32"`.

**`mode_from_class(cls)`**
TfNSW uses numeric product class codes. This maps them:
- `1` → `"train"`, `2` → `"metro"`, `4` → `"lrt"`, `5` → `"bus"`, `9` → `"ferry"`

**`extract_line_code(name)`**
Uses a regex to pull the route number out of a string. For example `"T2 Inner West"` → `"T2"`, or `"632N - Chatswood"` → `"632N"`.

**`line_class(n)`**
Maps a line name to a CSS class used for colouring:
- `"T1"` through `"T9"` → `"t1"` through `"t9"` (each has its own official TfNSW colour)
- Anything starting with `M` → `"metro"`, `L` → `"lrt"`, `F` → `"ferry"`, digits → `"bus"`

**`haversine(lat1, lon1, lat2, lon2)`**
Calculates the straight-line distance in metres between two GPS coordinates using the Haversine formula (accounts for the Earth's curvature). Used to sort nearby stops by actual distance from the user.

---

### API Endpoints (How Each One Works)

#### `GET /api/config`
Returns the Supabase URL and anon key to the frontend. This way, credentials aren't hardcoded in the HTML — the browser fetches them at runtime.

---

#### `GET /api/stop?q=Central`
Calls TfNSW's **Stop Finder** API. Filters out non-stop results and global IDs (starting with `"G"`), then returns up to 8 results with id, name, and coordinates.

---

#### `GET /api/departures?stopId=10101100&limit=30`
Calls TfNSW's **Departure Monitor** API for a specific stop. For each departure event it:
1. Calculates `minsAway` — compares the departure timestamp to the current UTC time
2. Calculates `delay` — difference between planned and estimated departure in minutes
3. Extracts the short line code using `extract_line_code()`
4. Reads platform name from the location properties
5. Returns everything the frontend needs to paint a departure row

---

#### `GET /api/nearby?lat=-33.86&lon=151.20&radius=2000`
Also calls the Departure Monitor, but with a coordinate instead of a stop ID. It loops through all returned stop events, deduplicates by stop ID using a `seen` dictionary, and calculates each stop's distance from the user using `haversine()`. Returns up to 12 stops sorted by distance.

---

#### `GET /api/trip?fromId=X&toId=Y`
Calls TfNSW's **Trip Planner** API. For each journey it:
1. Loops through every leg (a leg = one unbroken segment of the trip)
2. Checks `isFootpathLeg` to identify walking segments
3. Extracts from/to stop names and their coordinates (`coord` field)
4. Returns those coordinates so the frontend can draw polylines on the map
5. Sorts journeys by total duration so the fastest always comes first

---

#### `GET /api/vehicles?mode=bus`
This one is different — TfNSW's vehicle position feed uses **Protocol Buffers (protobuf)**, a binary format (not JSON). The steps are:
1. Fetch the raw binary data with `Accept: application/x-google-protobuf`
2. Parse it using `gtfs_realtime_pb2.FeedMessage()` from the `gtfs-realtime-bindings` library
3. Loop through each entity, skip anything without a `vehicle` field or valid coordinates
4. Extract route ID (e.g. `"2508_632n"`), split on `_` and take the last part (`"632N"`) as the human-readable route number
5. Convert speed from m/s to km/h
6. Return a clean JSON array the frontend can use to place map markers

---

## `public/landing.html` — The Sign In Page

A standalone HTML page with no dependencies except the Supabase JS client loaded from CDN.

**How auth works:**
1. On load, it fetches `/api/config` to get the Supabase credentials
2. Creates a Supabase client: `createClient(url, anonKey)`
3. Checks `supabase.auth.getSession()` — if a session already exists, skips straight to the main app
4. Sign In form calls `supabase.auth.signInWithPassword({email, password})`
5. Sign Up form calls `supabase.auth.signUp({email, password, options: {data: {full_name}}})` — Supabase sends the confirmation email automatically
6. On success, `window.location.href = '/index.html'` redirects to the main app

---

## `public/index.html` — The Main App

Everything — HTML structure, CSS, and JavaScript — lives in this single file. The script tag uses `type="module"` which enables `await` at the top level (needed for the Supabase import).

### Startup sequence
When the page loads, the script runs top to bottom:
1. **Import Supabase** — fetches the JS library from CDN using a dynamic `import()`
2. **Fetch config** — calls `/api/config` to get credentials
3. **Auth guard** — calls `supabase.auth.getSession()`. If no session → redirect to `/landing.html` immediately
4. **Set up user menu** — fills in the user's name and avatar initials from the session data
5. **Init Leaflet map** — creates the map, adds OSM tiles
6. **Start GPS** — calls `startWatchingLocation()`, which tries to get GPS position
7. **Load nearby stops** — calls `/api/nearby` with the GPS coordinates (or Sydney CBD fallback)
8. **Load departures** — loads the first stop's departure board automatically
9. **Load favourites** — fetches from Supabase and renders the favourites panel
10. **Start vehicle tracking** — calls `/api/vehicles?mode=bus` and sets a 20s refresh interval
11. **Wire all event listeners** — tabs, filters, search, trip planner, star buttons, etc.

---

### State Management
There is no framework — state is just JavaScript variables at the top of the script:

```javascript
let currentStopId      // which stop's departures are showing
let allDepartures      // the full unfiltered departure list
let activeMode         // current mode filter ('all', 'train', 'bus', etc.)
let activeLineFilter   // current chip filter ('all', 'T1', 'ontime', etc.)
let nearbyStops        // cached so search results don't erase them permanently
let departureTimer     // the setInterval handle — cleared and restarted on stop change
let favStops / favRoutes  // arrays from Supabase
let vehicleMarkers     // {vehicleId: LeafletMarker} — updated in-place every 20s
let routeLines         // Leaflet polylines for the current trip route
```

---

### How Filtering Works
There are two independent filters that both apply at once:

1. **Mode buttons** (Train / Bus / Ferry / LRT) — sets `activeMode`
2. **Line chips** (T1, T2, 144X, On time, Delayed, Cancelled) — sets `activeLineFilter`

`applyFilters()` runs whenever either changes:
```
allDepartures
  → filter by activeMode (if not 'all')
  → filter by activeLineFilter (if not 'all')
  → paintBoard(result)
```

The line chips are rebuilt dynamically from the actual departure data every time a new stop loads — so you only ever see chips for lines that actually have departures at that stop.

---

### How the Map Works
**Stop pins** — When nearby stops load, `updateMapStops()` creates a `L.divIcon` (a custom HTML div styled as a coloured circle) for each stop and adds it as a `L.marker`. Old markers are removed first.

**Vehicle pins** — `loadVehicles()` fetches `/api/vehicles`, then loops through the results. If a marker for that vehicle ID already exists, it calls `setLatLng()` to move it smoothly. If it's new, it creates a marker. Vehicles that disappeared from the feed get removed. This means markers move rather than flicker.

**Route polylines** — `drawRouteOnMap(journey)` loops through each leg and draws a `L.polyline` between the from and to coordinates returned by the trip API. Walk legs get a dashed grey line, transit legs get a solid coloured line. `map.fitBounds()` then zooms the map to fit the entire route with padding for the floating panel.

---

### How Favourites Work
When you click a star button on a stop:
1. `toggleFavStop()` checks if that `stop_id` already exists in `favStops`
2. If yes → `supabase.from('favourite_stops').delete()` removes it
3. If no → `supabase.from('favourite_stops').insert()` adds it
4. Either way, `loadFavourites()` is called to re-fetch and re-render the panel

The same pattern applies to routes via `addFavRoute()`.

Row Level Security on Supabase means the `.delete()` and `.insert()` calls automatically scope to the logged-in user — no user ID needs to be passed manually (the JWT in the session handles it).

---

### The Trip Planner Flow
1. User types in From/To → autocomplete calls `/api/stop` with a debounced 300ms delay
2. User clicks "Plan Trip" → `planTrip()` runs
3. Fetches `/api/trip` with the selected stop IDs
4. On success: opens fullscreen map, pre-fills the floating panel, calls `renderFloatJourneys()`
5. The fastest journey auto-expands to show `buildLegSteps()` — a step-by-step breakdown
6. `drawRouteOnMap()` draws the fastest route immediately
7. Clicking another journey card collapses the current one, expands the clicked one, and redraws the route

---

## `setup.bat` — Local Setup Script
A Windows batch file that automates local setup. It checks for Python, installs pip dependencies from `requirements.txt`, creates a `.env` template if one doesn't exist, and starts the server. Designed so anyone can clone the repo and be running in under 2 minutes.

---

## `Procfile` — Railway Deployment
```
web: uvicorn app:app --host 0.0.0.0 --port $PORT
```
Railway reads this file to know how to start the app. `$PORT` is provided by Railway automatically. `--host 0.0.0.0` makes the server accessible from outside the container (required for any cloud deployment).
