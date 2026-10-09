import sqlite3


# ==========================================
# CONNECT TO DATABASE
# ==========================================

connection = sqlite3.connect("flight_tracker.db")

cursor = connection.cursor()


print("======================================")
print("       ALTITUDE HISTORY")
print("======================================")


# ==========================================
# FIND AIRCRAFT WITH ALTITUDE DATA
# ==========================================

cursor.execute("""
    SELECT DISTINCT
        icao24,
        callsign

    FROM Flight_Logs

    WHERE altitude IS NOT NULL

    ORDER BY callsign
""")

aircraft_list = cursor.fetchall()


print(
    "Aircraft available:",
    len(aircraft_list)
)


# ==========================================
# SHOW AVAILABLE AIRCRAFT
# ==========================================

print("\nAvailable aircraft:")

for index, aircraft in enumerate(
    aircraft_list[:30],
    start=1
):

    print(
        index,
        "-",
        aircraft[1],
        "| ICAO24:",
        aircraft[0]
    )


# ==========================================
# CHECK IF DATA EXISTS
# ==========================================

if len(aircraft_list) == 0:

    print("\nNo altitude data found.")

    connection.close()

    exit()


# ==========================================
# USER INPUT
# ==========================================

print("\n--------------------------------------")

print(
    "Enter either the Flight Number"
)

print(
    "OR the ICAO24 code."
)

print("--------------------------------------")

search_value = input(
    "Flight / ICAO24: "
).strip().upper()


# ==========================================
# SEARCH BY CALLSIGN OR ICAO24
# ==========================================

cursor.execute("""
    SELECT
        timestamp,
        callsign,
        icao24,
        latitude,
        longitude,
        altitude,
        flight_phase

    FROM Flight_Logs

    WHERE (
        UPPER(callsign) = ?
        OR UPPER(icao24) = ?
    )

    AND altitude IS NOT NULL

    ORDER BY id ASC

    LIMIT 30
""", (
    search_value,
    search_value
))


records = cursor.fetchall()


# ==========================================
# DISPLAY RESULTS
# ==========================================

print("\n======================================")
print("       AIRCRAFT ALTITUDE HISTORY")
print("======================================")


if not records:

    print(
        "\nNo altitude history found for:",
        search_value
    )

    print(
        "\nMake sure the aircraft appears"
    )

    print(
        "in the list above and has altitude data."
    )


else:

    print(
        "\nFound",
        len(records),
        "altitude record(s)."
    )


    for record in records:

        timestamp = record[0]
        callsign = record[1]
        icao24 = record[2]
        latitude = record[3]
        longitude = record[4]
        altitude = record[5]
        phase = record[6]


        print("\n-----------------------------")

        print(
            "Time:",
            timestamp
        )

        print(
            "Flight:",
            callsign
        )

        print(
            "ICAO24:",
            icao24
        )

        print(
            "Position:",
            latitude,
            longitude
        )

        print(
            "Altitude:",
            round(altitude, 2),
            "meters"
        )

        print(
            "Flight Phase:",
            phase
        )


# ==========================================
# CLOSE DATABASE
# ==========================================

connection.close()


print("\n======================================")
print("Altitude history check complete.")
print("======================================")