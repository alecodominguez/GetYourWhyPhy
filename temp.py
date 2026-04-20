import sqlite3
from locations import get_standard_name


def clean_database():
    # Connect to your SQLite database in the /data folder
    db_path = './data/campus_wifi.db'
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("--- Starting Database Cleanup ---")

    try:
        # 1. Fetch all unique locations currently in the database
        cursor.execute("SELECT DISTINCT location FROM wifi_logs")
        locations_in_db = cursor.fetchall()

        for (raw_name,) in locations_in_db:
            # 2. Find what the name SHOULD be using your new logic
            standard_name = get_standard_name(raw_name)

            if standard_name and standard_name != raw_name:
                print(f"Fixing: '{raw_name}' -> '{standard_name}'")

                # 3. Update all records that use the "dirty" name
                cursor.execute(
                    "UPDATE wifi_logs SET location = ? WHERE location = ?",
                    (standard_name, raw_name)
                )
            elif not standard_name:
                print(f"Warning: '{raw_name}' is not a valid campus building. Skipping...")

        conn.commit()
        print("--- Cleanup Complete! ---")

    except Exception as e:
        print(f"Error during cleanup: {e}")
        conn.rollback()
    finally:
        conn.close()


if __name__ == "__main__":
    clean_database()