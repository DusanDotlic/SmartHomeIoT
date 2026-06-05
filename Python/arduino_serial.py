"""
arduino_serial.py
=================
The serial communication layer between the PC (Python) and the Arduino.

RESPONSIBILITIES
----------------
- Open and hold the serial connection to the Arduino (COM3 by default).
- Read the lines the Arduino emits and parse them:
    * "STATUS;TEMP:..;LUX:..;..."  -> a dict of all current values/states
    * "EVENT:MOTION" / "EVENT:EMERGENCY" -> discrete events for the app to act on
    * "READY;..." -> emitted by the Arduino on (re)boot
- Send commands to the Arduino ("LIGHT_ON\n", "STATUS\n", etc.).
- AUTO-RECONNECT: if the cable is unplugged or the link drops, keep retrying
  until the Arduino is back, without crashing the program.

DESIGN
------
This class runs a background thread that continuously reads from the serial
port. Parsed status snapshots and events are handed to optional callback
functions supplied by the caller (main.py). This keeps the serial details in
one place and lets the rest of the application simply react to clean data.

NOTE ON ARDUINO RESET
---------------------
Opening the serial port resets the Arduino (normal behaviour on the Uno). So
right after connecting you will see a "READY;SmartHome;v1" line. The code
handles this gracefully; nothing special is required from the caller.
"""

import threading
import time

import serial  # from pyserial

import config


class ArduinoSerial:
    """Manages the serial link to the Arduino with automatic reconnection."""

    def __init__(self, on_status=None, on_event=None, on_connection_change=None):
        """
        on_status(dict)            : called with each parsed STATUS snapshot.
        on_event(str)              : called with the event name ("MOTION" /
                                     "EMERGENCY") for each EVENT line.
        on_connection_change(bool) : called with True when connected, False
                                     when the link drops. Optional.
        """
        self.on_status = on_status
        self.on_event = on_event
        self.on_connection_change = on_connection_change

        self._serial = None              # the pyserial Serial object (or None)
        self._connected = False          # currently connected?
        self._stop = False               # set True to ask the thread to stop
        self._thread = None              # the background reader thread
        self._write_lock = threading.Lock()  # serialize writes from any thread

        # The most recent parsed status, so other code can ask "what's the
        # current state?" without waiting for the next line.
        self.latest_status = None

    # ------------------------------------------------------------------ #
    # PUBLIC API
    # ------------------------------------------------------------------ #
    def start(self):
        """Start the background reader/reconnect thread."""
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the thread and close the port."""
        self._stop = True
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._close_port()

    def is_connected(self):
        return self._connected

    def send_command(self, command):
        """
        Send a command string to the Arduino (a newline is appended). Returns
        True if it was written, False if not currently connected. Thread-safe.
        """
        if not self._connected or self._serial is None:
            print(f"[serial] Cannot send '{command}': not connected.")
            return False
        try:
            with self._write_lock:
                self._serial.write((command.strip() + "\n").encode("ascii"))
            return True
        except (serial.SerialException, OSError) as e:
            # Writing failed -> the link probably dropped; let the reader loop
            # detect it and reconnect.
            print(f"[serial] Write failed ('{command}'): {e}")
            self._handle_disconnect()
            return False

    # ------------------------------------------------------------------ #
    # BACKGROUND THREAD
    # ------------------------------------------------------------------ #
    def _run(self):
        """Main loop: stay connected, read lines, reconnect on failure."""
        while not self._stop:
            if not self._connected:
                self._try_connect()
                if not self._connected:
                    # Connection failed; wait before retrying so we don't spin.
                    time.sleep(config.SERIAL_RECONNECT_DELAY)
                    continue

            # Connected: read one line and process it.
            try:
                raw = self._serial.readline()
                if raw:
                    line = raw.decode("ascii", errors="ignore").strip()
                    if line:
                        self._process_line(line)
            except (serial.SerialException, OSError) as e:
                print(f"[serial] Read error: {e}")
                self._handle_disconnect()
                time.sleep(config.SERIAL_RECONNECT_DELAY)

    def _try_connect(self):
        """Attempt to open the serial port once."""
        try:
            self._serial = serial.Serial(
                port=config.SERIAL_PORT,
                baudrate=config.SERIAL_BAUD,
                timeout=config.SERIAL_TIMEOUT,
            )
            # Opening resets the Arduino; give it a moment to boot before we
            # rely on the stream.
            time.sleep(2)
            self._connected = True
            print(f"[serial] Connected on {config.SERIAL_PORT}.")
            if self.on_connection_change:
                self.on_connection_change(True)
        except (serial.SerialException, OSError) as e:
            # Port not available (e.g. cable unplugged, or held by the IDE).
            self._connected = False
            self._serial = None
            # Keep this quiet-ish so reconnection attempts don't flood the log.
            print(f"[serial] Connect failed ({config.SERIAL_PORT}): {e}")

    def _handle_disconnect(self):
        """Mark disconnected and close the port so we can cleanly reopen it."""
        was_connected = self._connected
        self._connected = False
        self._close_port()
        if was_connected and self.on_connection_change:
            self.on_connection_change(False)

    def _close_port(self):
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

    # ------------------------------------------------------------------ #
    # LINE PARSING
    # ------------------------------------------------------------------ #
    def _process_line(self, line):
        """Route a single received line to the right handler."""
        if line.startswith("STATUS;"):
            status = self._parse_status(line)
            if status is not None:
                self.latest_status = status
                if self.on_status:
                    self.on_status(status)
        elif line.startswith("EVENT:"):
            event = line[len("EVENT:"):].strip()
            print(f"[serial] EVENT received: {event}")
            if self.on_event:
                self.on_event(event)
        elif line.startswith("READY;"):
            print(f"[serial] Arduino reports: {line}")
        else:
            # Other lines (UNKNOWN_CMD, IGNORED_DURING_EMERGENCY, etc.) - log.
            print(f"[serial] (info) {line}")

    @staticmethod
    def _parse_status(line):
        """
        Parse a STATUS line into a dict. Example input:
          STATUS;TEMP:22.4;LUX:45;MOTION:0;HEAT:0;COOL:1;LIGHT:0;ATC:1;SECURE:0;LAUTO:1;EMERG:0
        Returns a dict like:
          {'temperature': 22.4, 'illumination': 45, 'motion': 0,
           'heating': 0, 'cooling': 0, 'light': 0, 'atc': 1,
           'secure': 0, 'light_auto': 1, 'emergency': 0}
        Returns None if the line is malformed (so a single garbled line - which
        can happen during the Arduino's reset - is skipped, not fatal).
        """
        try:
            parts = line.split(";")
            # parts[0] is "STATUS"; the rest are KEY:VALUE.
            fields = {}
            for token in parts[1:]:
                if ":" not in token:
                    continue
                key, value = token.split(":", 1)
                fields[key] = value

            # Map the Arduino's short keys to clear Python names + types.
            return {
                "temperature": float(fields["TEMP"]),
                "illumination": int(fields["LUX"]),
                "motion": int(fields["MOTION"]),
                "heating": int(fields["HEAT"]),
                "cooling": int(fields["COOL"]),
                "light": int(fields["LIGHT"]),
                "atc": int(fields["ATC"]),
                "secure": int(fields["SECURE"]),
                "light_auto": int(fields["LAUTO"]),
                "emergency": int(fields["EMERG"]),
            }
        except (KeyError, ValueError, IndexError):
            # Malformed/partial line (common right at connect). Skip it.
            return None


# ---------------------------------------------------------------------------
# STANDALONE TEST  
# ---------------------------------------------------------------------------
# Usage:  python arduino_serial.py
#   - Prints each parsed status snapshot.
#   - Prints events.
#   - Prints connection changes (so you can watch auto-reconnect when you
#     unplug/replug the USB cable).
#   - Sends a STATUS command every 10 seconds to exercise the write path.
# Make sure the Arduino IDE Serial Monitor is CLOSED first (only one program
# can hold COM3 at a time).
if __name__ == "__main__":
    def handle_status(s):
        print(f"  STATUS  temp={s['temperature']:.1f}C  lux={s['illumination']}%  "
              f"motion={s['motion']}  heat={s['heating']} cool={s['cooling']} "
              f"light={s['light']}  atc={s['atc']} secure={s['secure']} "
              f"lauto={s['light_auto']} emerg={s['emergency']}")

    def handle_event(e):
        print(f"  >>> EVENT: {e}")

    def handle_conn(connected):
        print("  *** CONNECTED" if connected else "  *** DISCONNECTED (will retry)")

    link = ArduinoSerial(on_status=handle_status,
                         on_event=handle_event,
                         on_connection_change=handle_conn)
    link.start()
    print("Serial test running. Watching telemetry.")
    print("Try: unplug the USB cable, wait, replug -> should auto-reconnect.")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(10)
            # Exercise the write path periodically.
            link.send_command("STATUS")
    except KeyboardInterrupt:
        print("\nStopping...")
        link.stop()
        print("Stopped.")
