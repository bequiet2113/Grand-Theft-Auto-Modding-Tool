# GTA Mod Toolkit – Ultimate Edition 🛠️

**GTA Mod Toolkit** is an advanced modding suite written entirely in Python using standard libraries and Tkinter. It provides tools for editing, inspecting, and managing assets for RenderWare-era games: **GTA III**, **GTA Vice City**, and **GTA San Andreas**.

This application resolves hex hash key issues in `.gxt` files by providing a dictionary-based lookup system and offers 2D map instance placement visualization, IMG archive management, RenderWare asset parsing, audio extraction, and a script opcode viewer.

---

## 🚀 Key Features

* **GXT Editor & Key Recovery:**
  * Reverses and decodes hex hashes (CRC32 / GXT Hash) into human-readable key names using an extensible builtin/user lookup dictionary.
  * Real-time preview panel stripping GTA color and formatting tags (`~r~`, `~b~`, `~w~`, `~n~`, etc.).
  * Maps custom hash names and saves them persistently to `gxt_dictionary.json`.
  * Multi-table support (`MAIN` and mission tables) for San Andreas and III/VC formats.

* **IMG Archive Manager:**
  * Full support for **IMG v1** (`.dir` + `.img`) and **IMG v2** (single `.img`) formats.
  * Import, export, delete, replace, verify, and **Rebuild IMG** (defragmentation) features.
  * Asynchronous threading keeps the GUI smooth during heavy operations.

* **IDE / IPL + 2D Map Visualizer:**
  * Parses item definitions (`.ide`) and item placement instances (`.ipl`).
  * Interactive 2D Canvas viewer (pan, zoom, select objects) to edit XYZ positions.

* **DFF / COL / TXD Asset Inspector:**
  * RenderWare stream parser displaying geometry count, vertex/triangle stats, textures, and COL collision metadata.

* **Audio & Script Tools:**
  * Converts `.sdt` + `.raw` audio streams into playable `.wav` files.
  * Heuristic opcode viewer for `MAIN.SCM` script files.

---

## 🛠️ System Requirements & Installation

### Requirements:
* **Python 3.8+**
* **Tkinter** (included in standard Python installers on Windows).

### Running the Toolkit:
1. Download or clone the repository containing `GTAMT.pyw`.
2. Run the file directly using Python:
   ```bash
   python GTAMT.pyw
