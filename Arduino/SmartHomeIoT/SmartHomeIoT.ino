/*

  WIRING :
    A0  - LM35 temperature sensor (analog)
    A1  - LDR illumination divider (analog)
    D2  - HC-SR501 PIR motion sensor OUT (digital in)
    D3  - Emergency push button (digital in, uses internal pull-up)
    D4  - Relay 1 IN  -> Heating  (100ohm heater + yellow RGB indicator)
    D5  - Relay 2 IN  -> Cooling  (DC fan + blue RGB indicator)
    D6  - Relay 3 IN  -> Lighting (white RGB LED)
    D7  - Emergency RGB LED, BLUE leg  (not used)
    D8  - Emergency RGB LED, RED leg   (emergency ACTIVE indicator)
    D9  - Emergency RGB LED, GREEN leg (emergency INACTIVE indicator)

  RELAY POLARITY:
    The relay module is ACTIVE-LOW: writing LOW to an IN pin turns that
    relay ON; writing HIGH turns it OFF. Helper functions wrap this so the rest of the code uses plain ON/OFF.

  SERIAL PROTOCOL (the contract with Python):
    Arduino -> PC:
      On boot:           READY;SmartHome;v1
      Periodic / on change, a single self-describing snapshot line:
        STATUS;TEMP:22.4;LUX:45;MOTION:0;HEAT:0;COOL:0;LIGHT:0;ATC:1;SECURE:0;LAUTO:1;EMERG:0
      Discrete events : EVENT:MOTION / EVENT:EMERGENCY
    PC -> Arduino (one command per line, newline terminated):
      STATUS  -> force an immediate STATUS line
      
  =========================================================================
*/

// ------------------------------------------------------------------------
// PIN DEFINITIONS
// ------------------------------------------------------------------------
const int PIN_LM35   = A0;   // temperature sensor (analog)
const int PIN_LDR    = A1;   // illumination divider (analog)
const int PIN_PIR    = 2;    // motion sensor OUT
const int PIN_BUTTON = 3;    // emergency button (active-low w/ pull-up)
const int PIN_RELAY_HEAT  = 4;   // relay 1 - heating
const int PIN_RELAY_COOL  = 5;   // relay 2 - cooling
const int PIN_RELAY_LIGHT = 6;   // relay 3 - lighting
const int PIN_RGB_BLUE  = 7;   // emergency LED blue  (unused)
const int PIN_RGB_RED   = 8;   // emergency LED red   (active)
const int PIN_RGB_GREEN = 9;   // emergency LED green (inactive)

// ------------------------------------------------------------------------
// TIMING
// ------------------------------------------------------------------------
// Measurement interval in ms. Spec value is 600000 (10 min); 
// a smaller value can be used to observe behavior more frequently.
//   TEST  : 5000   (5 seconds)
//   DEMO  : 600000 (10 minutes)
#define MEASURE_INTERVAL_MS 5000UL

unsigned long lastMeasure = 0;     // timestamp of last measurement

// ------------------------------------------------------------------------
// TEMPERATURE CONTROL THRESHOLDS  
// ------------------------------------------------------------------------
// Hysteresis (a deliberate gap between switch-on and switch-off points) stops
// the relays "chattering" rapidly when the temperature hovers near a limit.
//   Cooling: turns ON above 23 C, stays on until temp falls to 17 C.
//   Heating: turns ON below 17 C, stays on until temp rises to 23 C.
const float TEMP_COOL_ON_ABOVE  = 23.0;  // > this  -> start cooling
const float TEMP_COOL_OFF_BELOW = 17.0;  // cooling runs until temp <= this
const float TEMP_HEAT_ON_BELOW  = 17.0;  // < this  -> start heating
const float TEMP_HEAT_OFF_ABOVE = 23.0;  // heating runs until temp >= this

// ------------------------------------------------------------------------
// LIGHT AUTO MODE THRESHOLD  
// ------------------------------------------------------------------------
// When light auto mode is enabled: if illumination is below this percent the
// light turns ON, otherwise it turns OFF.
const int LIGHT_AUTO_ON_BELOW_PCT = 30;  // illumination < 30% -> light on

// ------------------------------------------------------------------------
// HOME SECURE MODE TIMING 
// ------------------------------------------------------------------------
// In secure mode, motion turns the light on. If no further motion occurs for
// this many milliseconds, the light turns off. Only ONE e-mail notification
// is sent per motion event (the window resets on continued motion).
#define SECURE_LIGHT_TIMEOUT_MS 10000UL   // 10 seconds

// Button debounce: ignore button state changes faster than this (ms). Real
// presses are far slower than contact "bounce", so this filters the bounce.
#define BUTTON_DEBOUNCE_MS 50UL

// ------------------------------------------------------------------------
// LDR CALIBRATION
// ------------------------------------------------------------------------
// A1 reads higher with more light. The raw 0..1023 ADC value is mapped 
// linearly to a 0..100 illumination percentage (bright = high).
int readIlluminationPercent() {
  int raw = analogRead(PIN_LDR);          // 0..1023, higher = brighter
  int pct = map(raw, 0, 1023, 0, 100);    // linear map to 0..100
  if (pct < 0)   pct = 0;
  if (pct > 100) pct = 100;
  return pct;
}

// ------------------------------------------------------------------------
// LM35 TEMPERATURE READ  (MEDIAN filter to reject fan-induced noise spikes)
// ------------------------------------------------------------------------
// LM35 outputs 10 mV per degree C. analogRead is 0..1023 across 0..5V.
//   volts = raw * 5.0 / 1024
//   tempC = volts / 0.01
//
// WHY A MEDIAN (not an average): the DC fan injects electrical noise onto the
// shared 5V/GND rails, which corrupts the LM35's high-impedance analog signal
// and produces large transient spikes (readings jumping to 50-90 C while the
// true temperature is ~30 C). An AVERAGE is pulled upward by those spikes. A
// MEDIAN ignores them: we take many samples, sort them, and keep the middle
// value. Even if a large fraction of samples are spikes, the middle sample is
// still a real reading. This is a firmware mitigation for the missing hardware
// filter capacitor; it is not perfectly accurate but is stable enough for the
// heating/cooling threshold logic to work reliably.
float readTemperatureC() {
  const int N = 15;       // odd number so there is a clear middle
  int samples[N];

  // 1) Collect N raw readings as fast as practical.
  for (int i = 0; i < N; i++) {
    samples[i] = analogRead(PIN_LM35);
    delay(2);
  }

  // 2) Simple insertion sort (N is small, so this is fast).
  for (int i = 1; i < N; i++) {
    int key = samples[i];
    int j = i - 1;
    while (j >= 0 && samples[j] > key) {
      samples[j + 1] = samples[j];
      j--;
    }
    samples[j + 1] = key;
  }

  // 3) The median is the middle element after sorting.
  int medianRaw = samples[N / 2];

  // 4) Convert the median ADC value to degrees Celsius.
  float volts = medianRaw * (5.0 / 1024.0);
  float tempC = volts / 0.01;             // 10 mV per degree C
  return tempC;
}

// ------------------------------------------------------------------------
// STATE VARIABLES
// ------------------------------------------------------------------------
// These mirror exactly the tokens in the STATUS line and the SQLite columns.
bool stateHeat   = false;   // heating relay on?
bool stateCool   = false;   // cooling relay on?
bool stateLight  = false;   // lighting relay on?
bool stateATC    = true;    // automatic temperature control enabled?
bool stateSecure = false;   // home secure mode enabled?
bool stateLAuto  = true;    // light auto mode enabled?
bool stateEmerg  = false;   // emergency mode active?
int  motionNow   = 0;       // current PIR reading (0/1)

// --- Home Secure Mode internal state ---
// secureLightOn: true while secure mode is holding the light on after motion.
// secureLastMotion: timestamp of the most recent motion within the window.
// secureNotified: true once we have emitted EVENT:MOTION for the current
//   window, so we only send ONE notification per event (resets when the
//   window expires).
bool          secureLightOn   = false;
unsigned long secureLastMotion = 0;
bool          secureNotified  = false;

// --- Emergency button debounce state ---
// lastButtonReading: the most recent raw reading (HIGH = released, LOW = pressed,
//   because of INPUT_PULLUP). lastButtonStable: the debounced stable level.
// lastButtonChange: when the raw reading last changed (for debounce timing).
int           lastButtonReading = HIGH;
int           lastButtonStable  = HIGH;
unsigned long lastButtonChange  = 0;

// ------------------------------------------------------------------------
// RELAY HELPERS  (hide the active-LOW detail)
// ------------------------------------------------------------------------
void applyHeat(bool on)  {
  digitalWrite(PIN_RELAY_HEAT,  on ? LOW : HIGH);
  stateHeat  = on;
}
void applyCool(bool on)  {
  digitalWrite(PIN_RELAY_COOL,  on ? LOW : HIGH);
  stateCool  = on;
}
void applyLight(bool on) {
  digitalWrite(PIN_RELAY_LIGHT, on ? LOW : HIGH);
  stateLight = on;
}

// ------------------------------------------------------------------------
// INTERLOCKED HEAT/COOL SETTERS  (THE SAFETY RULE: never both on)
// ------------------------------------------------------------------------
// Heating and cooling can NEVER run at the same time. Rather
// than trust every call site to remember this, we funnel all heat/cool changes
// through these two functions, which physically force the opposite system off
// first. This guarantees the interlock no matter who calls it (the automatic
// thermostat, a manual command, or emergency handling).
void setHeating(bool on) {
  if (on) applyCool(false);   // turning heating ON forces cooling OFF
  applyHeat(on);
}
void setCooling(bool on) {
  if (on) applyHeat(false);   // turning cooling ON forces heating OFF
  applyCool(on);
}

// ------------------------------------------------------------------------
// AUTOMATIC TEMPERATURE CONTROL (ATC) STATE MACHINE
// ------------------------------------------------------------------------
// Called every measurement cycle. Only does anything when ATC is enabled AND
// emergency mode is not active. Implements the hysteresis described above.
//
// The logic, expressed as states:
//   - If currently cooling: keep cooling until temp drops to the OFF point.
//   - If currently heating: keep heating until temp rises to the OFF point.
//   - If idle: start cooling if too hot, start heating if too cold.
// The interlock is guaranteed because we only ever drive one system, and we
// route through setHeating()/setCooling() which force the other off.
void runTemperatureControl(float tempC) {
  if (!stateATC)   return;   // automatic control disabled -> do nothing
  if (stateEmerg)  return;   // emergency overrides everything 

  if (stateCool) {
    // Currently cooling: stop once we have cooled to the lower limit.
    if (tempC <= TEMP_COOL_OFF_BELOW) {
      setCooling(false);
    }
  }
  else if (stateHeat) {
    // Currently heating: stop once we have warmed to the upper limit.
    if (tempC >= TEMP_HEAT_OFF_ABOVE) {
      setHeating(false);
    }
  }
  else {
    // Idle: decide whether to start heating or cooling.
    if (tempC > TEMP_COOL_ON_ABOVE) {
      setCooling(true);
    } else if (tempC < TEMP_HEAT_ON_BELOW) {
      setHeating(true);
    }
    // Between 17 and 23 with nothing running: stay idle (the comfortable band).
  }
}

// ------------------------------------------------------------------------
// LIGHT AUTO MODE STATE MACHINE
// ------------------------------------------------------------------------
// Called every measurement cycle. When light auto mode is enabled (and we are
// not in emergency mode), the light follows the ambient illumination:
//   illumination < 30%  -> light ON
//   illumination >= 30% -> light OFF
// A MANUAL light command (LIGHT_ON / LIGHT_OFF) disables this mode; that rule
// lives in the command parser, mirroring the manual/ATC rule for heat/cool.
void runLightAuto(int illuminationPct) {
  if (!stateLAuto) return;   // auto mode disabled -> manual control only
  if (stateEmerg)  return;   // emergency overrides everything

  // If secure mode is currently holding the light on after motion, don't let
  // light-auto override it (secure mode takes priority over auto while its
  // 10s window is active). Light-auto resumes once the secure hold expires.
  if (stateSecure && secureLightOn) return;

  if (illuminationPct < LIGHT_AUTO_ON_BELOW_PCT) {
    applyLight(true);
  } else {
    applyLight(false);
  }
}

// ------------------------------------------------------------------------
// HOME SECURE MODE
// ------------------------------------------------------------------------
// Runs every loop (motion can happen at any instant, not just on the measure
// tick). When secure mode is enabled and NOT in emergency:
//   - On motion: turn the light ON, (re)start the 10s timer, and emit ONE
//     EVENT:MOTION per event window (so Python sends exactly one e-mail).
//   - Continued motion keeps resetting the timer (light stays on, no new
//     event).
//   - After 10s with no motion: turn the light OFF and reset, so the next
//     intrusion is treated as a fresh event.
// Note: secure mode drives the light directly here. It is independent of light
// auto mode; 
void runSecureMode() {
  if (stateEmerg)  return;   // emergency handles the light itself
  if (!stateSecure) {
    // Secure mode just turned off (or never on): clear any held secure light.
    if (secureLightOn) {
      secureLightOn  = false;
      secureNotified = false;
      applyLight(false);
    }
    return;
  }

  unsigned long now = millis();

  if (motionNow == 1) {
    // Motion present: light on, restart the no-motion timer.
    applyLight(true);
    secureLightOn   = true;
    secureLastMotion = now;
    // Emit the notification only once per event window.
    if (!secureNotified) {
      Serial.println("EVENT:MOTION");
      secureNotified = true;
    }
  }
  else if (secureLightOn) {
    // No motion right now, but the light is being held on from a prior event.
    if (now - secureLastMotion >= SECURE_LIGHT_TIMEOUT_MS) {
      // 10s of quiet -> turn the light off and reset for the next event.
      applyLight(false);
      secureLightOn  = false;
      secureNotified = false;
    }
  }
}

// ------------------------------------------------------------------------
// EMERGENCY MODE ACTIVATION  (button only)
// ------------------------------------------------------------------------
// When the physical button is pressed, the system enters emergency mode:
//   everything OFF (heating, cooling, light, light-auto, ATC), secure mode
//   FORCED ON, red LED on / green off, and EVENT:EMERGENCY emitted so Python
//   sends the emergency e-mail. Emergency latches until EMERGENCY_OFF arrives
//   by command (the ONLY way out). If already in emergency, a button press
//   does nothing.
void activateEmergency() {
  if (stateEmerg) return;          // already in emergency
  stateEmerg = true;

  // Force everything to the safe state.
  setHeating(false);
  setCooling(false);
  applyLight(false);
  stateATC    = false;             // automatic temp control off
  stateLAuto  = false;             // light auto off
  stateSecure = true;              // secure mode forced ON

  // Reset secure-mode internals so it behaves cleanly under emergency.
  secureLightOn  = false;
  secureNotified = false;

  updateEmergencyLed();            // red on, green off
  Serial.println("EVENT:EMERGENCY");
  sendStatus();
}

// ------------------------------------------------------------------------
// EMERGENCY DEACTIVATION  (command only: EMERGENCY_OFF)
// ------------------------------------------------------------------------
// Clears emergency mode. This is the ONLY way out of emergency.
// Systems remain off (green LED back on); the user re-enables what they want
// via normal commands. Secure mode is left as-is (it was forced on).
void deactivateEmergency() {
  if (!stateEmerg) return;
  stateEmerg = false;
  updateEmergencyLed();            // green on, red off
  sendStatus();
}

// ------------------------------------------------------------------------
// EMERGENCY BUTTON READ  (debounced edge detection)
// ------------------------------------------------------------------------
// With INPUT_PULLUP, the pin reads HIGH when released and LOW when pressed.
// The reading is debounced: only accept a level once it has been stable for the debounce
// period, and trigger emergency on the falling edge (HIGH -> LOW), i.e. a
// clean press. Holding the button does not re-trigger.
void readEmergencyButton() {
  int reading = digitalRead(PIN_BUTTON);
  unsigned long now = millis();

  if (reading != lastButtonReading) {
    lastButtonReading = reading;
    lastButtonChange  = now;       // raw level changed; start debounce timer
  }

  if (now - lastButtonChange >= BUTTON_DEBOUNCE_MS) {
    // Reading has been stable long enough to be considered real.
    if (reading != lastButtonStable) {
      lastButtonStable = reading;
      if (lastButtonStable == LOW) {
        // Clean press detected (falling edge): activate emergency.
        activateEmergency();
      }
    }
  }
}

// ------------------------------------------------------------------------
// EMERGENCY INDICATOR LED  (common-cathode: HIGH lights the leg)
// ------------------------------------------------------------------------
// Green on when emergency INACTIVE, red on when ACTIVE. Blue always off.
void updateEmergencyLed() {
  digitalWrite(PIN_RGB_BLUE,  LOW);                 // unused
  digitalWrite(PIN_RGB_RED,   stateEmerg ? HIGH : LOW);
  digitalWrite(PIN_RGB_GREEN, stateEmerg ? LOW  : HIGH);
}

// ------------------------------------------------------------------------
// EMIT THE STATUS LINE
// ------------------------------------------------------------------------
void sendStatus() {
  float t = readTemperatureC();
  int   l = readIlluminationPercent();

  Serial.print("STATUS;TEMP:");
  Serial.print(t, 1);                 // one decimal place
  Serial.print(";LUX:");
  Serial.print(l);
  Serial.print(";MOTION:");
  Serial.print(motionNow);
  Serial.print(";HEAT:");
  Serial.print(stateHeat ? 1 : 0);
  Serial.print(";COOL:");
  Serial.print(stateCool ? 1 : 0);
  Serial.print(";LIGHT:");
  Serial.print(stateLight ? 1 : 0);
  Serial.print(";ATC:");
  Serial.print(stateATC ? 1 : 0);
  Serial.print(";SECURE:");
  Serial.print(stateSecure ? 1 : 0);
  Serial.print(";LAUTO:");
  Serial.print(stateLAuto ? 1 : 0);
  Serial.print(";EMERG:");
  Serial.println(stateEmerg ? 1 : 0);
}

// ------------------------------------------------------------------------
// COMMAND PARSER
// ------------------------------------------------------------------------
// Reads one newline-terminated command and acts on it.
//
// MANUAL/AUTOMATIC RULE: a manual heating or cooling command
// takes control away from the automatic system, so it sets ATC = OFF. ATC can
// then only be re-enabled with the ATC_ON command (i.e. via e-mail). All
// heat/cool changes go through the interlocked setters so heating and cooling
// can never both be on. (Light auto mode has the mirror-image rule)
void handleCommand(String cmd) {
  cmd.trim();                    // remove whitespace / CR
  cmd.toUpperCase();             // accept any case
  if (cmd.length() == 0) return;

  if      (cmd == "STATUS")     {
    sendStatus();
    return;
  }
  else if (cmd == "EMERGENCY_OFF") {
    deactivateEmergency();
    return;
  }

  // While emergency is active, ignore all other commands except EMERGENCY_OFF
  // and STATUS (handled above). Emergency suppresses normal control until
  // explicitly cleared.
  if (stateEmerg) {
    Serial.println("IGNORED_DURING_EMERGENCY");
    return;
  }

  if      (cmd == "HEAT_ON")    {
    stateATC = false;
    setHeating(true);
  }
  else if (cmd == "HEAT_OFF")   {
    stateATC = false;
    setHeating(false);
  }
  else if (cmd == "COOL_ON")    {
    stateATC = false;
    setCooling(true);
  }
  else if (cmd == "COOL_OFF")   {
    stateATC = false;
    setCooling(false);
  }
  else if (cmd == "LIGHT_ON")   {
    stateLAuto = false;
    applyLight(true);
  }
  else if (cmd == "LIGHT_OFF")  {
    stateLAuto = false;
    applyLight(false);
  }
  else if (cmd == "ATC_ON")     {
    stateATC = true;
  }
  else if (cmd == "ATC_OFF")    {
    stateATC = false;
  }
  else if (cmd == "LAUTO_ON")   {
    stateLAuto = true;
  }
  else if (cmd == "LAUTO_OFF")  {
    stateLAuto = false;
  }
  else if (cmd == "SECURE_ON")  {
    stateSecure = true;
  }
  else if (cmd == "SECURE_OFF") {
    stateSecure = false;
  }
  else {
    // Unknown command: report it but do not act. 
    Serial.print("UNKNOWN_CMD:");
    Serial.println(cmd);
    return;
  }
  // After any recognised state-changing command, emit a fresh snapshot so
  // Python immediately sees the result of its command.
  sendStatus();
}

// ------------------------------------------------------------------------
// SERIAL INPUT BUFFERING
// ------------------------------------------------------------------------
//Accumulate characters until a newline, then dispatch the assembled command; 
//ignore carriage returns."
String inputBuffer = "";

void readSerial() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n') {
      handleCommand(inputBuffer);
      inputBuffer = "";
    } else if (c != '\r') {
      inputBuffer += c;
    }
  }
}

// ------------------------------------------------------------------------
// SETUP
// ------------------------------------------------------------------------
void setup() {
  Serial.begin(9600);

  pinMode(PIN_PIR, INPUT);
  pinMode(PIN_BUTTON, INPUT_PULLUP);

  pinMode(PIN_RELAY_HEAT, OUTPUT);
  pinMode(PIN_RELAY_COOL, OUTPUT);
  pinMode(PIN_RELAY_LIGHT, OUTPUT);
  pinMode(PIN_RGB_BLUE, OUTPUT);
  pinMode(PIN_RGB_RED, OUTPUT);
  pinMode(PIN_RGB_GREEN, OUTPUT);

  // Start with all relays OFF (active-LOW => write HIGH) and emergency
  // indicator showing green (inactive).
  applyHeat(false);
  applyCool(false);
  applyLight(false);
  updateEmergencyLed();

  Serial.println("READY;SmartHome;v1");
  lastMeasure = millis();
  sendStatus();                  // send one snapshot right away
}

// ------------------------------------------------------------------------
// MAIN LOOP
// ------------------------------------------------------------------------
void loop() {
  // 1) Always service incoming commands promptly.
  readSerial();

  // 2) Read the PIR each loop so motionNow is current.
  motionNow = digitalRead(PIN_PIR);

  // 3) Check the emergency button (debounced edge detection).
  readEmergencyButton();

  // 4) Run secure mode every loop (motion can occur at any instant, and the
  //    10s light-off timer needs sub-second responsiveness).
  runSecureMode();

  // 5) Keep the emergency indicator correct (cheap, harmless every loop).
  updateEmergencyLed();

  // 6) On the measurement interval, run automatic temperature/light control
  //    and emit a periodic STATUS snapshot. (Skipped during emergency: the
  //    control functions themselves no-op when stateEmerg is true.)
  unsigned long now = millis();
  if (now - lastMeasure >= MEASURE_INTERVAL_MS) {
    lastMeasure = now;
    float tempC = readTemperatureC();   // read once for the control decision
    int   lux   = readIlluminationPercent();
    runTemperatureControl(tempC);       // automatic heat/cool (if ATC on)
    runLightAuto(lux);                  // automatic light (if light auto on)
    sendStatus();                       // report the (possibly updated) state
  }
}
