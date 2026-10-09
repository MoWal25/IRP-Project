import { formatAlt, formatSpeed, formatVRate, haversineKm } from '../format'

function Airport({ label, code, info }) {
  return (
    <div className="airport">
      <div className="muted">{label}</div>
      <div className="code">{code ?? '—'}</div>
      <div className="muted small">
        {info ? [info.city, info.name].filter(Boolean).join(' · ') : 'Unknown'}
      </div>
    </div>
  )
}

export default function FlightDetails({ flight, trailCount, onClose }) {
  const o = flight.origin_info
  const d = flight.destination_info

  let progress = null
  let remainingKm = null

  if (o?.latitude != null && d?.latitude != null) {
    const done = haversineKm(o.latitude, o.longitude, flight.latitude, flight.longitude)
    const left = haversineKm(flight.latitude, flight.longitude, d.latitude, d.longitude)
    progress = Math.round((100 * done) / (done + left))
    remainingKm = Math.round(left)
  }

  const stats = [
    ['Altitude', formatAlt(flight.altitude)],
    ['Speed', formatSpeed(flight.velocity)],
    ['Heading', flight.heading != null ? Math.round(flight.heading) + '°' : 'n/a'],
    ['Vertical rate', formatVRate(flight.vertical_rate)],
    ['Phase', flight.flight_phase],
    ['Squawk', flight.squawk ?? 'n/a'],
    ['Country', flight.country],
    ['ICAO24', flight.icao24],
    ['From airport', Math.round(flight.distance_from_airport) + ' km'],
    ['Logged points', trailCount],
  ]

  return (
    <section className="details">
      <button className="close" onClick={onClose}>×</button>

      <h2>{flight.callsign}</h2>
      <div className="muted">{flight.airline}</div>

      <div className="route">
        <Airport label="From" code={flight.origin} info={o} />
        <div className="arrow">✈</div>
        <Airport label="To" code={flight.destination} info={d} />
      </div>

      {progress != null ? (
        <>
          <div className="bar"><div className="fill" style={{ width: progress + '%' }} /></div>
          <div className="muted small">
            {progress}% of route flown · about {remainingKm.toLocaleString()} km to go
          </div>
        </>
      ) : (
        <div className="muted small">Route unknown for this callsign.</div>
      )}

      <dl className="stats">
        {stats.map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>

      <div className="muted small">
        Route is the scheduled route for this callsign, not confirmed live.
      </div>
    </section>
  )
}
