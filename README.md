# Smart Home IoT

Arduino Uno + Python smart-home control system. The Arduino handles sensors and
real-time control; a Python application on the PC handles e-mail control,
ThingSpeak uploads, local logging, and a daily report. They communicate over USB
serial.

## Requirements

- Python 3.10+
- An Arduino Uno (with the firmware from `Arduino/SmartHomeIoT.ino` uploaded)
- A Gmail account with 2-Step Verification and an App Password
- A ThingSpeak channel (fields: 1 = Temperature, 2 = Illumination, 3 = Motion)

## Setup

1. **Clone the repository**
   ```
   git clone https://github.com/DusanDotlic/SmartHomeIoT.git
   cd SmartHomeIoT/Python
   ```

2. **Install dependencies**
   ```
   pip install -r requirements.txt
   ```

3. **Create the secrets file.** Copy `.env.example` to `.env` and fill in your
   two credentials:
   ```
   EMAIL_APP_PASSWORD=your_gmail_app_password
   THINGSPEAK_WRITE_KEY=your_thingspeak_write_key
   ```

4. **Check `config.py`.** Set:
   - `SERIAL_PORT` — the Arduino's COM port (Arduino IDE → Tools → Port; often
     `COM3`, may differ on another machine).
   - `EMAIL_ADDRESS` and `AUTHORISED_SENDERS` — the project account and the
     addresses allowed to send commands.
   - `THINGSPEAK_CHANNEL_ID` — your channel ID.

5. **Upload the firmware.** Open `Arduino/SmartHomeIoT.ino` in the Arduino IDE,
   select board **Arduino Uno** and the correct **Port**, then **Upload**.

## Run

Close the Arduino IDE Serial Monitor first (only one program can use the COM port
at a time), then:

```
python main.py
```

The system connects to the Arduino, logs readings to a local SQLite database,
uploads to ThingSpeak, polls Gmail for command e-mails, and sends alert e-mails.

To build and e-mail the daily report immediately instead of waiting for the
scheduled time:

```
python main.py report
```

Press **Ctrl+C** to stop.

## E-mail commands

Send an e-mail (from an authorised address) with one of these exact subjects,
one command per e-mail:

```
HEAT_ON / HEAT_OFF      COOL_ON / COOL_OFF      ATC_ON / ATC_OFF
LIGHT_ON / LIGHT_OFF    LAUTO_ON / LAUTO_OFF    SECURE_ON / SECURE_OFF
EMERGENCY_OFF           STATUS
```
