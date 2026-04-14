#include <Arduino.h>
#include <ArduinoJson.h>
#include <M5Unified.h>
#include <Adafruit_NeoPixel.h>
#include <esp_timer.h>

// Hardware mapping
// AtomS3 Lite ESP32S3 Dev Kit SKU: C124
// ATOMIC PortABC Extension Base SKU: A130
// RGB LED Unit (SK6812) SKU: U003 x2 (Port A and Port C)
// Mini Dual Button Unit SKU: U025 (bottom port)

constexpr uint8_t PIN_BTN_RED = 2;
constexpr uint8_t PIN_BTN_BLUE = 1;

constexpr uint8_t PIN_LED_FLASH_1 = 5;   // Port C
constexpr uint8_t PIN_LED_FLASH_2 = 38;  // Port A

constexpr int NUM_LEDS = 3;
constexpr int ACTIVE_PIXEL = 1;

// Default stimulus brightness. The host can override this per session/trial
// by sending `flash_rgb_level`, so this is only the fallback value.
constexpr uint8_t DEFAULT_FLASH_RGB_LEVEL = 84;
const uint32_t COLOR_SESSION_RED = Adafruit_NeoPixel::Color(84, 0, 0);
const uint32_t COLOR_SESSION_YELLOW = Adafruit_NeoPixel::Color(84, 64, 0);
const uint32_t COLOR_SESSION_GREEN = Adafruit_NeoPixel::Color(0, 84, 0);

constexpr size_t SERIAL_LINE_CAPACITY = 512;
constexpr uint32_t BUTTON_RELEASE_TIMEOUT_MS = 1200;
constexpr uint32_t BUTTON_DEBOUNCE_MS = 20;
constexpr uint32_t SESSION_PREP_STEP_MS = 2000;
constexpr uint32_t SESSION_PREP_OFF_MS = 2000;
constexpr uint32_t SESSION_COMPLETE_HOLD_MS = 700;

const char* PROTOCOL_VERSION = "tdt-1";
const char* FIRMWARE_VERSION = "0.1.7";

Adafruit_NeoPixel flash1(NUM_LEDS, PIN_LED_FLASH_1, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel flash2(NUM_LEDS, PIN_LED_FLASH_2, NEO_GRB + NEO_KHZ800);

String incomingLine;
bool deviceBusy = false;

struct TrialCommand {
  long trialId = 0;
  uint32_t soaMs = 0;
  uint32_t flashMs = 10;
  uint8_t flashRgbLevel = DEFAULT_FLASH_RGB_LEVEL;
  uint32_t responseTimeoutMs = 8000;
  uint32_t prestimDelayMs = 500;
  String phase = "main";
  String leadLed = "simultaneous";
};

struct TrialResult {
  long trialId = 0;
  String phase = "main";
  uint32_t soaMs = 0;
  uint32_t flashMs = 10;
  uint8_t flashRgbLevel = DEFAULT_FLASH_RGB_LEVEL;
  String leadLed = "simultaneous";
  String button = "none";
  String response = "timeout";
  long rtMs = -1;
  bool timedOut = true;
  bool invalid = false;
};

void clearStrip(Adafruit_NeoPixel& strip) {
  strip.clear();
  strip.show();
}

void setStripAll(Adafruit_NeoPixel& strip, uint32_t color) {
  strip.clear();
  if (color != 0) {
    for (int pixel = 0; pixel < NUM_LEDS; ++pixel) {
      strip.setPixelColor(pixel, color);
    }
  }
  strip.show();
}

void setStripPixel(Adafruit_NeoPixel& strip, bool enabled, uint32_t color) {
  strip.clear();
  if (enabled) {
    strip.setPixelColor(ACTIVE_PIXEL, color);
  }
  strip.show();
}

uint32_t stimulusColor(uint8_t rgbLevel) {
  return Adafruit_NeoPixel::Color(rgbLevel, rgbLevel, rgbLevel);
}

void setIdleIndicator() {
  clearStrip(flash1);
  clearStrip(flash2);
}

void setSessionIndicator(uint32_t color) {
  setStripAll(flash1, color);
  setStripAll(flash2, color);
}

void showSessionPrepSequence() {
  setSessionIndicator(COLOR_SESSION_RED);
  delay(SESSION_PREP_STEP_MS);
  setSessionIndicator(COLOR_SESSION_YELLOW);
  delay(SESSION_PREP_STEP_MS);
  setSessionIndicator(COLOR_SESSION_GREEN);
  delay(SESSION_PREP_STEP_MS);
  setIdleIndicator();
  delay(SESSION_PREP_OFF_MS);
}

void showSessionCompleteSequence() {
  setSessionIndicator(COLOR_SESSION_RED);
  delay(SESSION_COMPLETE_HOLD_MS);
  setSessionIndicator(COLOR_SESSION_RED);
}

String deviceId() {
  uint64_t chipId = ESP.getEfuseMac();
  char buffer[17];
  snprintf(buffer, sizeof(buffer), "%08lX%08lX",
           static_cast<unsigned long>(chipId >> 32),
           static_cast<unsigned long>(chipId & 0xFFFFFFFF));
  return String(buffer);
}

template <typename TDocument>
void sendJson(const TDocument& doc) {
  serializeJson(doc, Serial);
  Serial.println();
}

void sendStateAck(const char* type, const char* state) {
  JsonDocument doc;
  doc["type"] = type;
  doc["state"] = state;
  doc["timestamp_ms"] = millis();
  sendJson(doc);
}

void sendError(const char* message, long trialId = -1) {
  JsonDocument doc;
  doc["type"] = "error";
  doc["message"] = message;
  doc["timestamp_ms"] = millis();
  if (trialId >= 0) {
    doc["trial_id"] = trialId;
  }
  sendJson(doc);
}

void sendHelloAck() {
  JsonDocument doc;
  doc["type"] = "hello_ack";
  doc["protocol"] = PROTOCOL_VERSION;
  doc["device"] = "m5stack-atoms3-lite";
  doc["firmware"] = FIRMWARE_VERSION;
  doc["device_id"] = deviceId();
  doc["busy"] = deviceBusy;
  doc["timestamp_ms"] = millis();

  JsonObject buttons = doc["buttons"].to<JsonObject>();
  buttons["blue"] = "yes";
  buttons["red"] = "no";

  JsonObject stimulus = doc["stimulus"].to<JsonObject>();
  stimulus["flash_1_pin"] = PIN_LED_FLASH_1;
  stimulus["flash_2_pin"] = PIN_LED_FLASH_2;
  stimulus["mode"] = "dual_led";
  stimulus["default_flash_rgb_level"] = DEFAULT_FLASH_RGB_LEVEL;
  stimulus["note"] = "Flash 1 and Flash 2 are emitted by separate LED modules.";

  sendJson(doc);
}

bool bluePressed() {
  return digitalRead(PIN_BTN_BLUE) == LOW;
}

bool redPressed() {
  return digitalRead(PIN_BTN_RED) == LOW;
}

void pollM5() {
  M5.update();
}

bool waitForButtonsReleased(uint32_t timeoutMs) {
  uint32_t startMs = millis();
  while (millis() - startMs < timeoutMs) {
    pollM5();
    if (!bluePressed() && !redPressed()) {
      return true;
    }
    delay(1);
  }
  return false;
}

void runFlashSequence(uint32_t soaMs, uint32_t flashMs, const String& leadLed, uint8_t flashRgbLevel) {
  const uint64_t startUs = esp_timer_get_time();
  const uint64_t flashDurationUs = static_cast<uint64_t>(flashMs) * 1000ULL;
  const uint64_t soaUs = static_cast<uint64_t>(soaMs) * 1000ULL;
  const uint32_t flashColor = stimulusColor(flashRgbLevel);

  const bool simultaneous = (soaMs == 0) || (leadLed == "simultaneous");
  const bool flash2First = !simultaneous && (leadLed == "flash2");

  const uint64_t flash1StartUs = flash2First ? (startUs + soaUs) : startUs;
  const uint64_t flash2StartUs = flash2First ? startUs : (startUs + soaUs);
  const uint64_t flash1EndUs = flash1StartUs + flashDurationUs;
  const uint64_t flash2EndUs = flash2StartUs + flashDurationUs;
  const uint64_t sequenceEndUs = flash1EndUs > flash2EndUs ? flash1EndUs : flash2EndUs;

  bool flash1State = false;
  bool flash2State = false;

  clearStrip(flash1);
  clearStrip(flash2);

  while (esp_timer_get_time() < sequenceEndUs) {
    const uint64_t nowUs = esp_timer_get_time();
    const bool wantFlash1 = nowUs >= flash1StartUs && nowUs < flash1EndUs;
    const bool wantFlash2 = nowUs >= flash2StartUs && nowUs < flash2EndUs;

    if (wantFlash1 != flash1State) {
      setStripPixel(flash1, wantFlash1, flashColor);
      flash1State = wantFlash1;
    }
    if (wantFlash2 != flash2State) {
      setStripPixel(flash2, wantFlash2, flashColor);
      flash2State = wantFlash2;
    }

    delayMicroseconds(150);
  }

  clearStrip(flash1);
  clearStrip(flash2);
}

TrialResult waitForResponse(const TrialCommand& command, uint64_t responseWindowStartUs) {
  TrialResult result;
  result.trialId = command.trialId;
  result.phase = command.phase;
  result.soaMs = command.soaMs;
  result.flashMs = command.flashMs;
  result.flashRgbLevel = command.flashRgbLevel;
  result.leadLed = command.leadLed;

  const uint32_t startMs = millis();
  uint32_t blueDownSince = 0;
  uint32_t redDownSince = 0;

  while (millis() - startMs < command.responseTimeoutMs) {
    pollM5();
    const bool blue = bluePressed();
    const bool red = redPressed();
    const uint32_t nowMs = millis();

    if (blue && red) {
      if (blueDownSince == 0) {
        blueDownSince = nowMs;
      }
      if (redDownSince == 0) {
        redDownSince = nowMs;
      }
      if (nowMs - blueDownSince >= BUTTON_DEBOUNCE_MS &&
          nowMs - redDownSince >= BUTTON_DEBOUNCE_MS) {
        result.button = "both";
        result.response = "invalid";
        result.invalid = true;
        result.timedOut = false;
        result.rtMs = static_cast<long>((esp_timer_get_time() - responseWindowStartUs) / 1000ULL);
        return result;
      }
      delay(1);
      continue;
    }

    if (blue) {
      if (blueDownSince == 0) {
        blueDownSince = nowMs;
      } else if (nowMs - blueDownSince >= BUTTON_DEBOUNCE_MS) {
        result.button = "blue";
        result.response = "yes";
        result.rtMs = static_cast<long>((esp_timer_get_time() - responseWindowStartUs) / 1000ULL);
        result.timedOut = false;
        return result;
      }
    } else {
      blueDownSince = 0;
    }

    if (red) {
      if (redDownSince == 0) {
        redDownSince = nowMs;
      } else if (nowMs - redDownSince >= BUTTON_DEBOUNCE_MS) {
        result.button = "red";
        result.response = "no";
        result.rtMs = static_cast<long>((esp_timer_get_time() - responseWindowStartUs) / 1000ULL);
        result.timedOut = false;
        return result;
      }
    } else {
      redDownSince = 0;
    }

    delay(1);
  }

  return result;
}

void sendTrialStarted(const TrialCommand& command) {
  JsonDocument doc;
  doc["type"] = "trial_started";
  doc["trial_id"] = command.trialId;
  doc["phase"] = command.phase;
  doc["soa_ms"] = command.soaMs;
  doc["lead_led"] = command.leadLed;
  doc["flash_ms"] = command.flashMs;
  doc["flash_rgb_level"] = command.flashRgbLevel;
  doc["timestamp_ms"] = millis();
  sendJson(doc);
}

void sendTrialResult(const TrialResult& result) {
  JsonDocument doc;
  doc["type"] = "trial_result";
  doc["trial_id"] = result.trialId;
  doc["phase"] = result.phase;
  doc["soa_ms"] = result.soaMs;
  doc["lead_led"] = result.leadLed;
  doc["flash_ms"] = result.flashMs;
  doc["flash_rgb_level"] = result.flashRgbLevel;
  doc["button"] = result.button;
  doc["response"] = result.response;
  doc["rt_ms"] = result.rtMs;
  doc["timed_out"] = result.timedOut;
  doc["invalid"] = result.invalid;
  doc["timestamp_ms"] = millis();
  sendJson(doc);
}

bool parseTrialCommand(JsonVariantConst doc, TrialCommand& command) {
  if (!doc["trial_id"].is<long>() && !doc["trial_id"].is<int>()) {
    return false;
  }

  command.trialId = doc["trial_id"].as<long>();
  command.soaMs = doc["soa_ms"] | 0;
  command.flashMs = doc["flash_ms"] | 10;
  command.flashRgbLevel = static_cast<uint8_t>(constrain(doc["flash_rgb_level"] | DEFAULT_FLASH_RGB_LEVEL, 0, 255));
  command.responseTimeoutMs = doc["response_timeout_ms"] | 8000;
  command.prestimDelayMs = doc["prestim_delay_ms"] | 500;
  command.phase = String(doc["phase"] | "main");
  command.leadLed = String(doc["lead_led"] | (command.soaMs == 0 ? "simultaneous" : "flash1"));

  const bool leadLedValid = (
    command.leadLed == "flash1" ||
    command.leadLed == "flash2" ||
    command.leadLed == "simultaneous"
  );

  if (command.flashMs == 0 || command.responseTimeoutMs == 0 || !leadLedValid) {
    return false;
  }
  return true;
}

void executeTrial(const TrialCommand& command) {
  deviceBusy = true;
  setIdleIndicator();

  if (!waitForButtonsReleased(BUTTON_RELEASE_TIMEOUT_MS)) {
    sendError("Buttons must be released before a new trial starts.", command.trialId);
    deviceBusy = false;
    setIdleIndicator();
    return;
  }

  sendTrialStarted(command);
  clearStrip(flash1);
  clearStrip(flash2);
  delay(command.prestimDelayMs);

  runFlashSequence(command.soaMs, command.flashMs, command.leadLed, command.flashRgbLevel);
  const uint64_t responseWindowStartUs = esp_timer_get_time();
  TrialResult result = waitForResponse(command, responseWindowStartUs);
  sendTrialResult(result);

  waitForButtonsReleased(BUTTON_RELEASE_TIMEOUT_MS);
  deviceBusy = false;
  setIdleIndicator();
}

void handleMessage(const String& rawLine) {
  JsonDocument doc;
  DeserializationError error = deserializeJson(doc, rawLine);
  if (error) {
    sendError("Invalid JSON.");
    return;
  }

  const char* type = doc["type"] | "";
  if (strcmp(type, "hello") == 0) {
    sendHelloAck();
    return;
  }

  if (strcmp(type, "ping") == 0) {
    JsonDocument response;
    response["type"] = "pong";
    response["protocol"] = PROTOCOL_VERSION;
    response["timestamp_ms"] = millis();
    response["busy"] = deviceBusy;
    sendJson(response);
    return;
  }

  if (strcmp(type, "run_trial") == 0) {
    if (deviceBusy) {
      sendError("Device is busy.");
      return;
    }

    TrialCommand command;
    if (!parseTrialCommand(doc, command)) {
      sendError("Invalid run_trial payload.");
      return;
    }
    executeTrial(command);
    return;
  }

  if (strcmp(type, "set_idle") == 0) {
    setIdleIndicator();
    sendStateAck("idle_ack", "off");
    return;
  }

  if (strcmp(type, "prepare_session") == 0) {
    if (deviceBusy) {
      sendError("Device is busy.");
      return;
    }
    deviceBusy = true;
    showSessionPrepSequence();
    deviceBusy = false;
    sendStateAck("prepare_session_ack", "off");
    return;
  }

  if (strcmp(type, "complete_session") == 0) {
    if (deviceBusy) {
      sendError("Device is busy.");
      return;
    }
    showSessionCompleteSequence();
    sendStateAck("complete_session_ack", "solid_red");
    return;
  }

  sendError("Unsupported message type.");
}

void readSerialMessages() {
  while (Serial.available() > 0) {
    char ch = static_cast<char>(Serial.read());
    if (ch == '\r') {
      continue;
    }
    if (ch == '\n') {
      String line = incomingLine;
      incomingLine = "";
      line.trim();
      if (!line.isEmpty()) {
        handleMessage(line);
      }
      continue;
    }

    if (incomingLine.length() < static_cast<int>(SERIAL_LINE_CAPACITY) - 1) {
      incomingLine += ch;
    } else {
      incomingLine = "";
      sendError("Incoming message too long.");
    }
  }
}

void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);
  Serial.begin(115200);

  pinMode(PIN_BTN_RED, INPUT_PULLUP);
  pinMode(PIN_BTN_BLUE, INPUT_PULLUP);

  flash1.begin();
  flash2.begin();
  setIdleIndicator();

  sendHelloAck();
}

void loop() {
  pollM5();
  readSerialMessages();
  delay(1);
}
