import { useEffect, useState } from 'react'
import { getFlights } from './api'

// Fetches /api/flights now, then every 20 seconds
export default function useFlights(intervalMs = 20000) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let active = true

    async function load() {
      try {
        const json = await getFlights()
        if (active) {
          setData(json)
          setError(null)
        }
      } catch (e) {
        if (active) setError(e.message)
      }
    }

    load()
    const id = setInterval(load, intervalMs)

    return () => {
      active = false
      clearInterval(id)
    }
  }, [intervalMs])

  return { data, error }
}
