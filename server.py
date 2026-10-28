from flask import Flask, jsonify, send_from_directory, request
import requests
import math
import sqlite3
import os
import time
import re
from datetime import datetime, timezone

app = Flask(__name__)

# ============================================================
# CONFIGURATION
# ============================================================

AIRPORTS = {
    "LHR": {
        "name": "London Heathrow",
        "lat": 51.4700,
        "lon": -0.4543
    },
    "DXB": {
        "name": "Dubai International",
        "lat": 25.2532,
        "lon": 55.3657
    },
    "JED": {
        "name": "King Abdulaziz International (Jeddah)",
        "lat": 21.6796,
        "lon": 39.1565
    }
}

DEFAULT_AIRPORT_CODE = "LHR"
GEOFENCE_RADIUS = 100
DATABASE = "flight_tracker.db"
OPENSKY_URL = "https://opensky-network.org/api/states/all"
MIN_REQUEST_INTERVAL = 15

last_request_time = 0
raw_states_cache = {}
aircraft_cache = {}
last_successful_time = {}

# ============================================================
# OPTIONAL OPENSKY OAUTH
# ============================================================

OPENSKY_CLIENT_ID = os.getenv("OPENSKY_CLIENT_ID")
OPENSKY_CLIENT_SECRET = os.getenv("OPENSKY_CLIENT_SECRET")

access_token = None
access_token_expires = 0


def get_opensky_token():
    global access_token, access_token_expires

    if not OPENSKY_CLIENT_ID or not OPENSKY_CLIENT_SECRET:
        return None

    if access_token and time.time() < access_token_expires:
        return access_token

    token_url = (
        "https://auth.opensky-network.org/"
        "auth/realms/opensky-network/protocol/openid-connect/token"
    )

    try:
        response = requests.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": OPENSKY_CLIENT_ID,
                "client_secret": OPENSKY_CLIENT_SECRET
            },
            timeout=10
        )

        response.raise_for_status()
        data = response.json()

        access_token = data["access_token"]
        access_token_expires = (
            time.time() + data.get("expires_in", 1800) - 60
        )

        print("OpenSky OAuth authentication successful.")
        return access_token

    except Exception as error:
        print("OpenSky OAuth failed:", error)
        access_token = None
        return None


# ============================================================
# AIRLINE IDENTIFICATION
# ============================================================

AIRLINE_BY_ICAO_CODE = {
    "DAL": "Delta Air Lines",
    "AAL": "American Airlines",
    "UAL": "United Airlines",
    "SWA": "Southwest Airlines",
    "ASA": "Alaska Airlines",
    "JBU": "JetBlue Airways",

    "BAW": "British Airways",
    "VIR": "Virgin Atlantic",
    "EIN": "Aer Lingus",
    "EXS": "Jet2",
    "EZY": "easyJet",

    "AFR": "Air France",
    "KLM": "KLM Royal Dutch Airlines",
    "DLH": "Lufthansa",
    "EWG": "Eurowings",
    "SWR": "SWISS",
    "ITY": "ITA Airways",
    "IBE": "Iberia",
    "TAP": "TAP Air Portugal",
    "SAS": "SAS Scandinavian Airlines",
    "FIN": "Finnair",
    "LOT": "LOT Polish Airlines",
    "AUA": "Austrian Airlines",
    "THY": "Turkish Airlines",
    "WZZ": "Wizz Air",
    "RYR": "Ryanair",
    "TOM": "TUI Airways",
    "CFG": "Condor",
    "AEA": "Air Europa",

    "UAE": "Emirates",
    "ETD": "Etihad Airways",
    "QTR": "Qatar Airways",

    "SVA": "Saudia",
    "RXI": "Riyadh Air",
    "GFA": "Gulf Air",
    "KAC": "Kuwait Airways",
    "JZR": "Jazeera Airways",
    "OMA": "Oman Air",

    "AIC": "Air India",
    "AXB": "Air India Express",
    "IGO": "IndiGo",
    "SEJ": "SpiceJet",

    "PIA": "Pakistan International Airlines",
    "ABQ": "Airblue",

    "SIA": "Singapore Airlines",
    "CPA": "Cathay Pacific",
    "ANA": "All Nippon Airways",
    "JAL": "Japan Airlines",
    "KAL": "Korean Air",
    "CES": "China Eastern Airlines",
    "CCA": "Air China",
    "CSN": "China Southern Airlines",

    "QFA": "Qantas",
    "VOZ": "Virgin Australia",
    "ANZ": "Air New Zealand",

    "ETH": "Ethiopian Airlines",
    "SAA": "South African Airways",

    "FDX": "FedEx",
    "UPS": "UPS Airlines",
    "GTI": "Atlas Air"
}


def identify_airline(callsign):
    if not callsign:
        return "Unknown Operator"

    callsign = callsign.strip().upper().replace(" ", "")

    if len(callsign) < 3:
        return "Unknown Operator"

    return AIRLINE_BY_ICAO_CODE.get(
        callsign[:3],
        "Unknown Operator"
    )


# ============================================================
# DATABASE SETUP
# ============================================================

def setup_database():
    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Flight_Logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            icao24 TEXT,
            callsign TEXT,
            country TEXT,
            latitude REAL,
            longitude REAL,
            altitude REAL,
            velocity REAL,
            heading REAL,
            vertical_rate REAL,
            flight_phase TEXT,
            distance_from_airport REAL,
            airline TEXT,
            origin TEXT,
            destination TEXT
        )
    """)

    cursor.execute("PRAGMA table_info(Flight_Logs)")
    existing_columns = {
        row[1] for row in cursor.fetchall()
    }

    for column in ("airline", "origin", "destination","airport_code"):
        if column not in existing_columns:
            cursor.execute(
                f"ALTER TABLE Flight_Logs ADD COLUMN {column} TEXT"
            )

    connection.commit()
    connection.close()


# ============================================================
# HAVERSINE DISTANCE
# ============================================================

def calculate_distance(lat1, lon1, lat2, lon2):
    radius = 6371

    lat1 = math.radians(lat1)
    lon1 = math.radians(lon1)
    lat2 = math.radians(lat2)
    lon2 = math.radians(lon2)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin(dlon / 2) ** 2
    )

    c = 2 * math.atan2(
        math.sqrt(a),
        math.sqrt(1 - a)
    )

    return radius * c


# ============================================================
# FLIGHT PHASE
# ============================================================

def determine_flight_phase(vertical_rate):
    if vertical_rate is None:
        return "UNKNOWN"

    if vertical_rate > 0.5:
        return "CLIMBING"

    if vertical_rate < -0.5:
        return "DESCENDING"

    return "CRUISING"


# ============================================================
# OPENSKY REQUEST
# ============================================================

def request_opensky_states(airport_code):
    global last_request_time

    now = time.time()

    if now - last_request_time < MIN_REQUEST_INTERVAL:
        if airport_code in raw_states_cache:
            print(
                f"OpenSky cooldown: using {airport_code} cached data."
            )
            return raw_states_cache[airport_code], True

        return [], True

    airport = AIRPORTS[airport_code]

    # Request a surrounding area, then apply the exact 100 km filter.
    lat_delta = 1.15
    lon_delta = 1.5 if abs(airport["lat"]) < 40 else 2.0

    params = {
        "lamin": max(-90, airport["lat"] - lat_delta),
        "lamax": min(90, airport["lat"] + lat_delta),
        "lomin": max(-180, airport["lon"] - lon_delta),
        "lomax": min(180, airport["lon"] + lon_delta)
    }

    headers = {}
    token = get_opensky_token()

    if token:
        headers["Authorization"] = f"Bearer {token}"

    last_request_time = now

    try:
        response = requests.get(
            OPENSKY_URL,
            params=params,
            headers=headers,
            timeout=15
        )

        response.raise_for_status()

        states = response.json().get("states", []) or []
        raw_states_cache[airport_code] = states

        print(
            f"OpenSky returned {len(states)} state vectors "
            f"near {airport_code}."
        )

        return states, False

    except Exception as error:
        print(f"OpenSky request failed for {airport_code}: {error}")

        if airport_code in raw_states_cache:
            print("Using previously cached raw data.")
            return raw_states_cache[airport_code], True

        if airport_code in aircraft_cache:
            print("Using previously cached aircraft data.")
            return None, True

        raise


# ============================================================
# GET AIRCRAFT FOR THE SELECTED AIRPORT
# ============================================================

def get_live_aircraft(airport_code):
    airport = AIRPORTS[airport_code]

    raw_states, using_cache = request_opensky_states(airport_code)

    if raw_states is None:
        return aircraft_cache.get(airport_code, []), True

    if using_cache and not raw_states:
        return aircraft_cache.get(airport_code, []), True

    aircraft_list = []

    for aircraft in raw_states:
        try:
            icao24 = aircraft[0]
            callsign = (
                aircraft[1].strip()
                if aircraft[1]
                else ""
            )
            country = aircraft[2]

            longitude = aircraft[5]
            latitude = aircraft[6]

            if latitude is None or longitude is None:
                continue

            distance = calculate_distance(
                airport["lat"],
                airport["lon"],
                latitude,
                longitude
            )

            if distance > GEOFENCE_RADIUS:
                continue

            altitude = aircraft[7]
            on_ground = aircraft[8]
            velocity = aircraft[9]
            heading = aircraft[10]
            vertical_rate = aircraft[11]

            geo_altitude = (
                aircraft[13]
                if len(aircraft) > 13
                else None
            )

            squawk = (
                aircraft[14]
                if len(aircraft) > 14
                else None
            )

            position_source = (
                aircraft[16]
                if len(aircraft) > 16
                else None
            )

            airline = identify_airline(callsign)

            aircraft_list.append({
                "icao24": icao24,
                "callsign": callsign or "Unknown",
                "flight_number": callsign or "Unknown",
                "airline": airline,
                "operator": airline,

                # OpenSky position data alone does not provide
                # reliable origin/destination airport information.
                "origin": None,
                "destination": None,

                "country": country,
                "latitude": latitude,
                "longitude": longitude,
                "altitude": altitude,
                "geo_altitude": geo_altitude,
                "velocity": velocity,
                "heading": heading,
                "vertical_rate": vertical_rate,

                "flight_phase": determine_flight_phase(
                    vertical_rate
                ),

                "distance_from_airport": distance,
                "on_ground": on_ground,
                "squawk": squawk,
                "position_source": position_source,
                "airport_code": airport_code
            })

        except (IndexError, TypeError, ValueError) as error:
            print("Aircraft parsing error:", error)

    if not using_cache:
        aircraft_cache[airport_code] = aircraft_list
        last_successful_time[airport_code] = (
            datetime.now(timezone.utc).isoformat()
        )

    print(
        f"{airport_code}: {len(aircraft_list)} aircraft "
        f"within {GEOFENCE_RADIUS} km."
    )

    return aircraft_list, using_cache


# ============================================================
# SAVE FLIGHTS TO DATABASE
# ============================================================


def save_flights_to_database(aircraft_list):
    if not aircraft_list:
        return

    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()

    timestamp = datetime.now(timezone.utc).isoformat()

    for aircraft in aircraft_list:
        cursor.execute("""
            INSERT INTO Flight_Logs (
                timestamp, icao24, callsign, country,
                latitude, longitude, altitude, velocity,
                heading, vertical_rate, flight_phase,
                distance_from_airport, airline, origin,
                destination, airport_code
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            timestamp,
            aircraft.get("icao24"),
            aircraft.get("callsign"),
            aircraft.get("country"),
            aircraft.get("latitude"),
            aircraft.get("longitude"),
            aircraft.get("altitude"),
            aircraft.get("velocity"),
            aircraft.get("heading"),
            aircraft.get("vertical_rate"),
            aircraft.get("flight_phase"),
            aircraft.get("distance_from_airport"),
            aircraft.get("airline"),
            aircraft.get("origin"),
            aircraft.get("destination"),
            aircraft.get("airport_code")
        ))

    connection.commit()
    connection.close()



# ============================================================
# API: AIRPORTS
# ============================================================

@app.route("/api/airports")
def api_airports():
    return jsonify({
        code: {
            "code": code,
            **airport,
            "radius_km": GEOFENCE_RADIUS
        }
        for code, airport in AIRPORTS.items()
    })


# ============================================================
# API: LIVE FLIGHTS
# ============================================================

@app.route("/api/flights")
def api_flights():
    airport_code = request.args.get(
        "airport",
        DEFAULT_AIRPORT_CODE
    ).strip().upper()

    if airport_code not in AIRPORTS:
        return jsonify({
            "error": "Unsupported airport code",
            "available_airports": list(AIRPORTS.keys())
        }), 400

    airport = AIRPORTS[airport_code]

    try:
        aircraft, using_cache = get_live_aircraft(airport_code)

        if not using_cache:
            save_flights_to_database(aircraft)

        return jsonify({
            "source": "OpenSky",
            "cached": using_cache,
            "updated_at": last_successful_time.get(airport_code),
            "airport": airport["name"],
            "airport_code": airport_code,
            "airport_lat": airport["lat"],
            "airport_lon": airport["lon"],
            "radius_km": GEOFENCE_RADIUS,
            "aircraft": aircraft
        })

    except Exception as error:
        print("API /api/flights error:", error)

        return jsonify({
            "error": str(error),
            "source": "OpenSky",
            "airport_code": airport_code,
            "aircraft": aircraft_cache.get(airport_code, [])
        }), 503


# ============================================================
# API: FLIGHT HISTORY
# ============================================================

@app.route("/api/flight-history/<flight_number>")
def flight_history(flight_number):
    search_value = flight_number.strip().upper()

    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            timestamp,
            callsign,
            icao24,
            country,
            airline,
            origin,
            destination,
            latitude,
            longitude,
            altitude,
            velocity,
            heading,
            vertical_rate,
            flight_phase,
            distance_from_airport
        FROM Flight_Logs
        WHERE
            UPPER(callsign) = ?
            OR UPPER(icao24) = ?
        ORDER BY id ASC
        LIMIT 500
    """, (
        search_value,
        search_value
    ))

    rows = cursor.fetchall()
    connection.close()

    if not rows:
        return jsonify({
            "found": False,
            "flight_number": search_value,
            "record_count": 0,
            "history": []
        })

    history = []

    for row in rows:
        history.append({
            "timestamp": row[0],
            "callsign": row[1],
            "icao24": row[2],
            "country": row[3],
            "airline": row[4],
            "origin": row[5],
            "destination": row[6],
            "latitude": row[7],
            "longitude": row[8],
            "altitude": row[9],
            "velocity": row[10],
            "heading": row[11],
            "vertical_rate": row[12],
            "flight_phase": row[13],
            "distance_from_airport": row[14]
        })

    first = rows[0]

    return jsonify({
        "found": True,
        "flight_number": search_value,
        "callsign": first[1],
        "icao24": first[2],
        "country": first[3],
        "airline": first[4],
        "origin": first[5],
        "destination": first[6],
        "record_count": len(history),
        "history": history
    })


# ============================================================
# API: DATABASE STATS
# ============================================================

@app.route("/api/database-stats")
def database_stats():
    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()

    cursor.execute("SELECT COUNT(*) FROM Flight_Logs")
    total_records = cursor.fetchone()[0]

    cursor.execute(
        "SELECT COUNT(DISTINCT icao24) FROM Flight_Logs"
    )
    unique_aircraft = cursor.fetchone()[0]

    connection.close()

    return jsonify({
        "total_records": total_records,
        "unique_aircraft": unique_aircraft
    })


# ============================================================
# SERVE MAP
# ============================================================

@app.route("/")
def home():
    return send_from_directory(".", "map.html")


# ============================================================
# SERVE 3D MAP
# ============================================================

@app.route("/3d")
def three_d():
    return send_from_directory(".", "3d.html")

# ============================================================
# AIRPORT ARRIVALS & DEPARTURES - NEXT 30 MINUTES
# ============================================================

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

AERODATABOX_API_KEY = os.getenv("AERODATABOX_API_KEY")

AIRPORT_TIMEZONES = {
    "LHR": "Europe/London",
    "DXB": "Asia/Dubai",
    "JED": "Asia/Riyadh"
}

AIRPORT_BOARD_CACHE = {}
AIRPORT_BOARD_CACHE_TIME = {}
AIRPORT_BOARD_CACHE_SECONDS = 60


def get_airport_board(airport_code):
    if airport_code not in AIRPORTS:
        raise ValueError("Unsupported airport")

    now = time.time()

    # Avoid repeatedly using API credits when refreshing the dashboard.
    if (
        airport_code in AIRPORT_BOARD_CACHE
        and now - AIRPORT_BOARD_CACHE_TIME.get(airport_code, 0)
        < AIRPORT_BOARD_CACHE_SECONDS
    ):
        return AIRPORT_BOARD_CACHE[airport_code], True

    if not AERODATABOX_API_KEY:
        return {
            "configured": False,
            "message": (
                "Add AERODATABOX_API_KEY to enable real "
                "airport schedules."
            ),
            "arrivals": [],
            "departures": []
        }, False

    timezone_name = AIRPORT_TIMEZONES[airport_code]
    airport_now = datetime.now(ZoneInfo(timezone_name))
    window_end = airport_now + timedelta(minutes=30)

    from_local = airport_now.strftime("%Y-%m-%dT%H:%M")
    to_local = window_end.strftime("%Y-%m-%dT%H:%M")

    url = (
        "https://aerodatabox.p.rapidapi.com/"
        f"flights/airports/iata/{airport_code}/"
        f"{from_local}/{to_local}"
    )

    headers = {
        "X-RapidAPI-Key": AERODATABOX_API_KEY,
        "X-RapidAPI-Host": "aerodatabox.p.rapidapi.com"
    }

    params = {
        "direction": "Both",
        "withCancelled": "false",
        "withCodeshared": "true",
        "withCargo": "false",
        "withPrivate": "false"
    }

    response = requests.get(
        url,
        headers=headers,
        params=params,
        timeout=20
    )
    response.raise_for_status()
    data = response.json()

    # The FIDS response contains arrivals and departures.
    # Handle both the usual object response and a list response.
    if isinstance(data, list):
        arrivals = [
            f for f in data
            if isinstance(f, dict)
            and f.get("movement", "").lower() == "arrival"
        ]
        departures = [
            f for f in data
            if isinstance(f, dict)
            and f.get("movement", "").lower() == "departure"
        ]
    else:
        arrivals = data.get("arrivals", []) or []
        departures = data.get("departures", []) or []

    result = {
        "configured": True,
        "airport": AIRPORTS[airport_code]["name"],
        "airport_code": airport_code,
        "timezone": timezone_name,
        "window_start": from_local,
        "window_end": to_local,
        "arrivals": arrivals,
        "departures": departures
    }

    AIRPORT_BOARD_CACHE[airport_code] = result
    AIRPORT_BOARD_CACHE_TIME[airport_code] = now

    return result, False


@app.route("/api/airport-board")
def api_airport_board():
    airport_code = request.args.get(
        "airport", DEFAULT_AIRPORT_CODE
    ).strip().upper()

    if airport_code not in AIRPORTS:
        return jsonify({
            "error": "Unsupported airport",
            "available_airports": list(AIRPORTS.keys())
        }), 400

    try:
        board, cached = get_airport_board(airport_code)
        board["cached"] = cached
        return jsonify(board)

    except requests.RequestException as error:
        print("Airport board API error:", error)

        return jsonify({
            "error": "Unable to retrieve airport schedules.",
            "details": str(error),
            "airport_code": airport_code,
            "arrivals": [],
            "departures": []
        }), 502

    except Exception as error:
        print("Airport board error:", error)

        return jsonify({
            "error": str(error),
            "airport_code": airport_code,
            "arrivals": [],
            "departures": []
        }), 500
# ============================================================
# STARTUP
# ============================================================

# ==========================================================
# ADSB.LOL BACKUP FLIGHT DATA — NO API KEY
# ==========================================================

import math
import requests
from flask import request, jsonify


@app.route("/api/adsblol-flights")
def api_adsblol_flights():
    airport_code = request.args.get("airport", "LHR").upper()

    if airport_code not in AIRPORTS:
        return jsonify({
            "error": "Unknown airport",
            "aircraft": []
        }), 400

    airport = AIRPORTS[airport_code]

    try:
        url = (
            "https://api.adsb.lol/v2/point/"
            f"{airport['lat']}/{airport['lon']}/54"
        )

        response = requests.get(url, timeout=12)
        response.raise_for_status()
        data = response.json()

        aircraft = []

        for plane in data.get("ac", []):
            lat = plane.get("lat")
            lon = plane.get("lon")

            if lat is None or lon is None:
                continue

            lat = float(lat)
            lon = float(lon)

            # Calculate distance from selected airport in km.
            lat1 = math.radians(airport["lat"])
            lat2 = math.radians(lat)
            dlat = lat2 - lat1
            dlon = math.radians(lon - airport["lon"])

            a = (
                math.sin(dlat / 2) ** 2
                + math.cos(lat1) * math.cos(lat2)
                * math.sin(dlon / 2) ** 2
            )

            distance_km = (
                6371 * 2 * math.atan2(
                    math.sqrt(a),
                    math.sqrt(max(0, 1 - a))
                )
            )

            if distance_km > 100:
                continue

            # ADS-B altitude is normally in feet.
            raw_altitude = plane.get("alt_baro")
            altitude = None

            if isinstance(raw_altitude, (int, float)):
                altitude = raw_altitude * 0.3048

            # Ground speed is normally in knots.
            raw_speed = plane.get("gs")
            velocity = (
                raw_speed * 0.514444
                if isinstance(raw_speed, (int, float))
                else None
            )

            # Track is degrees clockwise from north.
            heading = plane.get("track")

            # Vertical rate is normally feet per minute.
            vertical_rate = plane.get("baro_rate", 0) or 0

            if vertical_rate > 300:
                phase = "CLIMBING"
            elif vertical_rate < -300:
                phase = "DESCENDING"
            else:
                phase = "CRUISING"

            callsign = (plane.get("flight") or "").strip()

            aircraft.append({
                "icao24": plane.get("hex", ""),
                "callsign": callsign,
                "latitude": lat,
                "longitude": lon,
                "altitude": altitude,
                "velocity": velocity,
                "heading": heading,
                "flight_phase": phase,
                "distance_from_airport": distance_km,
                "country": "Unknown",
                "source": "ADSB.lol"
            })

        return jsonify({
            "airport_code": airport_code,
            "source": "ADSB.lol backup feed",
            "aircraft": aircraft,
            "count": len(aircraft)
        })

    except requests.RequestException as error:
        app.logger.warning("ADSB.lol request failed: %s", error)

        return jsonify({
            "error": "ADSB.lol is temporarily unavailable",
            "aircraft": []
        }), 502

    except (ValueError, TypeError, KeyError) as error:
        app.logger.exception("Could not process ADSB.lol data")

        return jsonify({
            "error": "Could not process backup flight data",
            "aircraft": []
        }), 500
        
# ============================================================
# GLOBAL FLIGHT RADAR - INTELLIGENCE FEATURES
# ============================================================

from collections import defaultdict
from statistics import mean

WEATHER_CACHE = {}
WEATHER_CACHE_TIME = {}
WEATHER_CACHE_SECONDS = 600

AIRPORT_WEATHER_TIMEZONES = {
    "LHR": "Europe/London",
    "DXB": "Asia/Dubai",
    "JED": "Asia/Riyadh"
}


def get_weather_description(code):
    descriptions = {
        0: "Clear sky",
        1: "Mainly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Fog",
        48: "Freezing fog",
        51: "Light drizzle",
        53: "Moderate drizzle",
        55: "Heavy drizzle",
        61: "Light rain",
        63: "Moderate rain",
        65: "Heavy rain",
        71: "Light snow",
        73: "Moderate snow",
        75: "Heavy snow",
        80: "Rain showers",
        81: "Moderate rain showers",
        82: "Heavy rain showers",
        95: "Thunderstorm",
        96: "Thunderstorm with hail",
        99: "Severe thunderstorm with hail"
    }
    return descriptions.get(code, "Conditions unavailable")


# ------------------------------------------------------------
# 1. AIRPORT WEATHER INTELLIGENCE
# Uses Open-Meteo public weather data; no API key required.
# ------------------------------------------------------------

@app.route("/api/airport-weather")
def api_airport_weather():
    airport_code = request.args.get(
        "airport", DEFAULT_AIRPORT_CODE
    ).strip().upper()

    if airport_code not in AIRPORTS:
        return jsonify({
            "error": "Unsupported airport",
            "available_airports": list(AIRPORTS.keys())
        }), 400

    now = time.time()

    if (
        airport_code in WEATHER_CACHE
        and now - WEATHER_CACHE_TIME.get(airport_code, 0)
        < WEATHER_CACHE_SECONDS
    ):
        result = dict(WEATHER_CACHE[airport_code])
        result["cached"] = True
        return jsonify(result)

    airport = AIRPORTS[airport_code]

    try:
        response = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": airport["lat"],
                "longitude": airport["lon"],
                "current": (
                    "temperature_2m,relative_humidity_2m,"
                    "apparent_temperature,is_day,precipitation,"
                    "weather_code,cloud_cover,pressure_msl,"
                    "wind_speed_10m,wind_direction_10m,visibility"
                ),
                "timezone": AIRPORT_WEATHER_TIMEZONES[airport_code]
            },
            timeout=15
        )
        response.raise_for_status()
        data = response.json()
        current = data.get("current", {})

        weather_code = current.get("weather_code")

        result = {
            "airport_code": airport_code,
            "airport": airport["name"],
            "source": "Open-Meteo",
            "cached": False,
            "observed_at": current.get("time"),
            "temperature_c": current.get("temperature_2m"),
            "feels_like_c": current.get("apparent_temperature"),
            "humidity_percent": current.get(
                "relative_humidity_2m"
            ),
            "weather_code": weather_code,
            "conditions": get_weather_description(weather_code),
            "wind_speed_kmh": current.get("wind_speed_10m"),
            "wind_direction_degrees": current.get(
                "wind_direction_10m"
            ),
            "visibility_m": current.get("visibility"),
            "cloud_cover_percent": current.get("cloud_cover"),
            "pressure_hpa": current.get("pressure_msl"),
            "precipitation_mm": current.get("precipitation"),
            "is_day": current.get("is_day")
        }

        WEATHER_CACHE[airport_code] = result
        WEATHER_CACHE_TIME[airport_code] = now

        return jsonify(result)

    except (requests.RequestException, ValueError) as error:
        app.logger.warning("Weather request failed: %s", error)
        return jsonify({
            "error": "Weather data is temporarily unavailable",
            "airport_code": airport_code
        }), 502


# ------------------------------------------------------------
# 2 AND 4. AIRPORT ACTIVITY AND TRAFFIC SUMMARY
# ------------------------------------------------------------

@app.route("/api/airport-activity")
def api_airport_activity():
    results = []

    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()

    for code, airport in AIRPORTS.items():
        aircraft = aircraft_cache.get(code, [])

        phases = defaultdict(int)

        for plane in aircraft:
            phases[plane.get("flight_phase", "UNKNOWN")] += 1

        cursor.execute("""
            SELECT COUNT(*), COUNT(DISTINCT icao24)
            FROM Flight_Logs
            WHERE airport_code = ?
              AND timestamp >= datetime('now', '-24 hours')
        """, (code,))

        row = cursor.fetchone()

        results.append({
            "airport_code": code,
            "airport": airport["name"],
            "currently_cached_aircraft": len(aircraft),
            "climbing": phases["CLIMBING"],
            "cruising": phases["CRUISING"],
            "descending": phases["DESCENDING"],
            "unknown_phase": phases["UNKNOWN"],
            "ground": phases["GROUND"],
            "observations_last_24_hours": row[0] or 0,
            "unique_aircraft_logged_last_24_hours": row[1] or 0,
            "note": (
                "Cached aircraft are not necessarily a complete "
                "count of airport arrivals or departures."
            )
        })

    connection.close()

    return jsonify({
        "airports": results,
        "source": "OpenSky observations and local SQLite history"
    })


# ------------------------------------------------------------
# 6. MOVEMENT ANOMALY / INTERESTING EVENT DETECTOR
# These are data-pattern flags, not safety or emergency alerts.
# ------------------------------------------------------------

@app.route("/api/flight-events")
def api_flight_events():
    airport_code = request.args.get(
        "airport", DEFAULT_AIRPORT_CODE
    ).strip().upper()

    if airport_code not in AIRPORTS:
        return jsonify({
            "error": "Unsupported airport",
            "available_airports": list(AIRPORTS.keys())
        }), 400

    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT icao24, callsign, timestamp, altitude,
               heading, velocity, vertical_rate, flight_phase
        FROM Flight_Logs
        WHERE airport_code = ?
          AND timestamp >= datetime('now', '-30 minutes')
        ORDER BY icao24, timestamp ASC
    """, (airport_code,))

    rows = cursor.fetchall()
    connection.close()

    by_aircraft = defaultdict(list)

    for row in rows:
        by_aircraft[row[0]].append(row)

    events = []

    for icao24, history in by_aircraft.items():
        if len(history) < 2:
            continue

        previous = history[-2]
        current = history[-1]

        _, callsign, timestamp, altitude, heading, velocity, vertical_rate, phase = current
        _, _, previous_timestamp, previous_altitude, previous_heading, _, _, _ = previous

        if (
            heading is not None
            and previous_heading is not None
        ):
            turn = abs(heading - previous_heading)
            turn = min(turn, 360 - turn)

            if turn >= 90:
                events.append({
                    "icao24": icao24,
                    "callsign": callsign,
                    "type": "LARGE_HEADING_CHANGE",
                    "description": (
                        "Heading changed by at least 90 degrees "
                        "between recorded observations."
                    ),
                    "heading_change_degrees": round(turn, 1),
                    "observed_at": timestamp,
                    "severity": "INFO"
                })

        if (
            altitude is not None
            and previous_altitude is not None
            and abs(altitude - previous_altitude) >= 1500
        ):
            events.append({
                "icao24": icao24,
                "callsign": callsign,
                "type": "LARGE_ALTITUDE_CHANGE",
                "description": (
                    "Altitude differed by at least 1500 metres "
                    "between recorded observations."
                ),
                "altitude_change_m": round(
                    altitude - previous_altitude, 1
                ),
                "observed_at": timestamp,
                "severity": "INFO"
            })

    return jsonify({
        "airport_code": airport_code,
        "events": events,
        "count": len(events),
        "note": (
            "These are automatically detected data patterns, "
            "not evidence of an emergency or unsafe flight."
        )
    })


# ------------------------------------------------------------
# 7. DATA FRESHNESS / CONFIDENCE
# ------------------------------------------------------------

@app.route("/api/data-confidence")
def api_data_confidence():
    now = datetime.now(timezone.utc)
    results = []

    for code, airport in AIRPORTS.items():
        last_update = last_successful_time.get(code)
        age_seconds = None
        status = "NO_RECENT_DATA"

        if last_update:
            try:
                parsed = datetime.fromisoformat(
                    last_update.replace("Z", "+00:00")
                )

                age_seconds = max(
                    0, int((now - parsed).total_seconds())
                )

                if age_seconds <= 60:
                    status = "FRESH"
                elif age_seconds <= 300:
                    status = "AGING"
                else:
                    status = "STALE"

            except (ValueError, TypeError):
                status = "UNKNOWN"

        results.append({
            "airport_code": code,
            "airport": airport["name"],
            "status": status,
            "seconds_since_successful_update": age_seconds,
            "last_successful_update": last_update,
            "cached_aircraft_count": len(
                aircraft_cache.get(code, [])
            )
        })

    return jsonify({"airports": results})


# ------------------------------------------------------------
# 8. SIMPLE HISTORICAL TRAFFIC ESTIMATES
# Uses recorded observations, not scheduled flight counts.
# ------------------------------------------------------------

@app.route("/api/airport-predictions")
def api_airport_predictions():
    airport_code = request.args.get(
        "airport", DEFAULT_AIRPORT_CODE
    ).strip().upper()

    if airport_code not in AIRPORTS:
        return jsonify({
            "error": "Unsupported airport",
            "available_airports": list(AIRPORTS.keys())
        }), 400

    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            date(timestamp) AS day,
            strftime('%H', timestamp) AS hour,
            COUNT(*) AS observations
        FROM Flight_Logs
        WHERE airport_code = ?
          AND timestamp >= datetime('now', '-14 days')
        GROUP BY day, hour
        ORDER BY day, hour
    """, (airport_code,))

    rows = cursor.fetchall()
    connection.close()

    hourly_values = defaultdict(list)

    for day, hour, count in rows:
        if hour is not None:
            hourly_values[hour].append(count)

    estimates = []

    for hour in range(24):
        key = f"{hour:02d}"
        values = hourly_values.get(key, [])

        estimates.append({
            "hour_utc": key,
            "average_observations_per_recorded_hour": (
                round(mean(values), 2) if values else None
            ),
            "days_with_data": len(values)
        })

    return jsonify({
        "airport_code": airport_code,
        "historical_days_requested": 14,
        "estimates": estimates,
        "note": (
            "These are averages of recorded aircraft observations "
            "per UTC hour, not predictions of flight departures "
            "or arrivals. Sparse history makes estimates unreliable."
        )
    })


# ------------------------------------------------------------
# 9. EXPLAINABLE FLIGHT INSIGHTS
# ------------------------------------------------------------

@app.route("/api/flight-insights/<flight_number>")
def api_flight_insights(flight_number):
    search_value = flight_number.strip().upper()

    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT timestamp, callsign, icao24, altitude, velocity,
               heading, vertical_rate, flight_phase, airport_code
        FROM Flight_Logs
        WHERE UPPER(callsign) = ? OR UPPER(icao24) = ?
        ORDER BY id DESC
        LIMIT 10
    """, (search_value, search_value))

    rows = cursor.fetchall()
    connection.close()

    if not rows:
        return jsonify({
            "found": False,
            "flight_number": search_value,
            "insights": [],
            "message": (
                "No saved observations found. Keep the radar running "
                "to collect history for this aircraft."
            )
        })

    latest = rows[0]
    timestamp, callsign, icao24, altitude, velocity, heading, vertical_rate, phase, airport_code = latest

    insights = []

    if altitude is not None:
        insights.append(
            f"Recorded altitude: {altitude:,.0f} metres."
        )

    if velocity is not None:
        insights.append(
            f"Recorded ground speed: {velocity * 3.6:,.0f} km/h."
        )

    if heading is not None:
        insights.append(
            f"Recorded heading: {heading:.0f} degrees."
        )

    if vertical_rate is not None:
        rate_ft_min = vertical_rate * 196.8504

        if rate_ft_min > 100:
            movement = "climbing"
        elif rate_ft_min < -100:
            movement = "descending"
        else:
            movement = "showing little vertical movement"

        insights.append(
            f"The latest vertical rate indicates the aircraft is "
            f"{movement} (about {rate_ft_min:,.0f} ft/min)."
        )

    if len(rows) >= 2:
        previous = rows[1]

        if (
            altitude is not None
            and previous[3] is not None
        ):
            change = altitude - previous[3]

            insights.append(
                f"Altitude changed by {change:+,.0f} metres "
                "since the previous saved observation."
            )

    return jsonify({
        "found": True,
        "flight_number": callsign or search_value,
        "icao24": icao24,
        "airport_code": airport_code,
        "last_recorded_at": timestamp,
        "flight_phase": phase,
        "observations_returned": len(rows),
        "origin": None,
        "destination": None,
        "insights": insights,
        "note": (
            "Insights describe saved observations. They do not "
            "confirm origin, destination, or flight safety."
        )
    })



# ============================================================
# AVIATION NEWS + AIRCRAFT SPECIFICATIONS
# ============================================================
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

NEWS_CACHE = {"updated_at": 0, "items": []}
NEWS_CACHE_SECONDS = 900
AIRCRAFT_LOOKUP_CACHE = {}
AIRCRAFT_LOOKUP_CACHE_SECONDS = 86400

AVIATION_NEWS_FEEDS = [
    ("Simple Flying", "https://simpleflying.com/feed/"),
    ("EASA", "https://www.easa.europa.eu/en/rss.xml"),
    ("Aviation Week", "https://aviationweek.com/air-transport/airports-networks/rss.xml")
]

# Typical seating ranges are estimates and vary by airline cabin layout.
# Maximum certified capacity is included only where a commonly documented
# figure is available; these are model-level references, not a specific plane's seat map.
AIRCRAFT_SPECS = {
    "A318": {"manufacturer": "Airbus", "model": "A318", "category": "Narrow-body jet", "seats": "107–132 typical", "max_seats": "132", "range_km": 5750, "cruise_kmh": 830, "engines": 2},
    "A319": {"manufacturer": "Airbus", "model": "A319", "category": "Narrow-body jet", "seats": "124–156 typical", "max_seats": "160 (variant/configuration dependent)", "range_km": 6900, "cruise_kmh": 830, "engines": 2},
    "A320": {"manufacturer": "Airbus", "model": "A320 family", "category": "Narrow-body jet", "seats": "140–180 typical", "max_seats": "194 (variant/configuration dependent)", "range_km": 6100, "cruise_kmh": 840, "engines": 2},
    "A20N": {"manufacturer": "Airbus", "model": "A320neo", "category": "Narrow-body jet", "seats": "150–186 typical", "max_seats": "194 (variant/configuration dependent)", "range_km": 6300, "cruise_kmh": 840, "engines": 2},
    "A21N": {"manufacturer": "Airbus", "model": "A321neo", "category": "Narrow-body jet", "seats": "180–220 typical", "max_seats": "244 (certified maximum, configuration dependent)", "range_km": 7400, "cruise_kmh": 840, "engines": 2},
    "A332": {"manufacturer": "Airbus", "model": "A330-200", "category": "Wide-body jet", "seats": "220–260 typical", "max_seats": "Model/configuration dependent", "range_km": 13400, "cruise_kmh": 880, "engines": 2},
    "A333": {"manufacturer": "Airbus", "model": "A330-300", "category": "Wide-body jet", "seats": "250–300 typical", "max_seats": "Model/configuration dependent", "range_km": 11750, "cruise_kmh": 880, "engines": 2},
    "A359": {"manufacturer": "Airbus", "model": "A350-900", "category": "Wide-body jet", "seats": "300–350 typical", "max_seats": "Model/configuration dependent", "range_km": 15000, "cruise_kmh": 903, "engines": 2},
    "A35K": {"manufacturer": "Airbus", "model": "A350-1000", "category": "Wide-body jet", "seats": "350–410 typical", "max_seats": "Model/configuration dependent", "range_km": 16100, "cruise_kmh": 903, "engines": 2},
    "A388": {"manufacturer": "Airbus", "model": "A380-800", "category": "Very large wide-body jet", "seats": "500–600 typical", "max_seats": "853", "range_km": 15200, "cruise_kmh": 903, "engines": 4},
    "B737": {"manufacturer": "Boeing", "model": "737 family", "category": "Narrow-body jet", "seats": "130–189 typical", "max_seats": "Model/variant dependent", "range_km": 5600, "cruise_kmh": 839, "engines": 2},
    "B738": {"manufacturer": "Boeing", "model": "737-800", "category": "Narrow-body jet", "seats": "162–189 typical", "max_seats": "189 (common single-class configuration)", "range_km": 5435, "cruise_kmh": 839, "engines": 2},
    "B38M": {"manufacturer": "Boeing", "model": "737 MAX 8", "category": "Narrow-body jet", "seats": "162–178 typical", "max_seats": "Model/configuration dependent", "range_km": 6570, "cruise_kmh": 839, "engines": 2},
    "B39M": {"manufacturer": "Boeing", "model": "737 MAX 9", "category": "Narrow-body jet", "seats": "178–193 typical", "max_seats": "Model/configuration dependent", "range_km": 6570, "cruise_kmh": 839, "engines": 2},
    "B744": {"manufacturer": "Boeing", "model": "747-400", "category": "Very large wide-body jet", "seats": "416–524 typical", "max_seats": "Model/configuration dependent", "range_km": 13450, "cruise_kmh": 920, "engines": 4},
    "B748": {"manufacturer": "Boeing", "model": "747-8", "category": "Very large wide-body jet", "seats": "410–467 typical", "max_seats": "Model/configuration dependent", "range_km": 14300, "cruise_kmh": 920, "engines": 4},
    "B772": {"manufacturer": "Boeing", "model": "777-200", "category": "Wide-body jet", "seats": "300–400 typical", "max_seats": "Model/configuration dependent", "range_km": 9700, "cruise_kmh": 905, "engines": 2},
    "B77W": {"manufacturer": "Boeing", "model": "777-300ER", "category": "Wide-body jet", "seats": "350–400 typical", "max_seats": "Model/configuration dependent", "range_km": 13650, "cruise_kmh": 905, "engines": 2},
    "B788": {"manufacturer": "Boeing", "model": "787-8", "category": "Wide-body jet", "seats": "242–290 typical", "max_seats": "Model/configuration dependent", "range_km": 13620, "cruise_kmh": 903, "engines": 2},
    "B789": {"manufacturer": "Boeing", "model": "787-9", "category": "Wide-body jet", "seats": "280–320 typical", "max_seats": "Model/configuration dependent", "range_km": 14140, "cruise_kmh": 903, "engines": 2},
    "E190": {"manufacturer": "Embraer", "model": "E190", "category": "Regional jet", "seats": "96–114 typical", "max_seats": "Model/configuration dependent", "range_km": 4500, "cruise_kmh": 829, "engines": 2},
    "E195": {"manufacturer": "Embraer", "model": "E195", "category": "Regional jet", "seats": "100–132 typical", "max_seats": "Model/configuration dependent", "range_km": 4260, "cruise_kmh": 829, "engines": 2},
    "E75L": {"manufacturer": "Embraer", "model": "E175", "category": "Regional jet", "seats": "76–88 typical", "max_seats": "Model/configuration dependent", "range_km": 3980, "cruise_kmh": 829, "engines": 2},
    "CRJ9": {"manufacturer": "Bombardier", "model": "CRJ-900", "category": "Regional jet", "seats": "76–90 typical", "max_seats": "Model/configuration dependent", "range_km": 2876, "cruise_kmh": 830, "engines": 2},
    "DH8D": {"manufacturer": "De Havilland Canada", "model": "Dash 8 Q400", "category": "Turboprop", "seats": "70–78 typical", "max_seats": "Model/configuration dependent", "range_km": 2500, "cruise_kmh": 667, "engines": 2},
    "AT76": {"manufacturer": "ATR", "model": "ATR 72-600", "category": "Turboprop", "seats": "68–78 typical", "max_seats": "Model/configuration dependent", "range_km": 1528, "cruise_kmh": 510, "engines": 2}
}


def _xml_text(item, names):
    for name in names:
        node = item.find(name)
        if node is not None and node.text:
            return node.text.strip()
    # Atom namespaces
    for node in item.iter():
        if node.tag.split("}")[-1] in names and node.text:
            return node.text.strip()
    return ""


def _news_date(value):
    if not value:
        return ""
    try:
        return parsedate_to_datetime(value).isoformat()
    except Exception:
        return value


@app.route("/api/aviation-news")
def api_aviation_news():
    now = time.time()
    if NEWS_CACHE["items"] and now - NEWS_CACHE["updated_at"] < NEWS_CACHE_SECONDS:
        return jsonify({"items": NEWS_CACHE["items"], "cached": True,
                        "updated_at": datetime.fromtimestamp(NEWS_CACHE["updated_at"], timezone.utc).isoformat()})

    items = []
    for source_name, feed_url in AVIATION_NEWS_FEEDS:
        try:
            response = requests.get(
                feed_url,
                headers={"User-Agent": "GlobalFlightRadar/1.0 (aviation news reader)"},
                timeout=7
            )
            response.raise_for_status()
            root = ET.fromstring(response.content)
            entries = [node for node in root.iter() if node.tag.split("}")[-1] in ("item", "entry")]
            for entry in entries[:12]:
                title = _xml_text(entry, ["title"])
                link = ""
                link_node = next((n for n in entry.iter() if n.tag.split("}")[-1] == "link"), None)
                if link_node is not None:
                    link = (link_node.attrib.get("href") or link_node.text or "").strip()
                if not title or not link or not link.startswith(("https://", "http://")):
                    continue
                summary = _xml_text(entry, ["description", "summary", "content"])
                summary = re.sub(r"<[^>]+>", " ", summary)
                summary = re.sub(r"\s+", " ", summary).strip()
                pubdate = _xml_text(entry, ["pubDate", "published", "updated", "date"])
                category = _xml_text(entry, ["category"]) or "Aviation"
                items.append({"title": title, "url": link, "source": source_name,
                              "published": _news_date(pubdate), "summary": summary[:350],
                              "category": category})
        except Exception as error:
            print(f"Aviation news feed failed ({source_name}): {error}")

    # Deduplicate on article URL and sort newest-first when dates are parseable.
    unique = {}
    for item in items:
        unique[item["url"]] = item
    items = list(unique.values())
    def _sort_key(item):
        try:
            return parsedate_to_datetime(item["published"]).timestamp()
        except Exception:
            try:
                return datetime.fromisoformat(item["published"].replace("Z", "+00:00")).timestamp()
            except Exception:
                return 0
    items.sort(key=_sort_key, reverse=True)
    items = items[:30]
    NEWS_CACHE["items"] = items
    NEWS_CACHE["updated_at"] = now
    return jsonify({"items": items, "cached": False,
                    "updated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                    "message": "News depends on the availability of public RSS feeds." if not items else None})


@app.route("/api/aircraft-details/<icao24>")
def api_aircraft_details(icao24):
    hex_code = re.sub(r"[^0-9a-fA-F]", "", icao24).lower()
    if not re.fullmatch(r"[0-9a-f]{6}", hex_code):
        return jsonify({"error": "A valid six-character ICAO24 hex code is required."}), 400

    now = time.time()
    cached = AIRCRAFT_LOOKUP_CACHE.get(hex_code)
    if cached and now - cached["timestamp"] < AIRCRAFT_LOOKUP_CACHE_SECONDS:
        return jsonify(cached["data"])

    details = {"icao24": hex_code, "registration": None, "type_code": None,
               "model": None, "manufacturer": None, "operator": None,
               "category": None, "typical_seats": None, "maximum_seats": None,
               "range_km": None, "cruise_kmh": None, "engines": None,
               "source": "ADSBdb aircraft registry + local model reference",
               "note": "Seating is a typical model-level estimate, not a verified seat count for this exact aircraft."}
    try:
        response = requests.get(f"https://api.adsbdb.com/v0/aircraft/{hex_code}", timeout=7,
                                headers={"User-Agent": "GlobalFlightRadar/1.0"})
        if response.ok:
            payload = response.json().get("response", {}).get("aircraft", {}) or {}
            details["registration"] = payload.get("reg") or payload.get("registration")
            details["type_code"] = (payload.get("type") or payload.get("icaoType") or "").upper() or None
            details["model"] = payload.get("desc") or payload.get("model") or None
            details["manufacturer"] = payload.get("manufacturer") or None
            details["operator"] = payload.get("owner") or payload.get("operator") or None
            details["category"] = payload.get("category") or None
    except Exception as error:
        print(f"Aircraft registry lookup failed for {hex_code}: {error}")

    spec = AIRCRAFT_SPECS.get(details["type_code"] or "")
    if spec:
        details.update({
            "model": details["model"] or spec["model"],
            "manufacturer": details["manufacturer"] or spec["manufacturer"],
            "category": details["category"] or spec["category"],
            "typical_seats": spec["seats"],
            "maximum_seats": spec["max_seats"],
            "range_km": spec["range_km"],
            "cruise_kmh": spec["cruise_kmh"],
            "engines": spec["engines"]
        })
    details["found"] = bool(details["registration"] or details["type_code"] or details["model"])
    AIRCRAFT_LOOKUP_CACHE[hex_code] = {"timestamp": now, "data": details}
    return jsonify(details)


if __name__ == "__main__":
    setup_database()

    print()
    print("==========================================")
    print("          GLOBAL FLIGHT RADAR")
    print("==========================================")
    print()
    print("Source: OpenSky Network")
    print("Available airports:", ", ".join(AIRPORTS.keys()))
    print("Default airport:", DEFAULT_AIRPORT_CODE)
    print("Geofence:", GEOFENCE_RADIUS, "km")
    print("Airline identification: ENABLED")
    print("Database logging: ENABLED")
    print("OpenSky caching: ENABLED")
    print()
    print("Open http://127.0.0.1:5000")
    print("==========================================")

    app.run(
        debug=False,
        host="127.0.0.1",
        port=5000
    )