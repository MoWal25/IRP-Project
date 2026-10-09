import { formatAlt } from '../format'

export default function FlightList({ flights, selectedId, onSelect }) {
  if (flights.length === 0) {
    return <p className="empty muted">No aircraft found.</p>
  }

  return (
    <ul className="flight-list">
      {flights.map((f) => (
        <li
          key={f.icao24}
          className={f.icao24 === selectedId ? 'row active' : 'row'}
          onClick={() => onSelect(f.icao24)}
        >
          <div className="row-top">
            <strong>{f.callsign}</strong>
            <span className={'phase ' + f.flight_phase}>{f.flight_phase}</span>
          </div>
          <div className="row-route">
            {f.origin ?? '???'} → {f.destination ?? '???'}
          </div>
          <div className="row-sub muted">
            {f.airline} · {formatAlt(f.altitude)}
          </div>
        </li>
      ))}
    </ul>
  )
}
