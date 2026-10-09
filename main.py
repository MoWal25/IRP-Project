import requests
import sqlite3
import time
from datetime import datetime, timezone
from math import radians, sin, cos, sqrt, atan2


# ==========================================
# DISTANCE CALCULATION
# ==========================================

def calculate_distance(lat1, lon1, lat2, lon2):

    R = 6371

    lat1 = radians(lat1)
    lon1 = radians(lon1)
    lat2 = radians(lat2)
    lon2 = radians(lon2)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        sin(dlat / 2) ** 2
        + cos(lat1) * cos(lat2)
        * sin(dlon / 2) ** 2
    )

    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    return R * c


# ==========================================
# FLIGHT PHASE
# ==========================================

def determine_flight_phase(vertical_rate):

    if vertical_rate is None:
        return "UNKNOWN"

    if vertical_rate > 0.5:
        return "CLIMBING"

    elif vertical_rate < -0.5:
        return "DESCENDING"

    else:
        return "CRUISING"


# ==========================================
# AIRCRAFT CLASS
# ==========================================

class Aircraft:

    def __init__(self, data):

        self.icao24 = data[0]

        self.callsign = (
            data[1].strip()
            if data[1]
            else "Unknown"
        )

        self.country = data[2]

        self.longitude = data[5]
        self.latitude = data[6]

        self.altitude = data[7]
        self.velocity = data[9]
        self.heading = data[10]
        self.vertical_rate = data[11]

        self.flight_phase = determine_flight_phase(
            self.vertical_rate
        )


# ==========================================
# DATABASE
# ==========================================

def create_database():

    connection = sqlite3.connect("flight_tracker.db")

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

            distance_from_airport REAL
        )
    """)

    connection.commit()

    return connection


# ==========================================
# SAVE FLIGHT
# ==========================================

def save_flight(connection, aircraft, distance):

    cursor = connection.cursor()

    timestamp = datetime.now(
        timezone.utc
    ).isoformat()

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
            distance_from_airport

        )

        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (

        timestamp,
        aircraft.icao24,
        aircraft.callsign,
        aircraft.country,
        aircraft.latitude,
        aircraft.longitude,
        aircraft.altitude,
        aircraft.velocity,
        aircraft.heading,
        aircraft.vertical_rate,
        aircraft.flight_phase,
        distance
    ))

    connection.commit()


# ==========================================
# MAIN SETTINGS
# ==========================================

url = "https://opensky-network.org/api/states/all"


# Heathrow Airport
airport_lat = 51.4700
airport_lon = -0.4543

GEOFENCE_RADIUS = 100


# ==========================================
# GHOST DETECTION SETTINGS
# ==========================================

# Stores aircraft from the previous scan
previous_aircraft = set()

# How many consecutive scans an aircraft
# can disappear before we call it a ghost
GHOST_THRESHOLD = 2


# Keeps track of missing scans
missing_aircraft = {}


# ==========================================
# DATABASE CONNECTION
# ==========================================

connection = create_database()


print("======================================")
print("       GLOBAL FLIGHT TRACKER")
print("======================================")

print("Database connected.")
print("Tracking aircraft within 100 km of LHR.")
print("Ghost detection enabled.")
print("Press CTRL + C to stop.\n")


# ==========================================
# CONTINUOUS TRACKING
# ==========================================

try:

    while True:

        print("\n")
        print("######################################")
        print("          NEW AIRCRAFT SCAN")
        print("######################################")


        # ==================================
        # GET API DATA
        # ==================================

        response = requests.get(
            url,
            timeout=20
        )

        print("API Status:", response.status_code)

        data = response.json()

        print(
            "Total aircraft received:",
            len(data["states"])
        )


        # ==================================
        # CURRENT AIRCRAFT
        # ==================================

        current_aircraft = set()


        # ==================================
        # PROCESS AIRCRAFT
        # ==================================

        aircraft_inside = 0


        for flight_data in data["states"]:

            aircraft = Aircraft(flight_data)


            # Need position
            if (
                aircraft.latitude is None
                or aircraft.longitude is None
            ):
                continue


            # Add aircraft to current scan
            current_aircraft.add(
                aircraft.icao24
            )


            # Calculate distance
            distance = calculate_distance(

                airport_lat,
                airport_lon,

                aircraft.latitude,
                aircraft.longitude
            )


            # ==================================
            # GEOFENCE
            # ==================================

            if distance <= GEOFENCE_RADIUS:

                aircraft_inside += 1


                print("\n-----------------------------")

                print(
                    "Flight:",
                    aircraft.callsign
                )

                print(
                    "ICAO24:",
                    aircraft.icao24
                )

                print(
                    "Position:",
                    aircraft.latitude,
                    aircraft.longitude
                )

                print(
                    "Distance:",
                    round(distance, 2),
                    "km"
                )

                print(
                    "Altitude:",
                    aircraft.altitude
                )

                print(
                    "Speed:",
                    aircraft.velocity
                )

                print(
                    "Heading:",
                    aircraft.heading
                )

                print(
                    "Flight Phase:",
                    aircraft.flight_phase
                )


                # ==================================
                # TELEMETRY STATUS
                # ==================================

                if aircraft.altitude is None:

                    print(
                        "Telemetry Status:",
                        "MISSING ALTITUDE"
                    )

                else:

                    print(
                        "Telemetry Status:",
                        "NORMAL"
                    )


                # Save to database
                save_flight(
                    connection,
                    aircraft,
                    distance
                )


        # ==================================
        # GHOST DETECTION
        # ==================================

        disappeared_aircraft = (
            previous_aircraft - current_aircraft
        )


        if disappeared_aircraft:

            print("\n")
            print("######################################")
            print("       AIRCRAFT TELEMETRY CHECK")
            print("######################################")


        for icao24 in disappeared_aircraft:

            # Increase missing count
            missing_aircraft[icao24] = (
                missing_aircraft.get(
                    icao24,
                    0
                ) + 1
            )


            missing_count = missing_aircraft[icao24]


            print(
                "Aircraft",
                icao24,
                "missing for",
                missing_count,
                "scan(s)"
            )


            # ==================================
            # GHOST AIRCRAFT
            # ==================================

            if missing_count >= GHOST_THRESHOLD:

                print("\n🚨 GHOST AIRCRAFT DETECTED!")

                print(
                    "ICAO24:",
                    icao24
                )

                print(
                    "Missing scans:",
                    missing_count
                )

                print(
                    "Status:",
                    "TELEMETRY LOST"
                )


        # ==================================
        # RESET MISSING COUNTERS
        # ==================================

        for icao24 in list(missing_aircraft):

            if icao24 in current_aircraft:

                del missing_aircraft[icao24]


        # ==================================
        # UPDATE PREVIOUS SCAN
        # ==================================

        previous_aircraft = current_aircraft


        # ==================================
        # SCAN SUMMARY
        # ==================================

        print("\n======================================")

        print(
            "Aircraft inside geofence:",
            aircraft_inside
        )

        print(
            "Aircraft in current scan:",
            len(current_aircraft)
        )

        print(
            "Potential missing aircraft:",
            len(missing_aircraft)
        )

        print(
            "Scan completed:",
            datetime.now().strftime("%H:%M:%S")
        )

        print("======================================")


        # ==================================
        # WAIT
        # ==================================

        print(
            "\nNext scan in 20 seconds..."
        )

        time.sleep(20)


except KeyboardInterrupt:

    print("\n\nTracker stopped.")


finally:

    connection.close()

    print(
        "Database connection closed."
    )