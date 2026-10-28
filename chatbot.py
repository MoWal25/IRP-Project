"""
chatbot.py  -  Flight Radar AI assistant (Google Gemini)
=========================================================

Answers questions about flights using retrieval through TOOLS
(a "RAG" pattern for structured data):

    user question
        -> Gemini decides which tool(s) to call
        -> tools query the live radar, the SQLite history, the
           airport boards or a route database
        -> Gemini reads the real data and writes the answer

Why tools instead of a vector database?
Your data is structured (numbers, callsigns, timestamps), so exact
lookups and SQL aggregates are far more accurate than embeddings.

Setup:
    pip install google-genai
    set GEMINI_API_KEY=your_key        (Mac/Linux: export GEMINI_API_KEY=...)
    Free key: https://aistudio.google.com/apikey

Hooked into server.py by register_chatbot(...) (see bottom of this file).
"""

import os
import re
import sqlite3
from typing import Optional

import requests
from flask import Blueprint, request, jsonify, send_from_directory

try:
    from google import genai
    from google.genai import types
except ImportError:  # reported cleanly by /api/chat
    genai = None
    types = None


def _api_key():
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


# Main model. Override with the CHAT_MODEL environment variable if you like.
CHAT_MODEL = os.getenv("CHAT_MODEL", "gemini-3.5-flash")

# If the main model returns "not found" (Google retires models regularly),
# these are tried in order automatically.
FALLBACK_MODELS = ["gemini-3.1-flash-lite", "gemini-flash-latest"]
MODEL_CANDIDATES = [CHAT_MODEL] + [m for m in FALLBACK_MODELS if m != CHAT_MODEL]

print("Chatbot models (tried in order):", ", ".join(MODEL_CANDIDATES))

MAX_TOOL_ROUNDS = 8
MAX_SQL_ROWS = 100
MAX_HISTORY_MESSAGES = 12

chat_bp = Blueprint("chatbot", __name__)

# Filled in by register_chatbot()
_CONFIG = {
    "database": "flight_tracker.db",
    "get_live_aircraft": None,   # callable(airport_code) -> (aircraft_list, using_cache)
    "get_airport_board": None,   # callable(airport_code) -> (board_dict, cached)
    "airports": {},              # {"LHR": {"name":..., "lat":..., "lon":...}, ...}
    "default_airport": "LHR",
    "radius_km": 100,
}


# ============================================================
# SYSTEM PROMPT
# ============================================================

def build_system_prompt(viewing_airport=None):
    airports = _CONFIG["airports"]
    airport_lines = "\n".join(
        f"  - {code}: {a['name']}" for code, a in airports.items()
    ) or "  (none configured)"

    viewing = ""
    if viewing_airport and viewing_airport in airports:
        viewing = (
            f"\nThe user currently has {viewing_airport} ({airports[viewing_airport]['name']}) open on the map. "
            "When they say 'here', 'overhead' or 'now' without naming an airport, they mean this one."
        )

    return f"""You are the AI assistant inside a live flight radar dashboard.
It tracks aircraft within {_CONFIG['radius_km']} km of these airports using the OpenSky Network:
{airport_lines}
Every time someone loads the radar, the aircraft in range are logged to a SQLite history database.{viewing}

You answer questions about flight numbers (callsigns), aircraft (ICAO24 codes), airlines, altitude, speed,
climbing/descending, traffic counts, routes, airport arrivals/departures and the logged history.
ALWAYS use your tools to get facts. Never invent flight data. You may call several tools for one question.

CHOOSING TOOLS
- Is it flying right now / what is overhead / current counts      -> get_live_flights
- Where is flight X coming from or going to (route)               -> lookup_route (scheduled route for that callsign)
- Scheduled arrivals/departures soon at an airport                -> get_airport_board
- Everything about a flight in the logs / its track               -> search_flights, then get_flight_history
- Totals, rankings, averages, "most", "busiest", per-hour counts  -> get_database_overview or run_sql

DATA FACTS
- Table Flight_Logs columns: id, timestamp (UTC ISO text), icao24, callsign, country (country of registration),
  latitude, longitude, altitude (METRES, barometric; NULL for many rows), velocity (METRES/SECOND), heading (degrees),
  vertical_rate (m/s), flight_phase (CLIMBING / DESCENDING / CRUISING / UNKNOWN), distance_from_airport (km),
  airline, origin, destination, airport_code (which tracked airport it was near; may be NULL on very old rows).
- origin/destination columns in the history are normally NULL because the position feed has no route data.
  Use lookup_route for routes, and say clearly that it is the SCHEDULED route for the callsign, not confirmed live.
- Airline is derived from the first 3 letters of the callsign. 'Unknown Operator' means that prefix isn't in the lookup table;
  callsign may be empty/'Unknown' for some aircraft.
- The log has one row per aircraft per refresh, so counting rows is NOT counting aircraft: use COUNT(DISTINCT icao24).
- flight_phase is a rough label from vertical rate; UNKNOWN is common (no vertical rate reported).
- Many aircraft report no altitude. When averaging or ranking altitude, ignore NULLs and mention that.

STYLE
- Convert units for the reader: altitude in feet (1 m = 3.281 ft) with metres in brackets, speed in knots (1 m/s = 1.944 kn).
- Be concise and direct. Use short bullet lists for several flights. Mention the data timestamp when it matters.
- If nothing is found, say so plainly and suggest what to try (spelling, different airport, flight may not have been in range).
- Politely decline questions unrelated to flights or this radar."""


# ============================================================
# HELPERS
# ============================================================

def _ro_connection():
    """Read-only connection - the chatbot can never modify your data."""
    path = os.path.abspath(_CONFIG["database"])
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _table_columns(conn):
    return {r[1] for r in conn.execute("PRAGMA table_info(Flight_Logs)").fetchall()}


def _round(value, digits=1):
    return round(value, digits) if isinstance(value, (int, float)) else value


def _clamp(value, default, maximum):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, maximum))


def _resolve_airport(code):
    """Return a valid configured airport code, or None if unknown."""
    airports = _CONFIG["airports"]
    if not code:
        return None
    code = str(code).strip().upper()
    return code if code in airports else None


def _compact(aircraft):
    """Trim a live aircraft dict to what the LLM needs."""
    return {
        "callsign": aircraft.get("callsign"),
        "icao24": aircraft.get("icao24"),
        "airline": aircraft.get("airline"),
        "country": aircraft.get("country"),
        "altitude_m": _round(aircraft.get("altitude")),
        "velocity_ms": _round(aircraft.get("velocity")),
        "heading_deg": _round(aircraft.get("heading")),
        "vertical_rate_ms": _round(aircraft.get("vertical_rate")),
        "flight_phase": aircraft.get("flight_phase"),
        "distance_from_airport_km": _round(aircraft.get("distance_from_airport")),
        "on_ground": aircraft.get("on_ground"),
        "latitude": _round(aircraft.get("latitude"), 4),
        "longitude": _round(aircraft.get("longitude"), 4),
    }


# ============================================================
# TOOL IMPLEMENTATIONS
# ============================================================

def tool_get_live_flights(airport=None, filter_text=None, flight_phase=None, sort_by=None, limit=15):
    getter = _CONFIG["get_live_aircraft"]
    if getter is None:
        return {"error": "Live feed is not connected to the chatbot."}

    code = _resolve_airport(airport) or _CONFIG["default_airport"]

    try:
        aircraft, using_cache = getter(code)
    except Exception as error:
        return {"error": f"Live feed unavailable for {code}: {error}"}

    results = list(aircraft)

    if filter_text:
        needle = filter_text.strip().lower()
        results = [
            a for a in results
            if needle in str(a.get("callsign", "")).lower()
            or needle in str(a.get("airline", "")).lower()
            or needle in str(a.get("icao24", "")).lower()
            or needle in str(a.get("country", "")).lower()
        ]

    if flight_phase:
        results = [a for a in results if a.get("flight_phase") == flight_phase.upper()]

    if sort_by in ("altitude", "velocity", "distance_from_airport"):
        results.sort(key=lambda a: (a.get(sort_by) is not None, a.get(sort_by) or 0), reverse=True)

    limit = _clamp(limit, 15, 50)

    return {
        "airport": code,
        "total_matching": len(results),
        "total_in_range": len(aircraft),
        "cached_data": using_cache,
        "note": "Data may be a few seconds to a minute old. Cached data means the OpenSky rate limit was hit.",
        "aircraft": [_compact(a) for a in results[:limit]],
    }


def tool_search_flights(query, airport=None, limit=10):
    query = (query or "").strip().upper()
    if not query:
        return {"error": "Empty query."}

    limit = _clamp(limit, 10, 30)
    like = f"%{query}%"
    code = _resolve_airport(airport)

    conn = _ro_connection()
    try:
        columns = _table_columns(conn)
        airline_filter = "OR UPPER(airline) LIKE :q" if "airline" in columns else ""
        airline_rank = "OR UPPER(airline) LIKE :q" if "airline" in columns else ""
        airport_filter = ""
        params = {"q": like, "limit": limit}
        if code and "airport_code" in columns:
            airport_filter = "AND airport_code = :airport"
            params["airport"] = code

        rows = conn.execute(f"""
            SELECT
                icao24,
                MAX(callsign)  AS callsign,
                {"MAX(airline)" if "airline" in columns else "NULL"} AS airline,
                MAX(country)   AS country,
                COUNT(*)       AS records,
                MIN(timestamp) AS first_seen,
                MAX(timestamp) AS last_seen,
                MAX(altitude)  AS max_altitude_m,
                MAX(velocity)  AS max_velocity_ms
            FROM Flight_Logs
            WHERE (
                   UPPER(callsign) LIKE :q
                OR UPPER(icao24)   LIKE :q
                OR UPPER(country)  LIKE :q
                {airline_filter}
            )
            {airport_filter}
            GROUP BY icao24
            ORDER BY
                MAX(UPPER(callsign) LIKE :q OR UPPER(icao24) LIKE :q {airline_rank}) DESC,
                last_seen DESC
            LIMIT :limit
        """, params).fetchall()

        results = []
        for r in rows:
            latest = conn.execute("""
                SELECT altitude, velocity, heading, flight_phase,
                       distance_from_airport, latitude, longitude
                FROM Flight_Logs WHERE icao24 = ?
                ORDER BY id DESC LIMIT 1
            """, (r["icao24"],)).fetchone()

            results.append({
                "icao24": r["icao24"],
                "callsign": r["callsign"],
                "airline": r["airline"],
                "country": r["country"],
                "records": r["records"],
                "first_seen": r["first_seen"],
                "last_seen": r["last_seen"],
                "max_altitude_m": _round(r["max_altitude_m"]),
                "max_velocity_ms": _round(r["max_velocity_ms"]),
                "latest": {
                    "altitude_m": _round(latest["altitude"]),
                    "velocity_ms": _round(latest["velocity"]),
                    "heading_deg": _round(latest["heading"]),
                    "flight_phase": latest["flight_phase"],
                    "distance_from_airport_km": _round(latest["distance_from_airport"]),
                    "latitude": _round(latest["latitude"], 4),
                    "longitude": _round(latest["longitude"], 4),
                } if latest else None,
            })

        return {"query": query, "aircraft_found": len(results), "results": results}
    finally:
        conn.close()


def tool_get_flight_history(identifier, max_points=15):
    identifier = (identifier or "").strip().upper()
    if not identifier:
        return {"error": "Empty identifier."}

    max_points = _clamp(max_points, 15, 60)

    conn = _ro_connection()
    try:
        rows = conn.execute("""
            SELECT timestamp, callsign, icao24, latitude, longitude, altitude,
                   velocity, heading, vertical_rate, flight_phase, distance_from_airport
            FROM Flight_Logs
            WHERE UPPER(callsign) = :id OR UPPER(icao24) = :id
            ORDER BY id ASC
            LIMIT 5000
        """, {"id": identifier}).fetchall()

        if not rows:
            return {"found": False, "identifier": identifier,
                    "hint": "No exact match. Try search_flights for partial matches."}

        alts = [r["altitude"] for r in rows if r["altitude"] is not None]
        speeds = [r["velocity"] for r in rows if r["velocity"] is not None]
        dists = [r["distance_from_airport"] for r in rows if r["distance_from_airport"] is not None]

        # Evenly sample points across the track
        step = max(1, len(rows) // max_points)
        sampled = rows[::step][:max_points]
        if rows[-1] not in sampled:
            sampled.append(rows[-1])

        return {
            "found": True,
            "identifier": identifier,
            "callsign": rows[0]["callsign"],
            "icao24": rows[0]["icao24"],
            "record_count": len(rows),
            "first_seen": rows[0]["timestamp"],
            "last_seen": rows[-1]["timestamp"],
            "altitude_m": {"min": _round(min(alts)), "max": _round(max(alts))} if alts else None,
            "max_velocity_ms": _round(max(speeds)) if speeds else None,
            "closest_approach_km": _round(min(dists)) if dists else None,
            "phases_seen": sorted({r["flight_phase"] for r in rows if r["flight_phase"]}),
            "sample_points": [
                {
                    "time": r["timestamp"],
                    "lat": _round(r["latitude"], 4),
                    "lon": _round(r["longitude"], 4),
                    "altitude_m": _round(r["altitude"]),
                    "velocity_ms": _round(r["velocity"]),
                    "phase": r["flight_phase"],
                    "distance_km": _round(r["distance_from_airport"]),
                }
                for r in sampled
            ],
        }
    finally:
        conn.close()


def tool_get_database_overview(airport=None):
    code = _resolve_airport(airport)
    conn = _ro_connection()
    try:
        columns = _table_columns(conn)
        where, params = "", {}
        if code and "airport_code" in columns:
            where, params = "WHERE airport_code = :airport", {"airport": code}

        total, unique, first, last = conn.execute(f"""
            SELECT COUNT(*), COUNT(DISTINCT icao24), MIN(timestamp), MAX(timestamp)
            FROM Flight_Logs {where}
        """, params).fetchone()

        phases = {
            r["flight_phase"]: r["n"]
            for r in conn.execute(f"""
                SELECT flight_phase, COUNT(DISTINCT icao24) AS n
                FROM Flight_Logs {where} GROUP BY flight_phase
            """, params)
        }

        extra_where = (where + " AND " if where else "WHERE ")
        top_airlines = [
            {"airline": r["airline"], "aircraft": r["n"]}
            for r in conn.execute(f"""
                SELECT airline, COUNT(DISTINCT icao24) AS n
                FROM Flight_Logs
                {extra_where} airline IS NOT NULL AND airline != 'Unknown Operator'
                GROUP BY airline ORDER BY n DESC LIMIT 8
            """, params)
        ] if "airline" in columns else []

        by_airport = {}
        if "airport_code" in columns and not code:
            by_airport = {
                (r["airport_code"] or "unassigned"): {"aircraft": r["n"], "records": r["rec"]}
                for r in conn.execute("""
                    SELECT airport_code, COUNT(DISTINCT icao24) AS n, COUNT(*) AS rec
                    FROM Flight_Logs GROUP BY airport_code
                """)
            }

        return {
            "airport_filter": code,
            "total_records": total,
            "unique_aircraft": unique,
            "first_record": first,
            "last_record": last,
            "aircraft_by_phase": phases,
            "top_airlines_excluding_unknown": top_airlines,
            "by_airport": by_airport,
        }
    finally:
        conn.close()


_FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum|reindex|truncate)\b",
    re.IGNORECASE,
)


def tool_run_sql(sql):
    sql = (sql or "").strip().rstrip(";").strip()

    if not sql.lower().startswith(("select", "with")):
        return {"error": "Only SELECT queries are allowed."}
    if ";" in sql:
        return {"error": "Only a single statement is allowed."}
    if _FORBIDDEN_SQL.search(sql):
        return {"error": "Query contains a forbidden keyword. Read-only SELECT only."}

    conn = _ro_connection()
    try:
        cursor = conn.execute(sql)
        rows = cursor.fetchmany(MAX_SQL_ROWS + 1)
        truncated = len(rows) > MAX_SQL_ROWS
        rows = rows[:MAX_SQL_ROWS]
        return {
            "columns": [d[0] for d in cursor.description] if cursor.description else [],
            "rows": [list(r) for r in rows],
            "row_count": len(rows),
            "truncated": truncated,
        }
    except sqlite3.Error as error:
        return {"error": f"SQL error: {error}"}
    finally:
        conn.close()


def _airport_summary(info):
    if not isinstance(info, dict):
        return None
    return {
        "iata": info.get("iata_code"),
        "icao": info.get("icao_code"),
        "name": info.get("name"),
        "city": info.get("municipality"),
        "country": info.get("country_name"),
    }


def tool_lookup_route(callsign):
    """Scheduled route for a callsign via the free adsbdb.com API."""
    callsign = (callsign or "").strip().upper().replace(" ", "")
    if not re.fullmatch(r"[A-Z0-9]{2,10}", callsign):
        return {"error": "That doesn't look like a valid callsign / flight number."}

    try:
        response = requests.get(f"https://api.adsbdb.com/v0/callsign/{callsign}", timeout=8)
        if response.status_code == 404:
            return {"found": False, "callsign": callsign, "note": "No scheduled route known for this callsign."}
        response.raise_for_status()
        body = response.json().get("response")
    except Exception as error:
        return {"error": f"Route lookup unavailable: {error}"}

    route = body.get("flightroute") if isinstance(body, dict) else None
    if not route:
        return {"found": False, "callsign": callsign, "note": "No scheduled route known for this callsign."}

    airline = route.get("airline") or {}
    return {
        "found": True,
        "callsign": callsign,
        "airline": airline.get("name"),
        "origin": _airport_summary(route.get("origin")),
        "destination": _airport_summary(route.get("destination")),
        "note": "Scheduled route for this callsign (adsbdb.com). Not confirmed live; airlines sometimes reuse callsigns.",
    }


def _board_flight(item):
    """Trim one AeroDataBox FIDS entry."""
    if not isinstance(item, dict):
        return None
    movement = item.get("movement") or {}
    other = movement.get("airport") or {}
    scheduled = movement.get("scheduledTime") or movement.get("scheduledTimeLocal") or {}
    revised = movement.get("revisedTime") or {}
    return {
        "flight": item.get("number"),
        "status": item.get("status"),
        "airline": (item.get("airline") or {}).get("name"),
        "aircraft": (item.get("aircraft") or {}).get("model"),
        "other_airport": other.get("name") or other.get("iata"),
        "other_airport_iata": other.get("iata"),
        "scheduled_local": scheduled.get("local") if isinstance(scheduled, dict) else scheduled,
        "revised_local": revised.get("local") if isinstance(revised, dict) else revised,
        "terminal": movement.get("terminal"),
        "gate": movement.get("gate"),
    }


def tool_get_airport_board(airport=None):
    getter = _CONFIG["get_airport_board"]
    if getter is None:
        return {"error": "Airport boards are not connected to the chatbot."}

    code = _resolve_airport(airport) or _CONFIG["default_airport"]

    try:
        board, cached = getter(code)
    except Exception as error:
        return {"error": f"Airport board unavailable for {code}: {error}"}

    if not board.get("configured", True):
        return {"airport": code, "available": False,
                "message": board.get("message", "Airport schedules are not configured (AERODATABOX_API_KEY missing).")}

    arrivals = [f for f in map(_board_flight, board.get("arrivals", [])) if f][:15]
    departures = [f for f in map(_board_flight, board.get("departures", [])) if f][:15]

    return {
        "airport": code,
        "available": True,
        "window_local": f"{board.get('window_start')} to {board.get('window_end')} ({board.get('timezone')})",
        "arrival_count": len(board.get("arrivals", [])),
        "departure_count": len(board.get("departures", [])),
        "arrivals": arrivals,
        "departures": departures,
    }


def execute_tool(function, **arguments):
    """Run a tool and turn any failure into data Gemini can read."""
    try:
        return function(**arguments)
    except sqlite3.OperationalError as error:
        return {"error": f"Database error: {error}"}
    except TypeError as error:
        return {"error": f"Bad arguments: {error}"}
    except Exception as error:
        return {"error": f"Tool failed: {error}"}


# ============================================================
# TOOLS EXPOSED TO GEMINI
# ============================================================
# Gemini builds each tool's schema from the function name,
# type hints and docstring below - keep them accurate.

def make_tools(tools_used):
    """Create the tool functions for one request, logging which get called."""

    def get_live_flights(
        airport: Optional[str] = None,
        filter_text: Optional[str] = None,
        flight_phase: Optional[str] = None,
        sort_by: Optional[str] = None,
        limit: int = 15,
    ) -> dict:
        """Get aircraft CURRENTLY in range of an airport from the live radar feed.
        Use for 'what is overhead now', 'is BAW123 flying right now',
        'how many planes are descending at Dubai'.

        Args:
            airport: Airport code to check (LHR, DXB or JED). Defaults to the airport the user is viewing.
            filter_text: Optional partial match on callsign, airline, icao24 or country.
            flight_phase: Optional. One of CLIMBING, DESCENDING, CRUISING, UNKNOWN.
            sort_by: Optional, sorts descending. One of altitude, velocity, distance_from_airport.
            limit: Max aircraft to return (default 15, max 50).
        """
        tools_used.append("get_live_flights")
        return execute_tool(tool_get_live_flights, airport=airport, filter_text=filter_text,
                            flight_phase=flight_phase, sort_by=sort_by, limit=limit)

    def search_flights(query: str, airport: Optional[str] = None, limit: int = 10) -> dict:
        """Search the logged HISTORY for aircraft matching a callsign / flight number,
        ICAO24, airline name or country (partial, case-insensitive). Returns one
        summary per aircraft: first/last seen, record count, max altitude, latest state.

        Args:
            query: e.g. 'BAW186', '4ca7b1', 'Emirates', 'UAE'.
            airport: Optional airport code (LHR, DXB, JED) to restrict results to.
            limit: Max aircraft to return (default 10, max 30).
        """
        tools_used.append("search_flights")
        return execute_tool(tool_search_flights, query=query, airport=airport, limit=limit)

    def get_flight_history(identifier: str, max_points: int = 15) -> dict:
        """Get the logged track for ONE flight (exact callsign or ICAO24): summary
        statistics (min/max altitude, max speed, phases seen, time span) plus
        sampled points.

        Args:
            identifier: Exact callsign or ICAO24.
            max_points: Max sample points to return (default 15, max 60).
        """
        tools_used.append("get_flight_history")
        return execute_tool(tool_get_flight_history, identifier=identifier, max_points=max_points)

    def get_database_overview(airport: Optional[str] = None) -> dict:
        """Overview of the history database: total rows, unique aircraft, time range,
        top airlines, aircraft per flight phase and per airport.

        Args:
            airport: Optional airport code (LHR, DXB, JED) to restrict the overview to.
        """
        tools_used.append("get_database_overview")
        return execute_tool(tool_get_database_overview, airport=airport)

    def run_sql(sql: str) -> dict:
        """Run a READ-ONLY SQLite SELECT on the Flight_Logs table for analytics the
        other tools cannot answer (busiest airline, average altitude by phase,
        flights per hour, per-airport comparisons). Single SELECT only; results
        capped at 100 rows. Remember altitude is metres and velocity is m/s.

        Args:
            sql: A single SELECT statement.
        """
        tools_used.append("run_sql")
        return execute_tool(tool_run_sql, sql=sql)

    def lookup_route(callsign: str) -> dict:
        """Look up the SCHEDULED route (origin and destination airports) of a flight
        number / callsign such as 'BAW186'. Use when asked where a flight is coming
        from or going to.

        Args:
            callsign: The flight callsign, e.g. 'BAW186' or 'UAE202'.
        """
        tools_used.append("lookup_route")
        return execute_tool(tool_lookup_route, callsign=callsign)

    def get_airport_board(airport: Optional[str] = None) -> dict:
        """Get scheduled arrivals and departures at an airport for the next 30 minutes.

        Args:
            airport: Airport code (LHR, DXB or JED). Defaults to the airport the user is viewing.
        """
        tools_used.append("get_airport_board")
        return execute_tool(tool_get_airport_board, airport=airport)

    return [get_live_flights, search_flights, get_flight_history,
            get_database_overview, run_sql, lookup_route, get_airport_board]


# ============================================================
# CHAT
# ============================================================

def run_chat(messages, viewing_airport=None):
    """messages: [{"role": "user"|"assistant", "content": "text"}, ...]"""
    client = genai.Client(api_key=_api_key())

    contents = [
        types.Content(
            role="user" if m["role"] == "user" else "model",
            parts=[types.Part(text=m["content"])],
        )
        for m in messages
    ]

    last_error = None

    # Try the main model first; if Google says it no longer exists (404),
    # move on to the next candidate automatically.
    for model in MODEL_CANDIDATES:
        tools_used = []

        config = types.GenerateContentConfig(
            system_instruction=build_system_prompt(viewing_airport),
            tools=make_tools(tools_used),
            temperature=0.3,
            # The SDK runs the call -> tool -> answer loop for us.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                maximum_remote_calls=MAX_TOOL_ROUNDS
            ),
        )

        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
        except Exception as error:
            if getattr(error, "code", None) == 404:
                print(f"Model '{model}' not found, trying the next one...")
                last_error = error
                continue
            raise

        print(f"Answered using model: {model}")
        text = (response.text or "").strip()
        if not text:
            text = "I couldn't produce an answer. Try rephrasing your question."
        return text, tools_used

    # Every candidate returned 404
    raise last_error


def _clean_history(raw):
    clean = []
    for m in raw[-MAX_HISTORY_MESSAGES:]:
        role, content = m.get("role"), m.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            clean.append({"role": role, "content": content[:4000]})
    # Must start with a user turn
    while clean and clean[0]["role"] != "user":
        clean.pop(0)
    return clean


# ============================================================
# ROUTES
# ============================================================

@chat_bp.route("/chat-widget.js")
def chat_widget_js():
    """Self-contained widget used by map.html and 3d.html."""
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "chat_widget.js")


@chat_bp.route("/api/chat", methods=["POST"])
def api_chat():
    if genai is None:
        return jsonify({"error": "The 'google-genai' package is not installed. Run: pip install google-genai"}), 500
    if not _api_key():
        return jsonify({"error": "GEMINI_API_KEY is not set. Get a free key at aistudio.google.com/apikey, set it in your terminal, then restart server.py."}), 500

    body = request.get_json(silent=True) or {}
    messages = _clean_history(body.get("messages", []))

    if not messages or messages[-1]["role"] != "user":
        return jsonify({"error": "No question received."}), 400

    viewing_airport = _resolve_airport(body.get("airport"))

    try:
        answer, tools_used = run_chat(messages, viewing_airport)
        return jsonify({"answer": answer, "tools_used": tools_used})
    except Exception as error:
        print("Chatbot error:", error)
        code = getattr(error, "code", None)
        message = str(error)
        if code == 429:
            return jsonify({"error": "Gemini rate limit / quota reached. Wait a minute and try again."}), 429
        if code in (400, 401, 403) and "API key" in message:
            return jsonify({"error": "Invalid GEMINI_API_KEY."}), 401
        if code == 404:
            return jsonify({"error": f"None of the models {MODEL_CANDIDATES} were found for your key. Check the model names available to your key."}), 500
        return jsonify({"error": f"Chatbot error: {message[:300]}"}), 500


# ============================================================
# REGISTER
# ============================================================

def register_chatbot(app, database, get_live_aircraft, airports, default_airport="LHR",
                     radius_km=100, get_airport_board=None):
    _CONFIG.update({
        "database": database,
        "get_live_aircraft": get_live_aircraft,
        "get_airport_board": get_airport_board,
        "airports": airports,
        "default_airport": default_airport,
        "radius_km": radius_km,
    })
    app.register_blueprint(chat_bp)