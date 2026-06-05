"""
email_controller.py
====================
The e-mail control layer: lets the Smart Home be commanded and report by
e-mail, using the dedicated Gmail account.

RESPONSIBILITIES
----------------
1. Poll the inbox (IMAP) for unread e-mails on a timer.
2. For each unread e-mail:
     - check the sender is on the AUTHORISED_SENDERS allow-list (ignore others)
     - match the SUBJECT against the known command set (case-insensitive)
     - translate the command into a serial command for the Arduino
     - mark the e-mail as read so it is not processed again
3. Send outgoing e-mails (SMTP):
     - secure-mode motion notification
     - emergency notification
     - reply to a STATUS request with the current system state
     - (the daily report is sent by report_generator.py, reusing send_email)

SECURITY
--------
Only e-mails whose sender address is in config.AUTHORISED_SENDERS are acted on.
A command from any other address is logged and ignored. This prevents a
stranger who learns the account from controlling the house.

SELF-TRIGGER SAFETY
-------------------
Because alerts/reports are sent TO the same account that receives commands,
outgoing mail uses subjects that are NOT command words (e.g. "Smart Home:
Motion Detected"), so the system never commands itself.

CREDENTIALS
-----------
The Gmail address and 16-char App Password come from config.py (which loads
the password from the private .env file). IMAP/SMTP servers are the standard
Gmail endpoints, also in config.py.
"""

import imaplib
import smtplib
import email
import threading
import time
from email.mime.text import MIMEText
from email.utils import parseaddr
from datetime import datetime

import config


# The set of valid command subjects. These are exactly the serial commands the
# Arduino understands, so we can pass a matched subject straight through. Stored
# uppercase; incoming subjects are upper-cased before lookup.
VALID_COMMANDS = {
    "HEAT_ON", "HEAT_OFF",
    "COOL_ON", "COOL_OFF",
    "ATC_ON", "ATC_OFF",
    "LIGHT_ON", "LIGHT_OFF",
    "LAUTO_ON", "LAUTO_OFF",
    "SECURE_ON", "SECURE_OFF",
    "EMERGENCY_OFF",
    "STATUS",
}


class EmailController:
    """Polls Gmail for command e-mails and sends alert/report e-mails."""

    def __init__(self, send_command_func=None, get_status_func=None):
        """
        send_command_func(str) : called with a command string to forward to the
                                 Arduino (normally ArduinoSerial.send_command).
        get_status_func()      : returns the latest status dict (for replying to
                                 STATUS requests). Optional.
        """
        self.send_command_func = send_command_func
        self.get_status_func = get_status_func

        # Lower-cased allow-list for case-insensitive sender comparison.
        self._allowed = {a.strip().lower() for a in config.AUTHORISED_SENDERS}

        self._stop = False
        self._thread = None

    # ------------------------------------------------------------------ #
    # LIFECYCLE
    # ------------------------------------------------------------------ #
    def start(self):
        """Start the background inbox-polling thread."""
        self._stop = False
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True
        if self._thread is not None:
            self._thread.join(timeout=5)

    # ------------------------------------------------------------------ #
    # OUTGOING MAIL (SMTP)
    # ------------------------------------------------------------------ #
    def send_email(self, subject, body, to_addr=None):
        """
        Send a plain-text e-mail. Returns True on success. Used for alerts,
        STATUS replies, and (via report_generator) the daily report.
        """
        to_addr = to_addr or config.EMAIL_ALERT_RECIPIENT
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = config.EMAIL_ADDRESS
        msg["To"] = to_addr
        try:
            with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT, timeout=20) as server:
                server.starttls()                       # upgrade to encrypted
                server.login(config.EMAIL_ADDRESS, config.EMAIL_APP_PASSWORD)
                server.sendmail(config.EMAIL_ADDRESS, [to_addr], msg.as_string())
            print(f"[email] Sent '{subject}' to {to_addr}.")
            return True
        except Exception as e:
            print(f"[email] Failed to send '{subject}': {e}")
            return False

    def send_email_with_attachments(self, subject, body, attachments, to_addr=None):
        """
        Send an e-mail with file attachments (used by the daily report for the
        PNG graphs). 'attachments' is a list of file paths. Returns True on
        success.
        """
        from email.mime.multipart import MIMEMultipart
        from email.mime.base import MIMEBase
        from email import encoders
        import os

        to_addr = to_addr or config.EMAIL_ALERT_RECIPIENT
        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = config.EMAIL_ADDRESS
        msg["To"] = to_addr
        msg.attach(MIMEText(body, "plain", "utf-8"))

        for path in attachments or []:
            try:
                with open(path, "rb") as f:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition",
                    f'attachment; filename="{os.path.basename(path)}"',
                )
                msg.attach(part)
            except OSError as e:
                print(f"[email] Could not attach {path}: {e}")

        try:
            with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT, timeout=30) as server:
                server.starttls()
                server.login(config.EMAIL_ADDRESS, config.EMAIL_APP_PASSWORD)
                server.sendmail(config.EMAIL_ADDRESS, [to_addr], msg.as_string())
            print(f"[email] Sent '{subject}' with {len(attachments or [])} attachment(s).")
            return True
        except Exception as e:
            print(f"[email] Failed to send '{subject}' with attachments: {e}")
            return False

    # --- Convenience wrappers for the specific alert types ---------------- #
    def notify_motion(self):
        """Secure-mode motion notification (called on EVENT:MOTION)."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.send_email(
            subject="Smart Home: Motion Detected",
            body=f"Motion was detected by the Home Secure system at {ts}.\n"
                 f"The light has been turned on automatically.",
        )

    def notify_emergency(self):
        """Emergency notification (called on EVENT:EMERGENCY)."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.send_email(
            subject="Smart Home: EMERGENCY ACTIVATED",
            body=f"EMERGENCY MODE was activated at {ts}.\n"
                 f"All systems have been turned off, Home Secure Mode is on, "
                 f"and the red indicator is lit.\n"
                 f"Emergency mode can only be cleared by sending an e-mail with "
                 f"subject EMERGENCY_OFF.",
        )

    def reply_status(self):
        """Reply to a STATUS request with the current system state."""
        if not self.get_status_func:
            self.send_email("Smart Home: Status", "Status is not available.")
            return
        s = self.get_status_func()
        if not s:
            self.send_email("Smart Home: Status",
                            "No status available yet (Arduino not connected?).")
            return
        body = (
            f"Smart Home status at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}:\n\n"
            f"  Temperature : {s['temperature']:.1f} C\n"
            f"  Illumination: {s['illumination']} %\n"
            f"  Motion      : {'yes' if s['motion'] else 'no'}\n"
            f"  Heating     : {'ON' if s['heating'] else 'off'}\n"
            f"  Cooling     : {'ON' if s['cooling'] else 'off'}\n"
            f"  Light       : {'ON' if s['light'] else 'off'}\n"
            f"  Auto Temp Ctrl (ATC): {'ON' if s['atc'] else 'off'}\n"
            f"  Home Secure Mode    : {'ON' if s['secure'] else 'off'}\n"
            f"  Light Auto Mode     : {'ON' if s['light_auto'] else 'off'}\n"
            f"  Emergency Mode      : {'ON' if s['emergency'] else 'off'}\n"
        )
        self.send_email("Smart Home: Status", body)

    # ------------------------------------------------------------------ #
    # INCOMING MAIL (IMAP)
    # ------------------------------------------------------------------ #
    def _poll_loop(self):
        """Poll the inbox on a timer, processing unread command e-mails."""
        while not self._stop:
            try:
                self._check_inbox_once()
            except Exception as e:
                # Never let an e-mail error kill the thread; log and retry.
                print(f"[email] Inbox check error: {e}")
            # Sleep in short slices so stop() is responsive.
            for _ in range(config.EMAIL_POLL_INTERVAL_SEC):
                if self._stop:
                    break
                time.sleep(1)

    def _check_inbox_once(self):
        """Connect, read unread messages, act on valid commands, mark read."""
        imap = imaplib.IMAP4_SSL(config.IMAP_SERVER, config.IMAP_PORT)
        try:
            imap.login(config.EMAIL_ADDRESS, config.EMAIL_APP_PASSWORD)
            imap.select("INBOX")

            # Find UNSEEN (unread) messages.
            status, data = imap.search(None, "UNSEEN")
            if status != "OK":
                return
            ids = data[0].split()
            for msg_id in ids:
                self._process_message(imap, msg_id)
        finally:
            try:
                imap.logout()
            except Exception:
                pass

    def _process_message(self, imap, msg_id):
        """Fetch one message, validate sender + subject, act, mark read."""
        status, data = imap.fetch(msg_id, "(RFC822)")
        if status != "OK" or not data or data[0] is None:
            return
        msg = email.message_from_bytes(data[0][1])

        # --- sender allow-list check ---
        _, from_addr = parseaddr(msg.get("From", ""))
        from_addr = from_addr.strip().lower()
        subject_raw = msg.get("Subject", "") or ""
        subject = subject_raw.strip().upper()

        if from_addr not in self._allowed:
            print(f"[email] Ignored command from unauthorised sender: {from_addr} "
                  f"(subject '{subject_raw}')")
            self._mark_read(imap, msg_id)
            return

        # --- subject must be a known command ---
        if subject not in VALID_COMMANDS:
            print(f"[email] Ignored e-mail with non-command subject: '{subject_raw}' "
                  f"from {from_addr}")
            self._mark_read(imap, msg_id)
            return

        print(f"[email] Authorised command '{subject}' from {from_addr}")

        # --- act on the command ---
        if subject == "STATUS":
            # A STATUS request: forward to Arduino (refreshes state) and reply.
            if self.send_command_func:
                self.send_command_func("STATUS")
            time.sleep(1)            # give the Arduino a moment to respond
            self.reply_status()
        else:
            # All other commands map 1:1 onto serial commands.
            if self.send_command_func:
                ok = self.send_command_func(subject)
                if not ok:
                    print(f"[email] Could not forward '{subject}' (Arduino offline).")

        self._mark_read(imap, msg_id)

    @staticmethod
    def _mark_read(imap, msg_id):
        """Mark a message as read so it is not processed again."""
        try:
            imap.store(msg_id, "+FLAGS", "\\Seen")
        except Exception as e:
            print(f"[email] Could not mark message read: {e}")


# ---------------------------------------------------------------------------
# STANDALONE TEST  
# ---------------------------------------------------------------------------
# Usage:  python email_controller.py
#
# This connects the e-mail controller to the live Arduino via the serial layer,
# so command e-mails actually move the hardware. It tests in pieces:
#
#   1. SENDING: immediately sends one test alert e-mail, so you can confirm
#      outgoing mail works (check the inbox of kucnimail1655@gmail.com).
#   2. RECEIVING/COMMANDS: then it polls the inbox. From your phone or any
#      e-mail client (using an AUTHORISED sender address), send e-mails with
#      these subjects and watch what happens:
#         LIGHT_ON   -> the white light should turn on
#         LIGHT_OFF  -> off
#         STATUS     -> you should receive a status reply e-mail
#         COOL_ON    -> fan + blue LED on
#      Unauthorised senders or non-command subjects are ignored (logged).
#
# Make sure the Arduino IDE Serial Monitor is CLOSED, and that .env has the
# real App Password. Stop with Ctrl+C.
if __name__ == "__main__":
    from arduino_serial import ArduinoSerial

    # Validate that credentials are present before trying anything.
    if not config.EMAIL_APP_PASSWORD:
        print("ERROR: EMAIL_APP_PASSWORD is empty. Fill in your .env file first.")
        raise SystemExit(1)

    link = ArduinoSerial()
    link.start()

    mailer = EmailController(
        send_command_func=link.send_command,
        get_status_func=lambda: link.latest_status,
    )

    # Forward EVENT lines from the Arduino to e-mail alerts, so motion and
    # emergency events generate notifications during this test too.
    def handle_event(event):
        if event == "MOTION":
            mailer.notify_motion()
        elif event == "EMERGENCY":
            mailer.notify_emergency()
    link.on_event = handle_event

    print("Test starting.")
    print("1) Sending a test alert e-mail now...")
    mailer.send_email("Smart Home: Test Alert",
                      "This is a test alert from your Smart Home system. "
                      "If you can read this, outgoing e-mail works.")

    print("2) Now polling the inbox for command e-mails.")
    print("   From an AUTHORISED address, e-mail subjects like LIGHT_ON, "
          "LIGHT_OFF, STATUS, COOL_ON.")
    print("   Press Ctrl+C to stop.\n")
    mailer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
        mailer.stop()
        link.stop()
        print("Stopped.")
