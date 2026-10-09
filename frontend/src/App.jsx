import { useEffect, useMemo, useState } from 'react'
import useFlights from './useFlights'
import { getHistory } from './api'
import FlightMap from './components/FlightMap'
import FlightList from './components/FlightList'
import FlightDetails from './components/FlightDetails'

const AIRPORT_CENTER = [51.47, -0.4543] // Heathrow, same as server.py

export default function App() {
  const { data, error } = useFlights()
  const [selectedId, setSelectedId] = useState(null)
  const [search, setSearch] = useState('')
  const [trail, setTrail] = useState([])

  const aircraft = data?.aircraft ?? []

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return aircraft
    return aircraft.filter((a) =>
      [a.callsign, a.icao24, a.airline, a.origin, a.destination].some(
        (v) => v && String(v).toLowerCase().includes(q)
      )
    )
  }, [aircraft, search])

  const selected = aircraft.find((a) => a.icao24 === selectedId) ?? null

  // Load the logged track of the selected aircraft from SQLite
  useEffect(() => {
    if (!selectedId) {
      setTrail([])
      return
    }
    let active = true
    getHistory(selectedId)
      .then((h) => {
        if (!active) return
        setTrail(
          (h.history ?? [])
            .filter((p) => p.latitude != null && p.longitude != null)
            .map((p) => [p.latitude, p.longitude])
        )
      })
      .catch(() => {})
    return () => {
      active = false
    }
  }, [selectedId])

  return (
    <div className="app">
      <header className="topbar">
        <h1>✈ {data?.airport ?? 'Flight Radar'}</h1>
        <span className="muted">
          {aircraft.length} aircraft within {data?.radius_km ?? 100} km
        </span>
        {data?.cached && <span className="badge warn">cached data</span>}
        {error && <span className="badge err">{error}</span>}
      </header>

      <aside className="sidebar">
        <input
          className="search"
          placeholder="Search callsign, airline, airport…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <FlightList
          flights={filtered}
          selectedId={selectedId}
          onSelect={setSelectedId}
        />
        <div className="footer muted">
          Positions: OpenSky · Routes: adsbdb.com
        </div>
      </aside>

      <main className="map-area">
        <FlightMap
          flights={filtered}
          selected={selected}
          trail={trail}
          onSelect={setSelectedId}
          center={AIRPORT_CENTER}
          radiusKm={data?.radius_km ?? 100}
        />
        {selected && (
          <FlightDetails
            flight={selected}
            trailCount={trail.length}
            onClose={() => setSelectedId(null)}
          />
        )}
      </main>
    </div>
  )
}
