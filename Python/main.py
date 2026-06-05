"""
main.py
=======
The Smart Home IoT application entry point. This ties every module together
into one continuously-running program:

    - ArduinoSerial   : reads telemetry / events, sends commands, auto-reconnect
    - DataLogger      : stores every status snapshot in SQLite
    - EmailController : receives command e-mails, sends alerts and STATUS replies
    - ThingSpeakClient: uploads temperature / illumination / motion
    - ReportGenerator : builds and e-mails the daily report at a scheduled time

WHAT HAPPENS AT RUNTIME
-----------------------
1. The serial layer connects to the Arduino and starts streaming STATUS lines.
2. Every STATUS snapshot is:
     - logged to the database,
     - uploaded to ThingSpeak (subject to the rate-limit / upload interval).
3. EVENT lines from the Arduino become e-mail alerts:
     - EVENT:MOTION    -> "Motion Detected" e-mail (Home Secure Mode)
     - EVENT:EMERGENCY -> "EMERGENCY ACTIVATED" e-mail
4. The e-mail controller polls the inbox; authorised command subjects are
   forwarded to the Arduino, and STATUS requests get a reply e-mail.
5. A scheduler checks the clock; at the configured time each day it generates
   and e-mails the daily report.

Run with:   python main.py      (press Ctrl+C to stop)
Make sure the Arduino IDE Serial Monitor is CLOSED and .env is filled in.

NOTE ON UPLOAD/REPORT TIMING
----------------------------
The Arduino's measurement interval is set in the firmware (#define). ThingSpeak
uploads are gated by THINGSPEAK_MIN_UPLOAD_INTERVAL_SEC here so we never exceed
the free-tier limit even if status lines arrive quickly during testing.
"""

import time
import threading
from datetime import datetime, date

import config
from arduino_serial import ArduinoSerial
from data_logger import DataLogger
from email_controller import EmailController
from thingspeak_client import ThingSpeakClient
from report_generator import ReportGenerator


class SmartHomeApp:
    """Orchestrates all subsystems."""

    def __init__(self):
        # --- core data + cloud services ---
        self.logger = DataLogger()
        self.thingspeak = ThingSpeakClient()

        # --- serial link; callbacks wired to logging / uploading / events ---
        self.link = ArduinoSerial(
            on_status=self._on_status,
            on_event=self._on_event,
            on_connection_change=self._on_connection_change,
        )

        # --- e-mail controller; can command the Arduino and read latest status ---
        self.mailer = EmailController(
            send_command_func=self.link.send_command,
            get_status_func=lambda: self.link.latest_status,
        )

        # --- daily report generator (reuses the same logger + mailer) ---
        self.report = ReportGenerator(logger=self.logger, mailer=self.mailer)

        # --- internal scheduling state ---
        self._last_upload = 0.0          # monotonic time of last ThingSpeak upload
        self._last_report_date = None    # date we last sent the daily report
        self._stop = False
        self._scheduler_thread = None

        # Reduce log spam: only print "connect failed" once per disconnect.
        self._connect_warned = False

    # ------------------------------------------------------------------ #
    # CALLBACKS FROM THE SERIAL LAYER
    # ------------------------------------------------------------------ #
    def _on_status(self, status):
        """Called for every STATUS snapshot: log it and (rate-limited) upload."""
        # 1) Always log to the database.
        try:
            self.logger.log_status(status)
        except Exception as e:
            print(f"[main] DB log error: {e}")

        # 2) Upload to ThingSpeak, but not more often than the upload interval.
        now = time.monotonic()
        if now - self._last_upload >= config.THINGSPEAK_MIN_UPLOAD_INTERVAL_SEC:
            self._last_upload = now
            # Run the upload in a short-lived thread so a slow network call
            # never blocks the serial reader.
            threading.Thread(
                target=self.thingspeak.upload, args=(status,), daemon=True
            ).start()

    def _on_event(self, event):
        """Called for EVENT lines: turn them into e-mail alerts."""
        if event == "MOTION":
            threading.Thread(target=self.mailer.notify_motion, daemon=True).start()
        elif event == "EMERGENCY":
            threading.Thread(target=self.mailer.notify_emergency, daemon=True).start()

    def _on_connection_change(self, connected):
        if connected:
            print("[main] Arduino connected.")
            self._connect_warned = False
        else:
            print("[main] Arduino disconnected; auto-reconnecting...")

    # ------------------------------------------------------------------ #
    # DAILY REPORT SCHEDULER
    # ------------------------------------------------------------------ #
    def _scheduler_loop(self):
        """
        Checks the clock once a minute. When local time reaches the configured
        report time and we have not yet sent today's report, generate and send
        it. Using a 'last report date' guard ensures it fires exactly once a day.
        """
        while not self._stop:
            now = datetime.now()
            today = now.date()
            if (now.hour == config.DAILY_REPORT_HOUR
                    and now.minute == config.DAILY_REPORT_MINUTE
                    and self._last_report_date != today):
                print("[main] Generating scheduled daily report...")
                try:
                    self.report.generate_and_send(day=today, send=True)
                except Exception as e:
                    print(f"[main] Daily report error: {e}")
                self._last_report_date = today
            # Sleep ~30s (responsive to stop, and fine for minute-resolution).
            for _ in range(30):
                if self._stop:
                    return
                time.sleep(1)

    # ------------------------------------------------------------------ #
    # LIFECYCLE
    # ------------------------------------------------------------------ #
    def start(self):
        print("=" * 55)
        print(" Smart Home IoT - starting")
        print("=" * 55)

        # Warn (do not abort) if credentials are missing.
        config.validate_config()

        self.link.start()
        self.mailer.start()
        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop, daemon=True
        )
        self._scheduler_thread.start()

        print(f"[main] Running. Daily report scheduled for "
              f"{config.DAILY_REPORT_HOUR:02d}:{config.DAILY_REPORT_MINUTE:02d}.")
        print("[main] Press Ctrl+C to stop.\n")

    def run_forever(self):
        self.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[main] Stopping...")
            self.stop()
            print("[main] Stopped.")

    def stop(self):
        self._stop = True
        self.mailer.stop()
        self.link.stop()
        if self._scheduler_thread is not None:
            self._scheduler_thread.join(timeout=2)
        self.logger.close()


# ---------------------------------------------------------------------------
# MANUAL DAILY-REPORT TRIGGER
# ---------------------------------------------------------------------------
# For testing/demonstration. 
# Run:   python main.py report
# to build and e-mail today's report immediately, then exit.
if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1].lower() == "report":
        print("Generating today's daily report immediately (manual trigger)...")
        mailer = EmailController()
        rg = ReportGenerator(mailer=mailer)
        rg.generate_and_send(send=True)
        print("Done.")
    else:
        app = SmartHomeApp()
        app.run_forever()
