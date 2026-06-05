# Changelog - V3.0

- **No more anchor cards needed** — the scanner now finds your collection automatically. Just run it.
- Fixed partial/incomplete exports: the scanner now reads MTGA's collection dictionary directly (validated against the card database), capturing your full collection instead of a small fragment.
- Cross-platform memory scanning (Windows + macOS) with no manual setup.
- Fixed Scryfall fallback (now sends the required request headers).

# Changelog - V1.2

- added priority for local card sql database, scryfall used as backup

- added progress bar for mem searching

- removed redundant comments on source code

- fixed issue where the .txt would list items multiple times

- added csv exports for Moxfield

- added card set identifiers to the .txt 

- and more small changes

imported collection to moxfield:
<img width="1901" height="962" alt="image" src="https://github.com/user-attachments/assets/4f784272-e2fc-4521-8aa1-9137c1029aa4" />

Better text file
- before: 
<img width="1080" height="467" alt="image" src="https://github.com/user-attachments/assets/c0bb05cd-4996-4b2a-8c12-7b4bba20aabe" />

- after: 
<img width="1112" height="480" alt="image" src="https://github.com/user-attachments/assets/9609dd74-69c2-4c85-9ea1-8a9c35aa7d6e" />

Progress bars: 
<img width="388" height="96" alt="image" src="https://github.com/user-attachments/assets/ccc5c324-3f62-430b-bc74-366c4f9314d9" />

# MTG Arena Collection Exporter

This tool scans your game memory while MTG Arena is running to export your entire card collection.
It outputs two files:
- `mtga_collection.json`: Full data including card IDs and quantities.
- `mtga_collection.txt`: A readable list of your cards (Count + Name).

## How to use

### Windows — Run the Executable (Simplest)
1. Navigate to **Releases**
2. Download and extract the **zip**
3. Navigate inside the extracted folder
4. Ensure **MTG Arena is running**
5. Go to the **Decks** or **Collection** tab in-game, scroll for 30 secs through your collection (important so your collection loads into memory)
6. Run `MTGA_Exporter.exe` — it scans automatically and writes the export files next to itself

### Windows — Run from Python Source
1. Download and extract zip
2. Install Python 3.x
3. Run `install.bat` to install dependencies
4. Run `python mtg.py`

### macOS — Run from Python Source
MTG Arena has a native macOS app available via **Steam**.

**Prerequisites:**
- MTG Arena installed via Steam
- Python 3.x (`brew install python` if needed)

**Steps:**
1. Start MTG Arena via Steam
2. Go to the **Decks** or **Collection** tab and scroll through your collection for ~30 seconds (so the game loads all cards into memory)
3. Open Terminal, navigate to this folder
4. Run `bash install.sh` once to install dependencies
5. Run the exporter with sudo (required to read game memory):
   ```
   sudo python3 mtg.py
   ```

> **Why sudo?** macOS restricts cross-process memory access. Running with `sudo` grants the necessary permissions, equivalent to "Run as Administrator" on Windows.

## Troubleshooting
- **Collection not found:** Make sure you visited the Collection/Decks tab and scrolled through it before running the tool, so the cards are loaded into memory.
- **Windows:** Run as Administrator if you encounter permission errors.
- **macOS:** Run with `sudo python3 mtg.py` if you see a "Cannot access game memory" error.
- **macOS — process not found:** Make sure MTGA is open via Steam. If using CrossOver instead, the script also searches for `MTGA.exe` in Wine processes.

## Files
- `MTGA_Exporter.exe`: Standalone Windows application.
- `mtg.py`: Cross-platform Python source (Windows + macOS).
- `requirements.txt`: Python dependencies.
- `install.bat`: Setup script for Windows.
- `install.sh`: Setup script for macOS.
