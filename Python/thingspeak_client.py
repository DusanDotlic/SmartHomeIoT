"""
thingspeak_client.py
=====================
Uploads measurements to ThingSpeak so they can be viewed and
charted online, sending all measurements and detections to ThingSpeak.

WHAT IS UPLOADED  (field mapping must match the channel configuration)
----------------------------------------------------------------------
    field1 = Temperature   (degrees C)
    field2 = Illumination  (0..100 percent)
    field3 = Motion        (0 / 1)

HOW IT WORKS
------------
ThingSpeak accepts a simple HTTP GET/POST to its /update endpoint with the
Write API Key and the field values as parameters. A successful update returns
the new "entry id" (a positive integer) as the response body; "0" means the
update was rejected (usually for exceeding the rate limit).

RATE LIMIT
----------
The free tier accepts at most one update every 15 seconds. We enforce a safety
floor (config.THINGSPEAK_MIN_UPLOAD_INTERVAL_SEC, e.g. 20s) so rapid calls
during testing are skipped rather than rejected. In normal operation the system
measures every 10 minutes, so this is never an issue.

ROBUSTNESS
----------
All network calls are wrapped so a failed upload (no internet, timeout, etc.)
is logged and ignored - it must never crash the application.

CREDENTIALS
-----------
The Write API Key comes from config.py (loaded from the private .env file).
"""

import time

import requests

import config


class ThingSpeakClient:
    """Sends temperature / illumination / motion to a ThingSpeak channel."""

    def __init__(self):
        self.write_key = config.THINGSPEAK_WRITE_KEY
        self.url = config.THINGSPEAK_UPDATE_URL
        self.min_interval = config.THINGSPEAK_MIN_UPLOAD_INTERVAL_SEC
        self._last_upload_time = 0.0   # monotonic timestamp of last successful send

    def upload(self, status, force=False):
        """
        Upload one status snapshot's measurements to ThingSpeak.

        status : a parsed status dict (temperature, illumination, motion, ...).
        force  : if True, bypass the local rate-limit floor (used by the test
                 harness so you don't have to wait; do NOT use in production or
                 ThingSpeak will reject the rapid updates).

        Returns the ThingSpeak entry id (int > 0) on success, or None on
        failure / skip.
        """
        if not self.write_key:
            print("[thingspeak] No write key configured (.env). Skipping upload.")
            return None

        # Enforce the local rate-limit floor unless forced.
        now = time.monotonic()
        if not force and (now - self._last_upload_time) < self.min_interval:
            wait = self.min_interval - (now - self._last_upload_time)
            print(f"[thingspeak] Skipping upload (rate limit; {wait:.0f}s to go).")
            return None

        params = {
            "api_key": self.write_key,
            config.THINGSPEAK_FIELD_TEMPERATURE: f"{status['temperature']:.1f}",
            config.THINGSPEAK_FIELD_ILLUMINATION: status["illumination"],
            config.THINGSPEAK_FIELD_MOTION: status["motion"],
        }

        try:
            resp = requests.get(self.url, params=params, timeout=15)
            body = resp.text.strip()
            if resp.status_code == 200 and body.isdigit() and int(body) > 0:
                entry_id = int(body)
                self._last_upload_time = time.monotonic()
                print(f"[thingspeak] Uploaded entry #{entry_id}: "
                      f"T={params[config.THINGSPEAK_FIELD_TEMPERATURE]} "
                      f"L={status['illumination']} M={status['motion']}")
                return entry_id
            else:
                # "0" or non-numeric -> rejected (often rate limit on server side).
                print(f"[thingspeak] Update rejected (response='{body}', "
                      f"status={resp.status_code}).")
                return None
        except requests.RequestException as e:
            # Network problem: log and continue, never crash.
            print(f"[thingspeak] Upload failed (network): {e}")
            return None


# ---------------------------------------------------------------------------
# STANDALONE TEST 
# ---------------------------------------------------------------------------
# Usage:  python thingspeak_client.py
#
# Connects to the Arduino via the serial layer and uploads each STATUS snapshot
# to ThingSpeak. Because real operation only uploads every 10 minutes (too slow
# to watch), this test uploads roughly every 20 seconds (the rate-limit floor)
# so you can SEE the channel populate within a couple of minutes.
#
# AFTER STARTING: open your channel at thingspeak.com (Channels > My Channels >
# your channel) and watch the Field 1 (Temperature), Field 2 (Illumination),
# and Field 3 (Motion) charts fill in with live points.
#
# Make sure the Serial Monitor is CLOSED and .env has the Write API Key.
# Stop with Ctrl+C.
if __name__ == "__main__":
    from arduino_serial import ArduinoSerial

    if not config.THINGSPEAK_WRITE_KEY:
        print("ERROR: THINGSPEAK_WRITE_KEY is empty. Fill in your .env file first.")
        raise SystemExit(1)

    client = ThingSpeakClient()
    link = ArduinoSerial()
    link.start()

    print("Uploading the latest reading every ~20s.")
    print("Open your ThingSpeak channel and watch fields 1/2/3 populate.")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            # Wait the rate-limit interval, then upload the most recent reading.
            time.sleep(config.THINGSPEAK_MIN_UPLOAD_INTERVAL_SEC)
            status = link.latest_status
            if status is not None:
                client.upload(status)
            else:
                print("[thingspeak] No status yet (Arduino connecting?)...")
    except KeyboardInterrupt:
        print("\nStopping...")
        link.stop()
        print("Stopped.")
