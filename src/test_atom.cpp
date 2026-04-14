// --- PLIK TESTOWY (ARCHIWUM) ---
// Ten plik pozostaje do testowania układu. Aby go użyć w przyszłości, zmień '#if 0' na '#if 1'
// i wyłącz/zmień nazwę pliku z nowym programem (app_program.cpp), aby uniknąć konfliktów "multiple definition of setup/loop".
#if 0
// Sprzęt: 
// AtomS3 Lite ESP32S3 Dev Kit SKU: C124
// ATOMIC PortABC Extension Base SKU: A130
// RGB LED Unit (SK6812) SKU: U003 x2 (Port A i Port C)
// Mini Dual Button Unit SKU: U025 (Port dolny)

#include <M5Unified.h>
#include <Adafruit_NeoPixel.h>

// --- KONFIGURACJA Z NAKLEJKI ---

// Przyciski (Port dolny AtomS3)
#define PIN_BTN_RED   2 
#define PIN_BTN_BLUE  1 

// Lampka Niebieska (Port C - Niebieski)
// Żółty kabel to G5
#define PIN_LED_BLUE_PORT 5 

// Lampka Czerwona (Port A - Czerwony)
// Żółty kabel to G38
#define PIN_LED_RED_PORT 38 

#define NUM_LEDS 3 // SK6812 Panel LED RGB - 3 diody na lampkę

// ========== KONFIGURACJA LED'ÓW - ZMIEŃ TUTAJ ==========

// KOLOR lampki niebieskiej (RGB: Red, Green, Blue - wartości 0-255)
// Przykłady: Color(0, 0, 150) = niebieski, Color(0, 150, 150) = cyan, Color(100, 100, 255) = jasnoniebieskie
uint32_t COLOR_BLUE = Adafruit_NeoPixel::Color(0, 0, 150);

// KOLOR lampki czerwonej (RGB)
// Przykłady: Color(150, 0, 0) = czerwony, Color(255, 100, 0) = pomarańczowy
uint32_t COLOR_RED = Adafruit_NeoPixel::Color(150, 0, 0);

// KTÓRE PIKSELE mają świecić w lampce niebieskiej (indeksy 0-2)
// Przykłady: {0, 1, 2} = wszystkie 3, {0} = tylko pierwszy, {0, 2} = pierwszy i trzeci
int LEDS_TO_LIGHT_BLUE[] = {1}; // wszystkie 3 diody
int NUM_LEDS_BLUE = sizeof(LEDS_TO_LIGHT_BLUE) / sizeof(LEDS_TO_LIGHT_BLUE[0]);

// KTÓRE PIKSELE mają świecić w lampce czerwonej (indeksy 0-2)
int LEDS_TO_LIGHT_RED[] = {1}; // wszystkie 3 diody
int NUM_LEDS_RED = sizeof(LEDS_TO_LIGHT_RED) / sizeof(LEDS_TO_LIGHT_RED[0]);

// =========================================================

Adafruit_NeoPixel ledBlue(NUM_LEDS, PIN_LED_BLUE_PORT, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel ledRed(NUM_LEDS, PIN_LED_RED_PORT, NEO_GRB + NEO_KHZ800);

void setup() {
    auto cfg = M5.config();
    M5.begin(cfg);
    Serial.begin(115200);

    pinMode(PIN_BTN_RED, INPUT_PULLUP);
    pinMode(PIN_BTN_BLUE, INPUT_PULLUP);

    ledBlue.begin();
    ledRed.begin();

    // Wyraźne czyszczenie na start
    ledBlue.clear(); ledBlue.show();
    ledRed.clear();  ledRed.show();

    Serial.println("ATOMS3 Gotowy!");
}

void loop() {
    M5.update();

    bool redPressed = (digitalRead(PIN_BTN_RED) == LOW);
    bool bluePressed = (digitalRead(PIN_BTN_BLUE) == LOW);

    // Obsługa Niebieskiej Lampki (Port C - G5)
    if (bluePressed) {
        for(int i = 0; i < NUM_LEDS_BLUE; i++) {
            ledBlue.setPixelColor(LEDS_TO_LIGHT_BLUE[i], COLOR_BLUE);
        }
    } else {
        ledBlue.clear();
    }
    ledBlue.show();

    // Obsługa Czerwonej Lampki (Port A - G38)
    if (redPressed) {
        for(int i = 0; i < NUM_LEDS_RED; i++) {
            ledRed.setPixelColor(LEDS_TO_LIGHT_RED[i], COLOR_RED);
        }
    } else {
        ledRed.clear();
    }
    ledRed.show();

    // Monitorowanie przycisków
    if (redPressed || bluePressed) {
        Serial.printf("BTN -> RED: %d, BLUE: %d\n", redPressed, bluePressed);
    }

    delay(20);
}

#endif