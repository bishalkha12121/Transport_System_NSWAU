require('dotenv').config();
const express = require('express');
const cors    = require('cors');
const path    = require('path');

// Use native fetch (Node 18+); fall back to node-fetch for older local envs
const fetch = globalThis.fetch ?? require('node-fetch');

const app      = express();
const PORT     = process.env.PORT || 3400;
const API_KEY  = process.env.TFNSW_API_KEY;
const TFNSW    = 'https://api.transport.nsw.gov.au/v1/tp';

app.use(cors());
app.use(express.static(path.join(__dirname, 'public')));

const hdrs = () => ({ Authorization: `apikey ${API_KEY}`, Accept: 'application/json' });

/* ── /api/health  ────────────────────────────────────────────────────────── */
app.get('/api/health', async (req, res) => {
  const keySet = !!API_KEY;
  const keyPreview = API_KEY ? `${API_KEY.slice(0, 12)}…` : 'NOT SET';
  let tfnswStatus = null, tfnswBody = '';
  try {
    const r = await fetch(
      `${TFNSW}/departure_mon?outputFormat=rapidJSON&type_dm=stop&name_dm=10101100&TfNSWDM=true&version=10.2.1.42`,
      { headers: hdrs() }
    );
    tfnswStatus = r.status;
    tfnswBody   = (await r.text()).slice(0, 500);
  } catch (e) {
    tfnswBody = e.message;
  }
  res.json({ keySet, keyPreview, tfnswStatus, tfnswBody });
});

/* ── /api/stop?q=<name>  ─────────────────────────────────────────────────── */
app.get('/api/stop', async (req, res) => {
  const q = req.query.q || 'Central Station';
  const p = new URLSearchParams({
    outputFormat: 'rapidJSON', coordOutputFormat: 'EPSG:4326',
    type_sf: 'any', name_sf: q, TfNSWSF: 'true', version: '10.2.1.42',
  });
  try {
    const r = await fetch(`${TFNSW}/stop_finder?${p}`, { headers: hdrs() });
    if (!r.ok) return res.status(r.status).json({ error: `TfNSW ${r.status}` });
    const raw = await r.json();
    const locs = (raw.locations || [])
      .filter(l => l.type === 'stop')          // stops only
      .filter(l => l.id && !l.id.startsWith('G')) // NSW stops only
      .slice(0, 8)
      .map(l => ({
        id:   l.id,
        name: l.disassembledName || l.name,
        type: l.type,
        lat:  l.coord?.[0],   // TfNSW returns [lat, lon]
        lon:  l.coord?.[1],
        modes: (l.assignedStops || []).flatMap(s => s.modes || []),
      }));
    res.json({ locations: locs });
  } catch (e) {
    console.error('stop finder:', e.message);
    res.status(500).json({ error: 'Stop finder failed' });
  }
});

/* ── /api/departures?stopId=<id>&limit=30  ───────────────────────────────── */
app.get('/api/departures', async (req, res) => {
  const stopId = req.query.stopId || '10101100'; // Central Station
  const limit  = Math.min(parseInt(req.query.limit) || 30, 50);
  const p = new URLSearchParams({
    outputFormat: 'rapidJSON', coordOutputFormat: 'EPSG:4326',
    mode: 'direct', type_dm: 'stop', name_dm: stopId,
    departureMonitorMacro: 'true', TfNSWDM: 'true', version: '10.2.1.42',
  });
  try {
    const r = await fetch(`${TFNSW}/departure_mon?${p}`, { headers: hdrs() });
    if (!r.ok) {
      const body = await r.text().catch(() => '');
      console.error(`departures TfNSW ${r.status}:`, body.slice(0, 300));
      return res.status(r.status).json({ error: `TfNSW ${r.status}` });
    }
    const raw    = await r.json();
    const events = (raw.stopEvents || []).slice(0, limit);
    const now    = Date.now();

    const departures = events.map(e => {
      const tr       = e.transportation || {};
      const prod     = tr.product || {};
      const dest     = tr.destination || {};
      const planned  = e.departureTimePlanned;
      const realtime = e.departureTimeEstimated;
      const depMs    = new Date(realtime || planned).getTime();
      const minsAway = Math.round((depMs - now) / 60000);
      const delay    = realtime && planned
        ? Math.round((new Date(realtime) - new Date(planned)) / 60000) : 0;

      // disassembledName gives the cleanest short code ("T8", "CCN", "L2", "M1")
      const shortLine = tr.disassembledName || extractLineCode(prod.shortName || tr.number || '');
      const prodClass = prod.class;
      return {
        line:        shortLine || '',
        lineClass:   lineClass(shortLine || ''),
        mode:        modeFromClass(prodClass),
        destination: dest.name || '',
        via:         tr.description || '',
        time:        toHHMM(realtime || planned),
        minsAway,
        delay,
        cancelled:   !!e.isCancelled,
        platform:    e.location?.properties?.platformName || '',
        occupancy:   e.location?.properties?.occupancy || null,
      };
    });

    res.json({ stopId, departures });
  } catch (e) {
    console.error('departures:', e.message);
    res.status(500).json({ error: 'Departures failed' });
  }
});

/* ── /api/nearby?lat=<lat>&lon=<lon>&radius=<m>  ─────────────────────────── */
app.get('/api/nearby', async (req, res) => {
  const lat    = parseFloat(req.query.lat) || -33.8688;
  const lon    = parseFloat(req.query.lon) || 151.2093;
  const radius = Math.min(parseInt(req.query.radius) || 2000, 5000);

  const p = new URLSearchParams({
    outputFormat: 'rapidJSON', coordOutputFormat: 'EPSG:4326',
    mode: 'direct', type_dm: 'coord',
    name_dm: `${lon}:${lat}:EPSG:4326`,
    coordRadius: radius,
    TfNSWDM: 'true', version: '10.2.1.42',
  });

  try {
    const r = await fetch(`${TFNSW}/departure_mon?${p}`, { headers: hdrs() });
    if (!r.ok) return res.status(r.status).json({ error: `TfNSW ${r.status}` });
    const raw = await r.json();

    const seen = {};
    for (const e of (raw.stopEvents || [])) {
      const loc    = e.location || {};
      const parent = loc.parent || loc;
      const sid    = parent.id || '';
      if (!sid) continue;
      if (!seen[sid]) {
        const coord = parent.coord || loc.coord || [lat, lon];
        seen[sid] = {
          id:    sid,
          name:  (parent.disassembledName || parent.name || '').replace(/, Sydney$/, ''),
          dist:  Math.round(haversine(lat, lon, coord[0], coord[1])),
          mode:  modeFromClass((e.transportation?.product?.class)),
          lines: [],
        };
      }
      const code = e.transportation?.disassembledName ||
                   extractLineCode(e.transportation?.number || '');
      if (code && !seen[sid].lines.includes(code)) seen[sid].lines.push(code);
    }

    const stops = Object.values(seen)
      .sort((a, b) => a.dist - b.dist)
      .slice(0, 12);

    res.json({ stops, lat, lon });
  } catch (e) {
    console.error('nearby:', e.message);
    res.status(500).json({ error: 'Nearby stops failed' });
  }
});

/* ── /api/trip  ──────────────────────────────────────────────────────────── */
// ?fromId=<id>  OR  ?fromLat=<lat>&fromLon=<lon>
// &toId=<id>
// &date=YYYYMMDD  &time=HHMM  (optional, default = now)
app.get('/api/trip', async (req, res) => {
  const { fromId, fromLat, fromLon, toId } = req.query;
  if ((!fromId && !(fromLat && fromLon)) || !toId) {
    return res.status(400).json({ error: 'Provide fromId (or fromLat+fromLon) and toId' });
  }

  const now     = new Date();
  const itdDate = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`;
  const itdTime = `${pad(now.getHours())}${pad(now.getMinutes())}`;

  const origin = fromLat && fromLon
    ? { type_origin: 'coord', name_origin: `${fromLon}:${fromLat}:EPSG:4326` }
    : { type_origin: 'stop', name_origin: fromId };

  const p = new URLSearchParams({
    outputFormat: 'rapidJSON', coordOutputFormat: 'EPSG:4326',
    depArrMacro: 'dep', itdDate, itdTime,
    ...origin,
    type_destination: 'stop', name_destination: toId,
    calcNumberOfTrips: '5',
    TfNSWSF: 'true', version: '10.2.1.42',
  });

  try {
    const r = await fetch(`${TFNSW}/trip?${p}`, { headers: hdrs() });
    if (!r.ok) return res.status(r.status).json({ error: `TfNSW ${r.status}` });
    const raw = await r.json();

    const journeys = (raw.journeys || []).map(j => {
      const legs = (j.legs || []).map(leg => {
        const tr      = leg.transportation || {};
        const prod    = tr.product || {};
        const code = tr.disassembledName || extractLineCode(prod.shortName || tr.number || '');
        const isWalk  = !!leg.isFootpathLeg;
        return {
          mode:      isWalk ? 'walk' : modeFromClass(prod.class),
          line:      code || '',
          lineClass: isWalk ? 'walk' : lineClass(code || ''),
          from:      leg.origin?.name || '',
          to:        leg.destination?.name || '',
          depTime:   toHHMM(leg.origin?.departureTimeEstimated || leg.origin?.departureTimePlanned),
          arrTime:   toHHMM(leg.destination?.arrivalTimeEstimated || leg.destination?.arrivalTimePlanned),
          durationSecs: leg.duration || 0,
          walk:      isWalk,
          distanceM: leg.distance || null,
        };
      });

      const firstDep = legs[0]?.depTime ? null : null; // use raw for toHHMM
      const rawFirstDep = j.legs?.[0]?.origin?.departureTimeEstimated || j.legs?.[0]?.origin?.departureTimePlanned;
      const rawLastArr  = j.legs?.[j.legs.length - 1]?.destination?.arrivalTimeEstimated || j.legs?.[j.legs.length - 1]?.destination?.arrivalTimePlanned;
      // duration: journey-level is often null — sum legs instead
      const totalSecs = j.duration || legs.reduce((s, l) => s + (l.durationSecs || 0), 0);
      const totalMin  = Math.round(totalSecs / 60);

      return {
        depTime:      toHHMM(rawFirstDep),
        arrTime:      toHHMM(rawLastArr),
        durationMin:  totalMin,
        interchanges: j.interchanges || 0,
        legs,
      };
    });

    // sort fastest first
    journeys.sort((a, b) => a.durationMin - b.durationMin);

    res.json({ journeys });
  } catch (e) {
    console.error('trip:', e.message);
    res.status(500).json({ error: 'Trip planning failed' });
  }
});

app.listen(PORT, () =>
  console.log(`Sydney Transport → http://localhost:${PORT}`)
);

/* ── helpers ─────────────────────────────────────────────────────────────── */
function toHHMM(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function pad(n) { return String(n).padStart(2, '0'); }
function haversine(lat1, lon1, lat2, lon2) {
  const R = 6371000;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dLat/2)**2 +
    Math.cos(lat1*Math.PI/180) * Math.cos(lat2*Math.PI/180) * Math.sin(dLon/2)**2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
}

function modeFromClass(cls) {
  switch (cls) {
    case 1:  return 'train';
    case 2:  return 'metro';
    case 4:  return 'lrt';
    case 5:  return 'bus';
    case 9:  return 'ferry';
    default: return 'bus';
  }
}

function extractLineCode(name) {
  // "T1 North Shore & Western Line" → "T1", "L2 Randwick Line" → "L2"
  const m = (name || '').match(/^([A-Z][0-9]+|[0-9]+[A-Z]?)/i);
  return m ? m[1].toUpperCase() : '';
}

function lineClass(n) {
  n = (n || '').toUpperCase();
  if (['T1','T2','T3','T4','T5','T8','T9'].includes(n)) return n.toLowerCase();
  if (/^M\d/.test(n))  return 'metro';
  if (/^L\d/.test(n))  return 'lrt';
  if (/^F\d?/.test(n)) return 'ferry';
  if (/^\d/.test(n))   return 'bus';
  return 'train'; // CCN, SHL, NR, etc. — intercity trains
}
