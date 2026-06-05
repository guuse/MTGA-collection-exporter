import json
import struct
import requests
import sys
import csv
import sqlite3
import os
import subprocess
import platform
import ctypes
from pathlib import Path
import time
import threading

# Ensure the progress bar's block characters print on any console code page.
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

PLATFORM = platform.system()  # 'Windows', 'Darwin', 'Linux'

if PLATFORM == 'Windows':
    try:
        import pymem
        import pymem.pattern
    except ImportError:
        pymem = None

try:
    import numpy as np
except ImportError:
    np = None

# Configuration
if getattr(sys, 'frozen', False):
    SCRIPT_DIR = Path(sys.executable).parent
else:
    SCRIPT_DIR = Path(__file__).resolve().parent

LOOKUP_FILE = SCRIPT_DIR / "arena_id_lookup.json"
OUTPUT_JSON = SCRIPT_DIR / "mtga_collection.json"
OUTPUT_TXT = SCRIPT_DIR / "mtga_collection.txt"
OUTPUT_CSV = SCRIPT_DIR / "mtga_collection.csv"


def print_progress(iteration, total, prefix='', suffix='', decimals=1, length=40, fill='█', printEnd="\r"):
    if total == 0: total = 1
    percent = ("{0:." + str(decimals) + "f}").format(100 * (iteration / float(total)))
    filledLength = int(length * iteration // total)
    bar = fill * filledLength + '-' * (length - filledLength)
    print(f'\r{prefix} |{bar}| {percent}% {suffix}', end=printEnd)
    if iteration == total:
        print()


# ─── macOS memory reader ──────────────────────────────────────────────────────

class MacOSMemReader:
    """Reads process memory on macOS using Mach kernel APIs.

    Requires the script to be run with sudo, or SIP disabled.
    Designed for MTGA running via CrossOver (Wine).
    """

    KERN_SUCCESS = 0
    VM_REGION_BASIC_INFO_64 = 9
    VM_REGION_BASIC_INFO_COUNT_64 = 9
    VM_PROT_READ = 0x01

    def __init__(self, process_name):
        self._lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
        self._configure_argtypes()

        pid = self._find_pid(process_name)
        if pid is None:
            raise Exception(
                f"Process '{process_name}' not found.\n"
                "Make sure MTG Arena is running via CrossOver and you have visited the Collection/Decks tab."
            )

        self.process_id = pid
        task = ctypes.c_uint(0)
        ret = self._lib.task_for_pid(
            self._lib.mach_task_self(),
            ctypes.c_int(pid),
            ctypes.byref(task)
        )

        if ret != self.KERN_SUCCESS:
            raise PermissionError(
                f"Cannot access game memory (Mach error {ret}).\n"
                "On macOS, run with: sudo python3 mtg.py"
            )

        self._task = task

    def _configure_argtypes(self):
        lib = self._lib
        lib.mach_task_self.restype = ctypes.c_uint
        lib.mach_task_self.argtypes = []

        lib.task_for_pid.restype = ctypes.c_int
        lib.task_for_pid.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]

        lib.mach_vm_read_overwrite.restype = ctypes.c_int
        lib.mach_vm_read_overwrite.argtypes = [
            ctypes.c_uint,
            ctypes.c_uint64,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint64),
        ]

        lib.mach_vm_region.restype = ctypes.c_int
        lib.mach_vm_region.argtypes = [
            ctypes.c_uint,
            ctypes.POINTER(ctypes.c_uint64),
            ctypes.POINTER(ctypes.c_uint64),
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint),
        ]

    def _find_pid(self, process_name):
        """Find PID by name or command-line substring (for CrossOver/Wine processes)."""
        try:
            # Exact name match first
            result = subprocess.run(['pgrep', '-x', process_name],
                                    capture_output=True, text=True)
            pids = [int(p) for p in result.stdout.strip().split('\n') if p.strip()]
            if pids:
                return pids[0]

            # Full command-line search (catches Wine running MTGA.exe)
            result = subprocess.run(['pgrep', '-f', process_name],
                                    capture_output=True, text=True)
            pids = [int(p) for p in result.stdout.strip().split('\n') if p.strip()]
            pids = [p for p in pids if p != os.getpid()]
            return pids[0] if pids else None
        except Exception:
            return None

    def read_bytes(self, address, size):
        """Read bytes from process memory, handling partial failures gracefully."""
        if address < 0:
            address = 0

        buf = ctypes.create_string_buffer(size)
        out_size = ctypes.c_uint64(0)
        ret = self._lib.mach_vm_read_overwrite(
            self._task,
            ctypes.c_uint64(address),
            ctypes.c_uint64(size),
            buf,
            ctypes.byref(out_size)
        )
        if ret == self.KERN_SUCCESS:
            return bytes(buf.raw[:out_size.value])

        # Fall back to page-by-page reads for ranges crossing unreadable pages
        result = bytearray()
        page = 4096
        for off in range(0, size, page):
            chunk = min(page, size - off)
            cb = ctypes.create_string_buffer(chunk)
            co = ctypes.c_uint64(0)
            r = self._lib.mach_vm_read_overwrite(
                self._task,
                ctypes.c_uint64(address + off),
                ctypes.c_uint64(chunk),
                cb,
                ctypes.byref(co)
            )
            result.extend(cb.raw[:co.value] if r == self.KERN_SUCCESS else b'\x00' * chunk)
        return bytes(result)

    def get_readable_regions(self):
        """Return list of (address, size) for all readable memory regions."""
        regions = []
        address = ctypes.c_uint64(1)

        while True:
            size = ctypes.c_uint64(0)
            info_buf = (ctypes.c_int * self.VM_REGION_BASIC_INFO_COUNT_64)()
            info_count = ctypes.c_uint(self.VM_REGION_BASIC_INFO_COUNT_64)
            object_name = ctypes.c_uint(0)

            ret = self._lib.mach_vm_region(
                self._task,
                ctypes.byref(address),
                ctypes.byref(size),
                self.VM_REGION_BASIC_INFO_64,
                ctypes.cast(info_buf, ctypes.c_void_p),
                ctypes.byref(info_count),
                ctypes.byref(object_name)
            )

            if ret != self.KERN_SUCCESS:
                break

            if info_buf[0] & self.VM_PROT_READ:
                regions.append((address.value, size.value))

            next_addr = address.value + size.value
            if next_addr <= address.value:
                break
            address.value = next_addr

        return regions


# ─── MTGA database helpers ────────────────────────────────────────────────────

def get_local_mtga_path():
    """Finds the MTGA Raw data folder across Windows and macOS."""
    if PLATFORM == 'Windows':
        candidates = [
            Path(r"C:\Program Files (x86)\Steam\steamapps\common\MTGA\MTGA_Data\Downloads\Raw"),
            Path(r"C:\Program Files\Wizards of the Coast\MTGA\MTGA_Data\Downloads\Raw"),
            Path(r"C:\Program Files (x86)\Wizards of the Coast\MTGA\MTGA_Data\Downloads\Raw"),
        ]
    else:
        steam_base = Path.home() / "Library" / "Application Support" / "Steam" / "steamapps" / "common"
        candidates = [
            # macOS Steam (native MTGA app)
            steam_base / "MTGA" / "MTGA_Data" / "Downloads" / "Raw",
        ]
        # CrossOver bottles as fallback
        crossover_bottles = Path.home() / "Library" / "Application Support" / "CrossOver" / "Bottles"
        if crossover_bottles.exists():
            for bottle in crossover_bottles.iterdir():
                drive_c = bottle / "drive_c"
                candidates += [
                    drive_c / "Program Files (x86)" / "Steam" / "steamapps" / "common" / "MTGA" / "MTGA_Data" / "Downloads" / "Raw",
                    drive_c / "Program Files" / "Wizards of the Coast" / "MTGA" / "MTGA_Data" / "Downloads" / "Raw",
                    drive_c / "Program Files (x86)" / "Wizards of the Coast" / "MTGA" / "MTGA_Data" / "Downloads" / "Raw",
                ]

    for p in candidates:
        if p.exists():
            return p
    return None


def load_local_mtga_database():
    """Scans local MTGA SQLite files for card definitions."""
    raw_path = get_local_mtga_path()
    if not raw_path:
        print("Local MTGA installation not found.")
        return {}

    print(f"Scanning local MTGA files in {raw_path}...")
    lookup = {}

    try:
        all_files = sorted(list(raw_path.glob("*.mtga")), key=lambda f: f.stat().st_size, reverse=True)
        total_files = len(all_files)

        print_progress(0, total_files, prefix='Local DB:', suffix='Complete', length=30)

        for i, f in enumerate(all_files):
            print_progress(i + 1, total_files, prefix='Local DB:', suffix=f'Checking {f.name[:10]}...', length=30)

            if f.stat().st_size < 500 * 1024: continue

            try:
                conn = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
                cursor = conn.cursor()

                tables = {row[0] for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")}

                if "Cards" in tables and "Localizations" in tables:
                    loc_map = {}
                    try:
                        cursor.execute("SELECT Id, Text FROM Localizations WHERE Format LIKE '%en-US%' OR Format IS NULL")
                        for lid, text in cursor.fetchall():
                            if text: loc_map[lid] = text
                    except sqlite3.Error:
                        cursor.execute("SELECT Id, Text FROM Localizations")
                        for lid, text in cursor.fetchall():
                            if text: loc_map[lid] = text

                    cols = [row[1] for row in cursor.execute("PRAGMA table_info(Cards)")]
                    has_set = "ExpansionCode" in cols
                    has_cn = "CollectorNumber" in cols

                    query = f"SELECT GrpId, TitleId, {'ExpansionCode' if has_set else 'NULL'}, {'CollectorNumber' if has_cn else 'NULL'} FROM Cards"

                    cursor.execute(query)
                    rows = cursor.fetchall()

                    for row in rows:
                        grp_id = row[0]
                        title_id = row[1]
                        set_code = row[2] if row[2] else ""
                        cn = str(row[3]) if row[3] else ""

                        if title_id in loc_map:
                            lookup[grp_id] = {
                                "name": loc_map[title_id],
                                "set": set_code,
                                "collector_number": cn
                            }

                    if len(lookup) > 1000:
                        print_progress(total_files, total_files, prefix='Local DB:', suffix='Done', length=30)
                        print(f"  Loaded {len(lookup)} cards locally.")
                        conn.close()
                        return lookup

                conn.close()
            except sqlite3.Error:
                continue

        print_progress(total_files, total_files, prefix='Local DB:', suffix='Done', length=30)

    except Exception as e:
        print(f"\nError scanning local files: {e}")

    return lookup


def fetch_scryfall_database():
    """Downloads card data from Scryfall API."""
    print("Fetching card data from Scryfall API...")
    headers = {"User-Agent": "MTGA-Exporter/2.0", "Accept": "application/json"}
    try:
        bulk_meta = requests.get("https://api.scryfall.com/bulk-data/default-cards", headers=headers, timeout=30).json()
        cards_data = requests.get(bulk_meta["download_uri"], headers=headers, timeout=120).json()

        lookup = {}
        for c in cards_data:
            if c.get("arena_id"):
                lookup[c["arena_id"]] = {
                    "name": c.get("name", "Unknown"),
                    "set": c.get("set", "").upper(),
                    "collector_number": c.get("collector_number", "")
                }
        return lookup
    except Exception as e:
        print(f"Scryfall download failed: {e}")
        return {}


def load_card_database():
    """Orchestrates loading: Cache -> Local -> Scryfall."""
    if LOOKUP_FILE.exists():
        try:
            print("Loading cached database...")
            with LOOKUP_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
                return {int(k): v for k, v in data.items() if isinstance(v, dict)}
        except Exception:
            print("Cache corrupted.")

    lookup = load_local_mtga_database()

    if not lookup:
        print("\n[Warn] Local database not found. Downloading from Scryfall...")
        lookup = fetch_scryfall_database()

    if lookup:
        try:
            with LOOKUP_FILE.open("w", encoding="utf-8") as f:
                json.dump({str(k): v for k, v in lookup.items()}, f)
            print("Database cached.")
        except Exception:
            pass

    return lookup


# ─── Memory scanning (anchor-free) ────────────────────────────────────────────
#
# MTGA keeps the owned collection in a C# Dictionary<int,int> (grpId -> count).
# A .NET dictionary stores its entries as an array of structs laid out as
# [hashCode, next, key, value] -> 4x uint32 per entry (stride 4); key=grpId and
# value=owned count sit at offsets 2 and 3. Older/array layouts use a plain
# [id, count] pairing (stride 2). We don't need user-supplied "anchor" cards:
# we read every readable private memory region, validate ids against the card
# database, and keep the largest contiguous block of valid (id -> count) entries.

_MEM_COMMIT = 0x1000
_MEM_PRIVATE = 0x20000
_PAGE_GUARD = 0x100
_READABLE_PROT = {0x02, 0x04, 0x20, 0x40}  # READONLY, READWRITE, EXECUTE_READ, EXECUTE_READWRITE


class _MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_ulonglong),
        ("AllocationBase", ctypes.c_ulonglong),
        ("AllocationProtect", ctypes.c_ulong),
        ("__alignment1", ctypes.c_ulong),
        ("RegionSize", ctypes.c_ulonglong),
        ("State", ctypes.c_ulong),
        ("Protect", ctypes.c_ulong),
        ("Type", ctypes.c_ulong),
        ("__alignment2", ctypes.c_ulong),
    ]


def iter_readable_regions(pm):
    """Yield (base, size) for readable memory regions, platform-aware."""
    if PLATFORM == 'Windows':
        VirtualQueryEx = ctypes.windll.kernel32.VirtualQueryEx
        VirtualQueryEx.restype = ctypes.c_size_t
        VirtualQueryEx.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p,
            ctypes.POINTER(_MEMORY_BASIC_INFORMATION), ctypes.c_size_t,
        ]
        handle = pm.process_handle
        mbi = _MEMORY_BASIC_INFORMATION()
        addr = 0
        while VirtualQueryEx(handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            if (mbi.State == _MEM_COMMIT and mbi.Type == _MEM_PRIVATE
                    and (mbi.Protect & 0xFF) in _READABLE_PROT and not (mbi.Protect & _PAGE_GUARD)):
                yield mbi.BaseAddress, mbi.RegionSize
            nxt = mbi.BaseAddress + mbi.RegionSize
            if nxt <= addr:
                break
            addr = nxt
    else:
        for base, size in pm.get_readable_regions():
            yield base, size


def _merged_runs(mask, gap):
    """Return (start, end) for True-runs in a bool array, bridging gaps <= gap."""
    if not mask.any():
        return []
    edges = np.flatnonzero(np.diff(np.concatenate(([0], mask.view(np.int8), [0]))))
    starts, ends = edges[0::2], edges[1::2]
    merged = []
    for s, e in zip(starts.tolist(), ends.tolist()):
        if merged and s - merged[-1][1] <= gap:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    return merged


def scan_collection(pm, db):
    """Anchor-free scan: return the largest {grpId: count} block found in memory."""
    if np is None:
        raise Exception("numpy is required for scanning. Install it with: pip install numpy")

    valid = np.array(sorted(db.keys()), dtype=np.uint32)

    print("Enumerating memory regions...")
    regions = list(iter_readable_regions(pm))
    total = len(regions) or 1

    MIN_ENTRIES = 30     # ignore tiny coincidental blocks
    MAX_COUNT = 1000     # owned counts are small; rejects pointer/garbage values
    GAP = 8              # tolerate empty dictionary slots inside a block

    best = {}
    print_progress(0, total, prefix='Mem Scan:', suffix='Starting', length=25)
    for ri, (base, size) in enumerate(regions):
        try:
            data = pm.read_bytes(base, size)
        except Exception:
            data = None
        if data:
            n = len(data) // 4
            if n >= 8:
                a = np.frombuffer(data, dtype=np.uint32, count=n)
                # stride 4 = C# Dictionary entries; stride 2 = plain [id, count] array
                for stride, idoff, cntoff in ((4, 2, 3), (2, 0, 1)):
                    for align in range(stride):
                        ids = a[align + idoff::stride]
                        counts = a[align + cntoff::stride]
                        m = min(len(ids), len(counts))
                        if m < MIN_ENTRIES:
                            continue
                        ids, counts = ids[:m], counts[:m]
                        mask = np.isin(ids, valid) & (counts >= 1) & (counts <= MAX_COUNT)
                        for s, e in _merged_runs(mask, GAP):
                            span = mask[s:e]
                            if int(span.sum()) <= len(best):
                                continue
                            d = {int(i): int(c)
                                 for i, c in zip(ids[s:e][span].tolist(),
                                                 counts[s:e][span].tolist())}
                            if len(d) > len(best):
                                best = d
        print_progress(ri + 1, total, prefix='Mem Scan:', suffix=f'{len(best)} cards', length=25)

    return best


# ─── Output ───────────────────────────────────────────────────────────────────

def open_output_folder(path):
    """Reveal output file in Finder (macOS) or Explorer (Windows)."""
    try:
        if PLATFORM == 'Windows':
            subprocess.Popen(f'explorer /select,"{path}"')
        elif PLATFORM == 'Darwin':
            subprocess.Popen(['open', '-R', str(path)])
    except Exception:
        pass


def connect_to_mtga():
    """Connect to the MTGA process, platform-aware."""
    if PLATFORM == 'Windows':
        if pymem is None:
            raise Exception("pymem not installed. Run: pip install pymem")
        pm = pymem.Pymem("MTGA.exe")
        return pm
    elif PLATFORM == 'Darwin':
        # Native macOS Steam build uses process name "MTGA"; CrossOver uses "MTGA.exe"
        for name in ("MTGA", "MTGA.exe", "mtga"):
            try:
                return MacOSMemReader(name)
            except PermissionError:
                raise  # propagate sudo error immediately
            except Exception:
                continue
        raise Exception(
            "MTG Arena not found. Make sure it is running (Steam or CrossOver) and you have visited the Collection/Decks tab."
        )
    else:
        raise Exception(f"Unsupported platform: {PLATFORM}. Only Windows and macOS are supported.")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"MTGA Collection Exporter | v3.0 | {PLATFORM}")
    print(f"Output Folder: {SCRIPT_DIR}\n")

    if np is None:
        print("[Error] numpy is required. Install it with: pip install numpy")
        input("Press Enter to exit.")
        return

    db = load_card_database()
    if not db:
        input("Database init failed. Press Enter to exit.")
        return

    print("Connecting to MTGA...")
    try:
        pm = connect_to_mtga()
        print(f"Connected (PID: {pm.process_id})")
    except PermissionError as e:
        print(str(e))
        input("Press Enter to exit.")
        return
    except Exception as e:
        print(str(e))
        input("Press Enter to exit.")
        return

    print("\nScanning memory for collection data (no anchors needed)...")
    collection = scan_collection(pm, db)

    if not collection:
        print("\nCould not locate your collection in memory.")
        print("Open MTG Arena, visit the Collection or Decks tab and scroll through")
        print("your cards (so they load into memory), then run this again.")
        input("Press Enter to exit.")
        return

    print(f"\n[Success] Found {len(collection)} unique cards "
          f"({sum(collection.values())} total).")

    processed = {}
    for cid, qty in collection.items():
        if info := db.get(cid):
            key = (info["name"], info["set"])
            if key not in processed:
                processed[key] = {
                    "count": 0,
                    "name": info["name"],
                    "set": info["set"],
                    "cn": info.get("collector_number", "")
                }
            processed[key]["count"] += qty

    final_list = sorted(processed.values(), key=lambda x: (x["name"], x["set"]))

    with OUTPUT_TXT.open("w", encoding="utf-8") as f:
        for i in final_list:
            set_str = f" ({i['set']})" if i['set'] else ""
            f.write(f"{i['count']} {i['name']}{set_str}\n")

    with OUTPUT_JSON.open("w", encoding="utf-8") as f:
        json.dump(final_list, f, indent=2)

    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Count", "Name", "Edition", "Condition", "Language", "Foil", "Tag"])
        for i in final_list:
            writer.writerow([i["count"], i["name"], i["set"], "Near Mint", "English", "", ""])

    print(f"\nExport complete!")
    print(f"Files saved to: {SCRIPT_DIR}")

    open_output_folder(OUTPUT_TXT)

    input("Press Enter to exit...")


if __name__ == "__main__":
    main()
