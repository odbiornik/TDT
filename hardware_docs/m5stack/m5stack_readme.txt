================================================================================
TECHNICAL DOCUMENTATION:
================================================================================

1. ELEMENTS:
	
	- M5Stack Atom-Lite SKU:C008, http://docs.m5stack.com/en/core/ATOM%20Lite, x1 
	- M5Stack Atomic Port ABC Base SKU:A130, http://docs.m5stack.com/en/atom/AtomPortABC, x1
	- M5Stack Unit RGB SKU:U003, http://docs.m5stack.com/en/unit/rgb, x2
	- M5Stack Unit Dual Button SKU:U025, http://docs.m5stack.com/en/unit/dual_button, x1
	- grove cable 20 cm, x2
	- grove cable 100 cm, x1
	- USB-C cable, x1


2. DETAILED PINOUT & SIGNAL MAPPING
------------------------------------
Element             | Interface / Port    | GPIO Pin | Code Variable | Logic
--------------------|----------------------|----------|---------------|---------
Laptop              | USB-C (Main)         | --       | Serial        | Data/Pwr
RGB Unit #1         | ABC Base -> Port C   | GPIO 5   | flash1        | Output
RGB Unit #2         | ABC Base -> Port A   | GPIO 38  | flash2        | Output
Dual Button (Red)   | Atom -> Bottom Port  | GPIO 2   | PIN_BTN_RED   | Input
Dual Button (Blue)  | Atom -> Bottom Port  | GPIO 1   | PIN_BTN_BLUE  | Input


3. UPDATED LOGICAL SCHEMATIC
----------------------------
      [ LAPTOP ]
          ||
     (USB-C Cable)
          ||
+---------------------------+
|   M5Stack AtomS3 Lite     | 
+-------------+-------------+
|             |             |
|   [ BOTTOM PORT ]         | <--- DIRECT CONNECTION
|         |                 |      (No ABC Base routing)
|   (Grove 100cm)           |
|         |                 |
|   [ DUAL BUTTON ]         |
|                           |
+---------------------------+
|   Atomic Port ABC Base    | <--- ATTACHED EXPANSION
+------+-------------+------+
       |             |
   [ PORT A ]    [ PORT C ]
       |             |
 (Grove 20cm)   (Grove 20cm)
       |             |
 [ RGB Unit #2 ] [ RGB Unit #1 ]


4. FIRMWARE CONFIGURATION AND DEPLOYMENT 
----------------------------------------
To successfully program and deploy firmware to the M5Stack Atom-Lite, you must utilize the configuration and source files provided in the repository. The build process is managed via PlatformIO, ensuring a consistent and reproducible environment.

Required Files:

	- platformio.ini: This is the project configuration file. 
	- atom.cpp: This main source file contains the core logic for the device

All hardware operations and clinical examinations are conducted via a computer equipped with the TDT Studio software. This dedicated environment ensures proper data acquisition and device management. To set up the workstation, run the standalone installer provided in the package:

	- installer/output/TDTStudio_Setup.exe


================================================================================
END OF DOCUMENTATION
================================================================================
