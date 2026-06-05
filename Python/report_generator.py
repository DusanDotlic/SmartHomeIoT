"""
report_generator.py
====================
Builds and sends the automatic DAILY REPORT.

THE REPORT CONTAINS :
    1.  Minimum daily temperature
    2.  Maximum daily temperature
    3.  Average daily temperature
    4.  Graph of all temperature measurements        (PNG)
    5.  Minimum daily illumination
    6.  Maximum daily illumination
    7.  Average daily illumination
    8.  Graph of all illumination measurements        (PNG)
    9.  Total number of motion detections that day
    10. Graph of motion detections that day           (PNG)
    11. Total duration Home Secure Mode was ON
    12. Total duration Light Auto Mode was ON

The three graphs are saved as PNG files AND attached to the
report e-mail.

DATA SOURCE
-----------
All figures come from the SQLite database (data_logger.DataLogger), which has
been storing one row per measurement snapshot. Durations for items 11/12 are
estimated from the logged state flags (see _on_duration_seconds).

DEPENDENCIES
------------
    matplotlib   (graphs)   ->  pip install matplotlib
"""

import os
from datetime import datetime, date

# Use a non-interactive backend so matplotlib works without any display
# (important on a PC that may run this headless / in the background).
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

import config
from data_logger import DataLogger


class ReportGenerator:
    """Computes daily statistics, renders PNG graphs, and e-mails the report."""

    def __init__(self, logger=None, mailer=None):
        """
        logger : a DataLogger (defaults to one opened on config.DATABASE_PATH).
        mailer : an EmailController, used to send the report. If None, the
                 report is built and graphs saved, but not e-mailed (useful for
                 offline testing).
        """
        self.logger = logger or DataLogger()
        self.mailer = mailer
        os.makedirs(config.REPORT_OUTPUT_DIR, exist_ok=True)

    # ------------------------------------------------------------------ #
    # STATISTICS
    # ------------------------------------------------------------------ #
    @staticmethod
    def _on_duration_seconds(rows, flag_key):
        """
        Estimate how long a boolean state (e.g. secure_mode_state) was ON during
        the day, in seconds.

        METHOD: rows are time-ordered snapshots. For each consecutive pair of
        rows, if the EARLIER row had the flag ON, we count the time gap between
        the two rows as "on" time. This approximates the true on-duration to the
        resolution of the measurement interval, which is the best we can do from
        sampled data. 
        """
        total = 0.0
        for earlier, later in zip(rows, rows[1:]):
            if earlier[flag_key] == 1:
                t0 = datetime.fromisoformat(earlier["timestamp"])
                t1 = datetime.fromisoformat(later["timestamp"])
                total += (t1 - t0).total_seconds()
        return total

    @staticmethod
    def _count_motion_events(rows):
        """
        Count motion DETECTIONS as the number of rising edges (0 -> 1) in the
        motion column, so a single sustained motion is one detection rather than
        many. This matches the intuitive "number of times motion was detected".
        """
        count = 0
        prev = 0
        for r in rows:
            m = r["motion_detected"]
            if prev == 0 and m == 1:
                count += 1
            prev = m
        return count

    @staticmethod
    def _fmt_duration(seconds):
        """Human-readable H:MM:SS from a number of seconds."""
        seconds = int(seconds)
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h}h {m}m {s}s"

    def compute_statistics(self, rows):
        """Return a dict of all the numeric report items from the day's rows."""
        if not rows:
            return None

        temps = [r["temperature"] for r in rows]
        lux = [r["illumination"] for r in rows]

        return {
            "count": len(rows),
            "temp_min": min(temps),
            "temp_max": max(temps),
            "temp_avg": sum(temps) / len(temps),
            "lux_min": min(lux),
            "lux_max": max(lux),
            "lux_avg": sum(lux) / len(lux),
            "motion_total": self._count_motion_events(rows),
            "secure_on_seconds": self._on_duration_seconds(rows, "secure_mode_state"),
            "lauto_on_seconds": self._on_duration_seconds(rows, "light_auto_mode_state"),
        }

    # ------------------------------------------------------------------ #
    # GRAPHS  (saved as PNG)
    # ------------------------------------------------------------------ #
    def _make_graphs(self, rows, day):
        """
        Render the three graphs to PNG files and return their paths.
        """
        times = [datetime.fromisoformat(r["timestamp"]) for r in rows]
        temps = [r["temperature"] for r in rows]
        lux = [r["illumination"] for r in rows]
        motion = [r["motion_detected"] for r in rows]

        daystr = day.isoformat()
        out = config.REPORT_OUTPUT_DIR

        temp_path = os.path.join(out, f"temperature_{daystr}.png")
        lux_path = os.path.join(out, f"illumination_{daystr}.png")
        motion_path = os.path.join(out, f"motion_{daystr}.png")

        # --- Temperature graph ---
        plt.figure(figsize=(8, 4))
        plt.plot(times, temps, color="tab:red", marker=".")
        plt.title(f"Temperature on {daystr}")
        plt.xlabel("Time")
        plt.ylabel("Temperature (C)")
        plt.gca().xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(temp_path)
        plt.close()

        # --- Illumination graph ---
        plt.figure(figsize=(8, 4))
        plt.plot(times, lux, color="tab:orange", marker=".")
        plt.title(f"Illumination on {daystr}")
        plt.xlabel("Time")
        plt.ylabel("Illumination (%)")
        plt.gca().xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(lux_path)
        plt.close()

        # --- Motion graph (step plot: clear 0/1 squarewave) ---
        plt.figure(figsize=(8, 4))
        plt.step(times, motion, where="post", color="tab:blue")
        plt.title(f"Motion detections on {daystr}")
        plt.xlabel("Time")
        plt.ylabel("Motion (0/1)")
        plt.yticks([0, 1])
        plt.gca().xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(motion_path)
        plt.close()

        return [temp_path, lux_path, motion_path]

    # ------------------------------------------------------------------ #
    # REPORT BODY
    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_body(stats, day):
        """Assemble the text body listing all 12 required report items."""
        return (
            f"SMART HOME DAILY REPORT - {day.isoformat()}\n"
            f"{'=' * 40}\n\n"
            f"Measurements recorded today: {stats['count']}\n\n"
            f"TEMPERATURE\n"
            f"  1. Minimum : {stats['temp_min']:.1f} C\n"
            f"  2. Maximum : {stats['temp_max']:.1f} C\n"
            f"  3. Average : {stats['temp_avg']:.1f} C\n"
            f"  4. Graph   : see attached temperature_{day.isoformat()}.png\n\n"
            f"ILLUMINATION\n"
            f"  5. Minimum : {stats['lux_min']} %\n"
            f"  6. Maximum : {stats['lux_max']} %\n"
            f"  7. Average : {stats['lux_avg']:.1f} %\n"
            f"  8. Graph   : see attached illumination_{day.isoformat()}.png\n\n"
            f"MOTION\n"
            f"  9. Total detections : {stats['motion_total']}\n"
            f"  10. Graph           : see attached motion_{day.isoformat()}.png\n\n"
            f"MODE DURATIONS\n"
            f"  11. Home Secure Mode was ON for : "
            f"{ReportGenerator._fmt_duration(stats['secure_on_seconds'])}\n"
            f"  12. Light Auto Mode was ON for  : "
            f"{ReportGenerator._fmt_duration(stats['lauto_on_seconds'])}\n"
        )

    # ------------------------------------------------------------------ #
    # MAIN ENTRY POINT
    # ------------------------------------------------------------------ #
    def generate_and_send(self, day=None, send=True):
        """
        Builds the daily report for 'day' (default: today). Saves graphs to PNG,
        and (if send=True and a mailer is configured) e-mails the report with
        the graphs attached. Returns the stats dict, or None if there is no data.
        """
        day = day or date.today()
        rows = self.logger.get_rows_for_day(day)
        if not rows:
            print(f"[report] No data for {day.isoformat()}; nothing to report.")
            return None

        stats = self.compute_statistics(rows)
        graph_paths = self._make_graphs(rows, day)
        body = self._build_body(stats, day)

        print("[report] Report built:")
        print(body)
        print(f"[report] Graphs saved: {graph_paths}")

        if send and self.mailer is not None:
            self.mailer.send_email_with_attachments(
                subject=f"Smart Home: Daily Report {day.isoformat()}",
                body=body,
                attachments=graph_paths,
            )
        elif send:
            print("[report] No mailer configured; report not e-mailed "
                  "(graphs still saved).")

        return stats


# ---------------------------------------------------------------------------
# STANDALONE TEST 
# ---------------------------------------------------------------------------
# Usage:  python report_generator.py
#
# Builds the report from whatever is currently in smart_home.db; 
# run data_logger.py first to collect data if the database is empty. 
# Saves the three PNG graphs into the 'reports' folder, and
# e-mails the report with the graphs attached to your alert recipient.
#
# Requires: pip install matplotlib   (and a populated smart_home.db)
# .env must have the App Password for the e-mail to send.
if __name__ == "__main__":
    from email_controller import EmailController

    # A mailer that can send (no Arduino needed for the report itself).
    mailer = EmailController()

    rg = ReportGenerator(mailer=mailer)
    print("Generating today's report from smart_home.db ...\n")
    stats = rg.generate_and_send(send=True)

    if stats is None:
        print("\nNo data found for today. Run data_logger.py for a while to "
              "collect some measurements, then try again.")
    else:
        print("\nDone. Check the 'reports' folder for the PNG graphs, and check "
              f"{config.EMAIL_ALERT_RECIPIENT} for the report e-mail.")
