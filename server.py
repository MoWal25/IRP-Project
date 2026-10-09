from flask import Flask, jsonify, send_from_directory, request
import requests
import math
import sqlite3
import os
import time
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

    for column in ("airline", "origin", "destination"):
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
                timestamp,
                icao24,
                callsign,
                country,
                latitude,
                longitude,
                altitude,
                velocity,
                heading,
                vertical_rate,
                flight_phase,
                distance_from_airport,
                airline,
                origin,
                destination
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            timestamp,
            aircraft["icao24"],
            aircraft["callsign"],
            aircraft["country"],
            aircraft["latitude"],
            aircraft["longitude"],
            aircraft["altitude"],
            aircraft["velocity"],
            aircraft["heading"],
            aircraft["vertical_rate"],
            aircraft["flight_phase"],
            aircraft["distance_from_airport"],
            aircraft["airline"],
            aircraft["origin"],
            aircraft["destination"]
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
# STARTUP
# ============================================================

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