
async function apiRequest(url) {
  const res = await fetch(url)

  const data = await res.json().catch(() => ({}))

  if (!res.ok) {
    throw new Error(
      data.error || data.message || `Request failed (${res.status})`
    )
  }

  return data
}

export function getFlights() {
  return apiRequest('/api/flights')
}

export function getHistory(icao24) {
  return apiRequest(
    '/api/flight-history/' + encodeURIComponent(icao24)
  )
}