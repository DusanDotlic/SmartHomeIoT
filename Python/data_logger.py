"""
data_logger.py
==============
Local storage of every measurement and system-state snapshot, using SQLite.



WHAT IS STORED  (one row per status snapshot)
---------------------------------------------
    timestamp            - when the row was logged (ISO 8601 text, local time)
    temperature          - degrees C (float)
    illumination         - 0..100 percent (int)
    motion_detected      - 0/1
    heating_state        - 0/1
    cooling_state        - 0/1
    light_state          - 0/1
    secure_mode_state    - 0/1
    light_auto_mode_state- 0/1
    emergency_mode_state - 0/1

These columns map one-to-one onto the dict produced by arduino_serial.ArduinoSerial._parse_status().
"""

import sqlite3
import threading
from datetime import datetime, timedelta

import config


class DataLogger:
    """Writes status snapshots to a SQLite database and answers daily queries."""

    def __init__(self, db_path=None):
        self.db_path = db_path or config.DATABASE_PATH
        # SQLite connections are not safe to share across threads by default.
        # The serial reader runs in a background thread, so we guard all DB
        # access with a lock and open the connection with check_same_thread
        # = False so the single guarded connection can be used from either
        # thread.
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_table()

    # ------------------------------------------------------------------ #
    # SCHEMA
    # ------------------------------------------------------------------ #
    def _create_table(self):
        """Create the measurements table if it does not already exist."""
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS measurements (
                    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp             TEXT    NOT NULL,
                    temperature           REAL    NOT NULL,
                    illumination          INTEGER NOT NULL,
                    motion_detected       INTEGER NOT NULL,
                    heating_state         INTEGER NOT NULL,
                    cooling_state         INTEGER NOT NULL,
                    light_state           INTEGER NOT NULL,
                    secure_mode_state     INTEGER NOT NULL,
                    light_auto_mode_state INTEGER NOT NULL,
                    emergency_mode_state  INTEGER NOT NULL
                )
                """
            )
            self._conn.commit()

    # ------------------------------------------------------------------ #
    # INSERT
    # ------------------------------------------------------------------ #
    def log_status(self, status, timestamp=None):
        """
        Insert one row from a parsed status dict (as produced by the serial
        layer). 'timestamp' defaults to now. Returns the new row id.
        """
        ts = timestamp or datetime.now().isoformat(timespec="seconds")
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO measurements (
                    timestamp, temperature, illumination, motion_detected,
                    heating_state, cooling_state, light_state,
                    secure_mode_state, light_auto_mode_state,
                    emergency_mode_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    status["temperature"],
                    status["illumination"],
                    status["motion"],
                    status["heating"],
                    status["cooling"],
                    status["light"],
                    status["secure"],
                    status["light_auto"],
                    status["emergency"],
                ),
            )
            self._conn.commit()
            return cur.lastrowid

    # ------------------------------------------------------------------ #
    # QUERIES (used by the daily report)
    # ------------------------------------------------------------------ #
    def get_rows_for_day(self, day=None):
        """
        Return all rows whose timestamp falls on the given day (a date object;
        defaults to today) ordered by time. Used by the daily report.
        """
        day = day or datetime.now().date()
        start = datetime(day.year, day.month, day.day).isoformat()
        end = (datetime(day.year, day.month, day.day) + timedelta(days=1)).isoformat()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM measurements WHERE timestamp >= ? AND timestamp < ? "
                "ORDER BY timestamp ASC",
                (start, end),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_rows(self):
        """Total number of rows (handy for the test below)."""
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) AS c FROM measurements").fetchone()["c"]

    def close(self):
        with self._lock:
            self._conn.close()


# ---------------------------------------------------------------------------
# STANDALONE TEST 
# ---------------------------------------------------------------------------
# Usage:  python data_logger.py
#   Connects to the Arduino via the serial layer and logs each incoming STATUS
#   snapshot to smart_home.db. Prints the running row count. Stop with Ctrl+C.
#   Make sure the Arduino IDE Serial Monitor is CLOSED.
#
# After stopping, inspect the database with the one-liner printed at the end,
# or any SQLite viewer.
if __name__ == "__main__":
    import time
    from arduino_serial import ArduinoSerial

    logger = DataLogger()
    print(f"Logging to {logger.db_path}. Existing rows: {logger.count_rows()}")

    def handle_status(s):
        row_id = logger.log_status(s)
        print(f"  logged row {row_id}: temp={s['temperature']:.1f} "
              f"lux={s['illumination']} motion={s['motion']} "
              f"(total rows: {logger.count_rows()})")

    link = ArduinoSerial(on_status=handle_status)
    link.start()
    print("Data-logging test running. Each STATUS snapshot is stored.")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
        link.stop()
        total = logger.count_rows()
        logger.close()
        print(f"Stopped. Database now has {total} rows.")
        print("\nTo inspect from the command line, run:")
        print('  python -c "import sqlite3; '
              "rows=sqlite3.connect('smart_home.db').execute("
              "'SELECT * FROM measurements ORDER BY id DESC LIMIT 10').fetchall(); "
              '[print(r) for r in rows]"')
