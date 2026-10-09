import sqlite3
import os

print("CHECKING DATABASE...")

# Show exactly which database file Python is opening
database_path = os.path.abspath("flight_tracker.db")

print("Database location:")
print(database_path)

# Connect
connection = sqlite3.connect("flight_tracker.db")
cursor = connection.cursor()

print("\nConnected to database!")

# Check whether Flight_Logs exists
cursor.execute("""
    SELECT name
    FROM sqlite_master
    WHERE type='table'
""")

tables = cursor.fetchall()

print("\nTables found:")

for table in tables:
    print("-", table[0])


# Count records
cursor.execute("""
    SELECT COUNT(*)
    FROM Flight_Logs
""")

total_records = cursor.fetchone()[0]

print("\n====================================")
print("TOTAL FLIGHT RECORDS:", total_records)
print("====================================")


# Show latest records
cursor.execute("""
    SELECT
        timestamp,
        callsign,
        latitude,
        longitude,
        altitude,
        flight_phase,
        distance_from_airport
    FROM Flight_Logs
    ORDER BY id DESC
    LIMIT 5
""")

records = cursor.fetchall()

print("\nLATEST 5 RECORDS:")

for record in records:

    print("\n-----------------------------")

    print("Time:", record[0])
    print("Flight:", record[1])
    print("Position:", record[2], record[3])
    print("Altitude:", record[4])
    print("Phase:", record[5])
    print("Distance:", round(record[6], 2), "km")


connection.close()

print("\n====================================")
print("DATABASE CHECK COMPLETE")
print("====================================")