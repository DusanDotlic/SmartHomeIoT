"""
config.py
=========
Central configuration for the Smart Home IoT project.

This is the SINGLE place where all tunable settings live: the serial port,
the Arduino pin map, the control thresholds, the e-mail server details, and
the ThingSpeak channel info. Every other Python module imports its settings
from here.

SECRETS HANDLING
----------------
This file contains NO actual passwords or API keys. Instead it reads them
from a separate, private ".env" file using the python-dotenv library. That
keeps secrets out of the source code, so config.py itself is safe to share,
e-mail, or submit. Only the ".env" file must be kept private.

If python-dotenv is not installed, or no .env file is found, the code falls
back to reading real operating-system environment variables, and finally to
empty strings (which will make the program complain clearly at startup rather
than failing in a confusing way later).

Install the loader with:   pip install python-dotenv
"""

import os

# ---------------------------------------------------------------------------
# Load the private .env file (if present) into the environment.
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv
    load_dotenv()  # looks for a ".env" file in the current working directory
except ImportError:
    # python-dotenv not installed. The program can still read OS environment
    # variables; it just won't auto-load the .env file. We print a hint.
    print("[config] Note: python-dotenv not installed. "
          "Run 'pip install python-dotenv' to auto-load the .env file.")


# ===========================================================================
# SECTION 1: SERIAL COMMUNICATION
# ===========================================================================
# The COM port the Arduino enumerates as on this PC. 
# If Windows assigns a different port later (e.g. after using a different USB socket),
# change this value or check Arduino IDE > Tools > Port.
SERIAL_PORT = "COM3"

# Baud rate. Must MATCH the Serial.begin() value in the Arduino firmware.
SERIAL_BAUD = 9600

# How long (seconds) to wait on a serial read before giving up and looping.
SERIAL_TIMEOUT = 2

# When the connection drops, how long (seconds) to wait before trying to
# reconnect. The auto-reconnect logic in arduino_serial.py uses this.
SERIAL_RECONNECT_DELAY = 3


# ===========================================================================
# SECTION 2: ARDUINO PIN MAP  
# ===========================================================================
PIN_LM35_TEMP       = "A0"   # LM35 analog temperature sensor   
PIN_LDR_LIGHT       = "A1"   # LDR light-divider midpoint       
PIN_PIR_MOTION      = "D2"   # HC-SR501 PIR motion OUT          
PIN_EMERGENCY_BTN   = "D3"   # Emergency push button            
PIN_RELAY_HEAT      = "D4"   # Relay 1 -> heating indicator LED 
PIN_RELAY_COOL      = "D5"   # Relay 2 -> cooling DC fan        
PIN_RELAY_LIGHT     = "D6"   # Relay 3 -> lighting LED          
PIN_RGB_LEG_A       = "D7"   # Emergency RGB LED leg A          
PIN_RGB_LEG_B       = "D8"   # Emergency RGB LED leg B          
PIN_RGB_LEG_C       = "D9"   # Emergency RGB LED leg C          


# ===========================================================================
# SECTION 3: CONTROL THRESHOLDS  (mirror the firmware constants)
# ===========================================================================
# The Arduino enforces these in real time; Python keeps copies so the daily
# report and any PC-side logic use identical numbers. If you change a threshold,
# change it in BOTH the firmware and here.

# Temperature control (hysteresis). Cooling turns on above the HIGH limit and
# stays on until the LOW limit; heating is the mirror image.
TEMP_COOL_ON_ABOVE  = 23.0   # deg C: above this -> start cooling
TEMP_COOL_OFF_BELOW = 17.0   # deg C: cooling runs until temperature drops here
TEMP_HEAT_ON_BELOW  = 17.0   # deg C: below this -> start heating
TEMP_HEAT_OFF_ABOVE = 23.0   # deg C: heating runs until temperature rises here

# Light auto mode: below this illumination percentage, the light turns on.
LIGHT_AUTO_ON_BELOW_PCT = 30  # percent (0..100)

# How often the Arduino takes/reports a measurement set, in the firmware.
MEASUREMENT_INTERVAL_SEC = 600

# Home Secure Mode: after motion, the light stays on for this many seconds of
# no further motion, then turns off. Only one e-mail per event window.
SECURE_MODE_LIGHT_TIMEOUT_SEC = 10


# ===========================================================================
# SECTION 4: E-MAIL (Gmail) SETTINGS
# ===========================================================================
# The dedicated project account. It both SENDS alerts/reports and RECEIVES
# commands. Logging in uses the 16-character App Password (NOT the normal
# Gmail password), which is read from the .env file below.
EMAIL_ADDRESS = "kucnimail1655@gmail.com"

# The App Password is a SECRET -> loaded from .env, never written here.
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "")

# Where alerts and the daily report are SENT. It equals EMAIL_ADDRESS for now. You could change this to a
# personal address later if you prefer them in your everyday inbox.
EMAIL_ALERT_RECIPIENT = "kucnimail1655@gmail.com"

# Standard Gmail server endpoints (these rarely change).
IMAP_SERVER = "imap.gmail.com"   # for READING incoming command e-mails
IMAP_PORT   = 993                # IMAP over SSL
SMTP_SERVER = "smtp.gmail.com"   # for SENDING alerts and reports
SMTP_PORT   = 587                # SMTP with STARTTLS

# How often (seconds) to poll the inbox for new command e-mails.
EMAIL_POLL_INTERVAL_SEC = 15


# ===========================================================================
# SECTION 5: AUTHORISED COMMAND SENDERS 
# ===========================================================================
# For security, the system only ACTS on command e-mails whose sender address
# is in this list. A command from any other address is ignored (and logged).
# This stops a stranger who somehow learns the account from controlling the
# house.
#
# HOW TO EXTEND THIS LATER (e.g. to add housemates):
#   - Just add their e-mail address as a new string in the list below,
#     each on its own line, separated by commas.
#   - Capitalisation does not matter; the code lower-cases everything before
#     comparing, so "Anna@Example.com" and "anna@example.com" are treated the
#     same.
#   - Save the file. No other change is needed; the new address can send
#     commands immediately the next time the program starts.
#
AUTHORISED_SENDERS = [
    "kucnimail1655@gmail.com",        # the project account itself
    "dusandotlic1999@gmail.com",       
    "jovana.vujovic4444@gmail.com",      
]


# ===========================================================================
# SECTION 6: THINGSPEAK SETTINGS
# ===========================================================================
# The Write API Key is a SECRET -> loaded from .env.
THINGSPEAK_WRITE_KEY = os.getenv("THINGSPEAK_WRITE_KEY", "")

# Channel ID is not secret (it identifies the channel for viewing). Stored here
# for convenience/debugging.
THINGSPEAK_CHANNEL_ID = 3397976  

# ThingSpeak update endpoint and field mapping. The field numbers MUST match
# how the channel is labelled: field1=Temperature, field2=Illumination, field3=Motion.
THINGSPEAK_UPDATE_URL = "https://api.thingspeak.com/update"
THINGSPEAK_FIELD_TEMPERATURE  = "field1"
THINGSPEAK_FIELD_ILLUMINATION = "field2"
THINGSPEAK_FIELD_MOTION       = "field3"

# Free-tier rate limit: one update per 15 seconds. We keep a safety margin.
THINGSPEAK_MIN_UPLOAD_INTERVAL_SEC = 20


# ===========================================================================
# SECTION 7: DATABASE & REPORT FILE PATHS
# ===========================================================================
# SQLite database file that stores every measurement and all state fields.
DATABASE_PATH = "smart_home.db"

# Folder where generated report graphs (PNG) are written.
REPORT_OUTPUT_DIR = "reports"

# What local time (24h clock) the daily report is generated and e-mailed.
DAILY_REPORT_HOUR   = 23   # 23 = 11 PM
DAILY_REPORT_MINUTE = 59


# ===========================================================================
# SECTION 8: STARTUP SANITY CHECK
# ===========================================================================
def validate_config():
    """
    Print clear warnings if any required secret is missing. Call this once at
    program startup (main.py does). It does NOT stop the program, but it tells
    you exactly what to fix instead of letting you hit a cryptic error later.
    Returns True if everything required is present, False otherwise.
    """
    ok = True
    if not EMAIL_APP_PASSWORD:
        print("[config] WARNING: EMAIL_APP_PASSWORD is empty. "
              "Add it to your .env file. E-mail features will not work.")
        ok = False
    if not THINGSPEAK_WRITE_KEY:
        print("[config] WARNING: THINGSPEAK_WRITE_KEY is empty. "
              "Add it to your .env file. ThingSpeak uploads will not work.")
        ok = False
    return ok


# Allow quick manual checking:  python config.py
if __name__ == "__main__":
    print("Smart Home IoT configuration loaded.")
    print(f"  Serial port      : {SERIAL_PORT} @ {SERIAL_BAUD} baud")
    print(f"  Email account    : {EMAIL_ADDRESS}")
    print(f"  Alert recipient  : {EMAIL_ALERT_RECIPIENT}")
    print(f"  Authorised senders: {len(AUTHORISED_SENDERS)} address(es)")
    print(f"  ThingSpeak channel: {THINGSPEAK_CHANNEL_ID}")
    print()
    validate_config()
