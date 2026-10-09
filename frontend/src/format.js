// Your database stores meters and metres/second, so convert for display.
export function formatAlt(m) {
  return m == null ? 'n/a' : Math.round(m * 3.28084).toLocaleString() + ' ft'
}

export function formatSpeed(ms) {
  return ms == null ? 'n/a' : Math.round(ms * 1.94384) + ' kt'
}

export function formatVRate(ms) {
  return ms == null ? 'n/a' : Math.round(ms * 196.85).toLocaleString() + ' ft/min'
}

export function haversineKm(lat1, lon1, lat2, lon2) {
  const R = 6371
  const rad = (x) => (x * Math.PI) / 180
  const dLat = rad(lat2 - lat1)
  const dLon = rad(lon2 - lon1)
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(dLon / 2) ** 2
  return 2 * R * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
}
