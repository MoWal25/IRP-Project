import { useEffect } from 'react'
import L from 'leaflet'
import {
  MapContainer, TileLayer, Marker, Polyline,
  Circle, CircleMarker, Tooltip, useMap,
} from 'react-leaflet'

const PHASE_COLORS = {
  CLIMBING: '#22c55e',
  DESCENDING: '#f59e0b',
  CRUISING: '#38bdf8',
  UNKNOWN: '#9ca3af',
}

function planeIcon(heading, color, selected) {
  const size = selected ? 36 : 26
  return L.divIcon({
    className: 'plane-icon',
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
    html: `<svg width="${size}" height="${size}" viewBox="0 0 24 24"
             style="transform:rotate(${heading ?? 0}deg)">
             <path d="M12 2l2 7 8 5v2l-8-2.5-.5 5.5 2.5 2v1l-4-1-4 1v-1l2.5-2-.5-5.5-8 2.5v-2l8-5z"
                   fill="${color}" stroke="#0f172a" stroke-width="1"/>
           </svg>`,
  })
}

// Zoom to the plane (and its route, if known) when the selection changes
function FocusSelected({ selected }) {
  const map = useMap()
  const id = selected?.icao24

  useEffect(() => {
    if (!selected) return

    const points = [[selected.latitude, selected.longitude]]
    const o = selected.origin_info
    const d = selected.destination_info

    if (o?.latitude != null) points.push([o.latitude, o.longitude])
    if (d?.latitude != null) points.push([d.latitude, d.longitude])

    if (points.length > 1) {
      map.fitBounds(points, { padding: [80, 80], maxZoom: 9 })
    } else {
      map.flyTo(points[0], Math.max(map.getZoom(), 9))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  return null
}

export default function FlightMap({ flights, selected, trail, onSelect, center, radiusKm }) {
  const o = selected?.origin_info
  const d = selected?.destination_info

  return (
    <MapContainer center={center} zoom={8} className="map">
      <TileLayer
        url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
        attribution="&copy; OpenStreetMap contributors &copy; CARTO"
      />

      <Circle
        center={center}
        radius={radiusKm * 1000}
        pathOptions={{ color: '#6366f1', weight: 1, fillOpacity: 0.03 }}
      />

      {trail.length > 1 && (
        <Polyline positions={trail} pathOptions={{ color: '#38bdf8', weight: 3 }} />
      )}

      {selected && o?.latitude != null && (
        <>
          <Polyline
            positions={[[o.latitude, o.longitude], [selected.latitude, selected.longitude]]}
            pathOptions={{ color: '#22c55e', weight: 2, dashArray: '6 8' }}
          />
          <CircleMarker center={[o.latitude, o.longitude]} radius={7}
            pathOptions={{ color: '#22c55e', fillOpacity: 1 }}>
            <Tooltip permanent direction="top">{selected.origin}</Tooltip>
          </CircleMarker>
        </>
      )}

      {selected && d?.latitude != null && (
        <>
          <Polyline
            positions={[[selected.latitude, selected.longitude], [d.latitude, d.longitude]]}
            pathOptions={{ color: '#f59e0b', weight: 2, dashArray: '6 8' }}
          />
          <CircleMarker center={[d.latitude, d.longitude]} radius={7}
            pathOptions={{ color: '#f59e0b', fillOpacity: 1 }}>
            <Tooltip permanent direction="top">{selected.destination}</Tooltip>
          </CircleMarker>
        </>
      )}

      {flights.map((f) => (
        <Marker
          key={f.icao24}
          position={[f.latitude, f.longitude]}
          icon={planeIcon(
            f.heading,
            PHASE_COLORS[f.flight_phase] ?? PHASE_COLORS.UNKNOWN,
            f.icao24 === selected?.icao24
          )}
          eventHandlers={{ click: () => onSelect(f.icao24) }}
        >
          <Tooltip direction="top" offset={[0, -12]}>
            {f.callsign} · {f.origin ?? '???'} → {f.destination ?? '???'}
          </Tooltip>
        </Marker>
      ))}

      <FocusSelected selected={selected} />
    </MapContainer>
  )
}
