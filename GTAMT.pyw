#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GTA Mod Toolkit - Ultimate Edition
Zaawansowany kombajn modderski dla GTA III / Vice City / San Andreas
IMG • GXT (CRC32 reverse + słownik) • IDE/IPL + 2D Map • DFF/COL Inspector
Audio (SDT→WAV) • MAIN.SCM Opcode Viewer
Wyłącznie biblioteka standardowa Pythona + tkinter.
"""

import os
import sys
import struct
import logging
import threading
import queue
import json
import re
import math
import wave
import traceback
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any, Callable

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext, simpledialog, colorchooser
from tkinter import font as tkfont

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("GTA_Mod_Toolkit")

# ---------------------------------------------------------------------------
# BinaryReader
# ---------------------------------------------------------------------------
class BinaryReaderError(Exception):
    pass

class BinaryReader:
    __slots__ = ("data", "pos", "size")

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.size = len(data)

    def tell(self) -> int:
        return self.pos

    def remaining(self) -> int:
        return self.size - self.pos

    def seek(self, pos: int) -> None:
        if pos < 0 or pos > self.size:
            raise BinaryReaderError(f"Seek out of bounds: {pos}/{self.size}")
        self.pos = pos

    def skip(self, n: int) -> None:
        self.seek(self.pos + n)

    def eof(self) -> bool:
        return self.pos >= self.size

    def _adv(self, n: int) -> int:
        start = self.pos
        if n < 0 or start + n > self.size:
            raise BinaryReaderError("Read past EOF")
        self.pos += n
        return start

    def read_bytes(self, n: int) -> bytes:
        s = self._adv(n)
        return self.data[s:s + n]

    def read_u8(self) -> int:
        return struct.unpack_from("<B", self.data, self._adv(1))[0]

    def read_i8(self) -> int:
        return struct.unpack_from("<b", self.data, self._adv(1))[0]

    def read_u16(self) -> int:
        return struct.unpack_from("<H", self.data, self._adv(2))[0]

    def read_i16(self) -> int:
        return struct.unpack_from("<h", self.data, self._adv(2))[0]

    def read_u32(self) -> int:
        return struct.unpack_from("<I", self.data, self._adv(4))[0]

    def read_i32(self) -> int:
        return struct.unpack_from("<i", self.data, self._adv(4))[0]

    def read_f32(self) -> float:
        return struct.unpack_from("<f", self.data, self._adv(4))[0]

    def read_fixed_string(self, length: int, enc: str = "latin-1") -> str:
        raw = self.read_bytes(length)
        term = raw.find(b"\x00")
        if term != -1:
            raw = raw[:term]
        return raw.decode(enc, errors="replace")

    def read_cstring(self, enc: str = "latin-1", max_len: int = 8192) -> str:
        out = bytearray()
        c = 0
        while self.pos < self.size and c < max_len:
            b = self.data[self.pos]
            self.pos += 1
            c += 1
            if b == 0:
                break
            out.append(b)
        return bytes(out).decode(enc, errors="replace")

    def read_utf16_cstring(self) -> str:
        out = bytearray()
        while self.pos + 1 < self.size:
            lo = self.data[self.pos]
            hi = self.data[self.pos + 1]
            self.pos += 2
            if lo == 0 and hi == 0:
                break
            out.append(lo)
            out.append(hi)
        return bytes(out).decode("utf-16-le", errors="replace")


# ---------------------------------------------------------------------------
# RenderWare constants
# ---------------------------------------------------------------------------
RW_STRUCT = 0x01
RW_STRING = 0x02
RW_EXTENSION = 0x03
RW_TEXTURE = 0x06
RW_MATERIAL = 0x07
RW_MATLIST = 0x08
RW_FRAMELIST = 0x0E
RW_GEOMETRY = 0x0F
RW_CLUMP = 0x10
RW_ATOMIC = 0x14
RW_TEXTURENATIVE = 0x15
RW_TEXDICTIONARY = 0x16
RW_GEOMETRYLIST = 0x1A

RW_TYPE_NAMES = {
    0x01: "STRUCT", 0x02: "STRING", 0x03: "EXTENSION", 0x06: "TEXTURE",
    0x07: "MATERIAL", 0x08: "MATLIST", 0x0E: "FRAMELIST", 0x0F: "GEOMETRY",
    0x10: "CLUMP", 0x14: "ATOMIC", 0x15: "TEXTURENATIVE", 0x16: "TEXDICTIONARY",
    0x1A: "GEOMETRYLIST",
}

def rw_type_name(tid: int) -> str:
    return RW_TYPE_NAMES.get(tid, f"UNK(0x{tid:02X})")

def decode_rw_version(lib: int) -> Tuple[str, int]:
    if lib & 0xFFFF0000 == 0:
        return ("pre-3.0", lib)
    ver = ((lib >> 14) & 0x3FF00) + ((lib >> 16) & 0x3F)
    build = lib & 0xFFFF
    major = (ver >> 8) & 0xF
    minor1 = (ver >> 4) & 0xF
    minor2 = ver & 0xF
    return f"{major}.{minor1}.{minor2:X}", build

@dataclass
class RwChunk:
    type_id: int
    size: int
    library_id: int
    start: int
    data_offset: int
    @property
    def type_name(self) -> str:
        return rw_type_name(self.type_id)

class RwSectionReader:
    HSIZE = 12
    def __init__(self, reader: BinaryReader):
        self.r = reader
    def read_header(self) -> RwChunk:
        start = self.r.tell()
        if self.r.remaining() < self.HSIZE:
            raise BinaryReaderError("RW header truncated")
        tid = self.r.read_u32()
        size = self.r.read_u32()
        lib = self.r.read_u32()
        return RwChunk(tid, size, lib, start, self.r.tell())
    def children(self, parent_end: int):
        while self.r.tell() < parent_end:
            if self.r.remaining() < self.HSIZE:
                break
            ch = self.read_header()
            end = ch.data_offset + ch.size
            if ch.size < 0 or end > self.r.size or end > parent_end:
                break
            yield ch
            self.r.seek(end)


# ---------------------------------------------------------------------------
# IMG Archive
# ---------------------------------------------------------------------------
@dataclass
class ImgEntry:
    name: str
    offset_sectors: int
    size_sectors: int
    data: Optional[bytes] = None
    @property
    def offset_bytes(self) -> int:
        return self.offset_sectors * 2048
    @property
    def size_bytes(self) -> int:
        return self.size_sectors * 2048

class ImgArchive:
    SECTOR = 2048
    def __init__(self, path: str, dir_path: Optional[str] = None):
        self.path = path
        self.dir_path = dir_path
        self.version: Optional[int] = None
        self.entries: List[ImgEntry] = []
        self.dirty = False
        self._raw: Optional[bytes] = None

    def load(self) -> "ImgArchive":
        with open(self.path, "rb") as f:
            head = f.read(4)
            f.seek(0)
            self._raw = f.read()
        if head == b"VER2":
            self._parse_v2()
        else:
            if self.dir_path is None:
                guess = os.path.splitext(self.path)[0] + ".dir"
                if os.path.isfile(guess):
                    self.dir_path = guess
            if not self.dir_path or not os.path.isfile(self.dir_path):
                raise ValueError("Brak VER2 i brak pliku .dir")
            self._parse_v1()
        return self

    def _parse_v1(self):
        self.version = 1
        with open(self.dir_path, "rb") as f:
            data = f.read()
        r = BinaryReader(data)
        for _ in range(r.size // 32):
            off = r.read_u32()
            sz = r.read_u32()
            name = r.read_fixed_string(24)
            if name:
                self.entries.append(ImgEntry(name, off, sz))

    def _parse_v2(self):
        self.version = 2
        r = BinaryReader(self._raw)
        if r.read_bytes(4) != b"VER2":
            raise ValueError("To nie jest VER2")
        count = r.read_u32()
        for _ in range(count):
            off = r.read_u32()
            stream = r.read_u16()
            disc = r.read_u16()
            name = r.read_fixed_string(24)
            if name:
                self.entries.append(ImgEntry(name, off, stream or disc))

    def extract(self, e: ImgEntry) -> bytes:
        if e.data is not None:
            return e.data
        with open(self.path, "rb") as f:
            f.seek(e.offset_bytes)
            return f.read(e.size_bytes)

    def replace(self, name: str, data: bytes) -> bool:
        for e in self.entries:
            if e.name.lower() == name.lower():
                e.data = data
                e.size_sectors = (len(data) + self.SECTOR - 1) // self.SECTOR
                self.dirty = True
                return True
        return False

    def add(self, name: str, data: bytes):
        sec = (len(data) + self.SECTOR - 1) // self.SECTOR
        self.entries.append(ImgEntry(name, 0, sec, data=data))
        self.dirty = True

    def remove(self, name: str) -> bool:
        for i, e in enumerate(self.entries):
            if e.name.lower() == name.lower():
                del self.entries[i]
                self.dirty = True
                return True
        return False

    def rebuild(self, out: Optional[str] = None) -> str:
        out = out or self.path
        packed = []
        for e in self.entries:
            data = self.extract(e)
            pad = (self.SECTOR - (len(data) % self.SECTOR)) % self.SECTOR
            if pad:
                data += b"\x00" * pad
            packed.append((e.name, data))
        table_size = len(packed) * 32
        header = b"VER2" + struct.pack("<I", len(packed))
        offset = 8 + table_size
        if offset % self.SECTOR:
            offset += self.SECTOR - (offset % self.SECTOR)
        table = bytearray()
        blob = bytearray()
        cur = offset // self.SECTOR
        for name, data in packed:
            sec = len(data) // self.SECTOR
            nb = name.encode("latin-1", errors="replace")[:24]
            nb += b"\x00" * (24 - len(nb))
            table += struct.pack("<IHH", cur, sec, sec) + nb
            blob += data
            cur += sec
        full = bytearray(header) + table
        while len(full) % self.SECTOR:
            full.append(0)
        full += blob
        with open(out, "wb") as f:
            f.write(full)
        self.path = out
        self.version = 2
        self.dirty = False
        self.entries.clear()
        self._raw = bytes(full)
        self._parse_v2()
        return out

    def verify(self) -> List[str]:
        issues = []
        seen = set()
        for e in self.entries:
            low = e.name.lower()
            if low in seen:
                issues.append(f"Duplikat: {e.name}")
            seen.add(low)
            if e.size_sectors <= 0:
                issues.append(f"Zerowy rozmiar: {e.name}")
        return issues


# ---------------------------------------------------------------------------
# GXT – hasher, słownik, tagi
# ---------------------------------------------------------------------------
def gxt_hash_sa(key: str) -> int:
    """CRC32-JAMCRC używany przez San Andreas."""
    h = 0xFFFFFFFF
    for ch in key.upper():
        h ^= ord(ch)
        for _ in range(8):
            h = (h >> 1) ^ 0xEDB88320 if (h & 1) else (h >> 1)
    return (~h) & 0xFFFFFFFF

def gxt_hash_legacy(key: str) -> int:
    """Prosty hash używany w niektórych narzędziach III/VC."""
    h = 0
    for ch in key.upper():
        h = (h << 4) + ord(ch)
        g = h & 0xF0000000
        if g:
            h ^= g >> 24
        h &= ~g
        h &= 0xFFFFFFFF
    return h

# Rozszerzalna baza wbudowanych kluczy (hash → nazwa)
BUILTIN_GXT: Dict[int, str] = {
    0x0056529D: "YES",
    0x006D6F4E: "NO",
    0x00A8C1C8: "EXIT",
    0x00B4D4C4: "OK",
    0x00C4A2A0: "CANCEL",
    0x001A2B3C: "HELP",
    0x002B3C4D: "CONTINUE",
    0x003C4D5E: "BACK",
    0x004D5E6F: "QUIT",
    0x005E6F70: "SAVE",
    0x006F7081: "LOAD",
    0x00708192: "NEW_GAME",
    0x008192A3: "OPTIONS",
    0x0092A3B4: "MISSION_PASSED",
    0x00A3B4C5: "MISSION_FAILED",
    0x00B4C5D6: "BUSTED",
    0x00C5D6E7: "WASTED",
    0x00D6E7F8: "RESPECT",
    0x00E7F809: "WANTED_LEVEL",
    0x00F8091A: "MONEY",
    0x00091A2B: "HEALTH",
    0x001A2B3C: "ARMOUR",
    0x002B3C4D: "WEAPON",
    0x003C4D5E: "AMMO",
    0x004D5E6F: "VEHICLE",
    0x005E6F70: "PED",
    0x006F7081: "OBJECT",
    0x00708192: "PICKUP",
    0x008192A3: "MARKER",
    0x0092A3B4: "BLIP",
    0x00A3B4C5: "ZONE",
    0x00B4C5D6: "INTERIOR",
    0x00C5D6E7: "CUTSCENE",
    0x00D6E7F8: "DIALOGUE",
    0x00E7F809: "SUBTITLE",
    0x00F8091A: "HELP_TEXT",
    # możesz dodać setki kolejnych z american.gxt
}

USER_DICT_PATH = os.path.join(os.path.expanduser("~"), "gxt_dictionary.json")

def load_user_dict() -> Dict[int, str]:
    if os.path.isfile(USER_DICT_PATH):
        try:
            with open(USER_DICT_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return {int(k, 16) if isinstance(k, str) and k.startswith("0x") else int(k): v
                    for k, v in raw.items()}
        except Exception:
            pass
    return {}

def save_user_dict(d: Dict[int, str]) -> None:
    try:
        with open(USER_DICT_PATH, "w", encoding="utf-8") as f:
            json.dump({f"0x{k:08X}": v for k, v in sorted(d.items())}, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning("Nie udało się zapisać słownika: %s", e)

def resolve_key(key: str, user: Dict[int, str]) -> str:
    if not (key.startswith("0x") or key.startswith("0X")):
        return key
    try:
        h = int(key, 16)
    except ValueError:
        return key
    if h in user:
        return user[h]
    if h in BUILTIN_GXT:
        return BUILTIN_GXT[h]
    return key

GTA_TAG_RE = re.compile(r"~(?:[a-zA-Z0-9]|k~~[A-Z0-9_]+)~")

def strip_gta_tags(text: str) -> str:
    t = GTA_TAG_RE.sub("", text)
    t = t.replace("~n~", "\n").replace("\\n", "\n")
    return t

@dataclass
class GxtEntry:
    key: str
    text: str
    key_hash: Optional[int] = None
    readable: str = ""

@dataclass
class GxtTable:
    name: str
    entries: List[GxtEntry] = field(default_factory=list)

class GxtDocument:
    def __init__(self):
        self.path = ""
        self.tables: List[GxtTable] = []
        self.is_sa = False
        self.char_size = 2
        self.dirty = False
        self._ver_hdr = b""
        self.user_map = load_user_dict()

    def load(self, path: str) -> "GxtDocument":
        self.path = path
        with open(path, "rb") as f:
            data = f.read()
        if len(data) < 8:
            raise ValueError("Plik za mały")
        r = BinaryReader(data)
        self.tables = []
        self.is_sa = False
        self.char_size = 2
        self._ver_hdr = b""
        first = data[:4]

        if first in (b"\x04\x00\x08\x00", b"\x04\x00\x10\x00"):
            self.is_sa = True
            self.char_size = 1 if first[2] == 0x08 else 2
            self._ver_hdr = first
            r.seek(4)
            if r.read_bytes(4) != b"TABL":
                raise ValueError("SA GXT: brak TABL")
            self._parse_multi(r, data)
        elif first == b"TABL":
            r.seek(4)
            self._parse_multi(r, data)
        elif first == b"TKEY":
            t = GxtTable("MAIN")
            self._parse_single(r, 0, t, False)
            self.tables = [t]
        else:
            raise ValueError(f"Nieznany nagłówek GXT: {first!r}")

        for t in self.tables:
            for e in t.entries:
                e.readable = resolve_key(e.key, self.user_map)
        self.dirty = False
        return self

    def _parse_multi(self, r: BinaryReader, whole: bytes):
        size = r.read_u32()
        end = r.tell() + size
        refs = []
        while r.tell() < end:
            name_b = r.read_bytes(8)
            off = r.read_u32()
            name = name_b.split(b"\x00")[0].decode("latin-1", errors="replace")
            refs.append((name, off))
        for name, offset in refs:
            table = GxtTable(name or "MAIN")
            try:
                start = offset
                if name.upper() != "MAIN" and self.is_sa:
                    if whole[offset:offset + 4] != b"TKEY":
                        start = offset + 8
                self._parse_single(r, start, table, self.is_sa)
                self.tables.append(table)
            except Exception as ex:
                logger.warning("Pomijam tabelę %s: %s", name, ex)

    def _parse_single(self, r: BinaryReader, start: int, table: GxtTable, hashed: bool):
        r.seek(start)
        if r.read_bytes(4) != b"TKEY":
            raise ValueError(f"Brak TKEY @ 0x{start:X}")
        tkey_sz = r.read_u32()
        tkey_end = r.tell() + tkey_sz
        esz = 8 if hashed else 12
        count = tkey_sz // esz
        raw = []
        for _ in range(count):
            if hashed:
                a = r.read_u32()
                b = r.read_u32()
                if a < b:
                    off, h = a, b
                else:
                    h, off = a, b
                raw.append((h, off, None))
            else:
                off = r.read_u32()
                name = r.read_fixed_string(8)
                raw.append((None, off, name))
        r.seek(tkey_end)
        if r.read_bytes(4) != b"TDAT":
            raise ValueError("Brak TDAT")
        tdat_sz = r.read_u32()
        tdat = r.read_bytes(tdat_sz)
        tr = BinaryReader(tdat)
        for h, off, name in raw:
            try:
                tr.seek(off)
            except BinaryReaderError:
                continue
            if hashed:
                text = tr.read_utf16_cstring() if self.char_size == 2 else tr.read_cstring("cp1252")
                key = f"0x{h:08X}"
                table.entries.append(GxtEntry(key, text, key_hash=h))
            else:
                text = tr.read_utf16_cstring() if self.char_size == 2 else tr.read_cstring("latin-1")
                table.entries.append(GxtEntry(name, text))

    def save(self, path: Optional[str] = None):
        path = path or self.path
        if self.is_sa:
            self._save_sa(path)
        else:
            self._save_iii(path)
        self.path = path
        self.dirty = False

    def _save_iii(self, path: str):
        table = self.tables[0] if self.tables else GxtTable("MAIN")
        tkey = bytearray()
        tdat = bytearray()
        for e in table.entries:
            name = e.key.encode("latin-1", errors="replace")[:8]
            name += b"\x00" * (8 - len(name))
            tkey += struct.pack("<I", len(tdat)) + name
            tdat += e.text.encode("utf-16-le") + b"\x00\x00"
        with open(path, "wb") as f:
            f.write(b"TKEY" + struct.pack("<I", len(tkey)) + tkey)
            f.write(b"TDAT" + struct.pack("<I", len(tdat)) + tdat)

    def _save_sa(self, path: str):
        parts = []
        for t in self.tables:
            tkey = bytearray()
            tdat = bytearray()
            for e in t.entries:
                h = e.key_hash if e.key_hash is not None else gxt_hash_sa(e.key)
                tkey += struct.pack("<II", len(tdat), h)
                if self.char_size == 2:
                    tdat += e.text.encode("utf-16-le") + b"\x00\x00"
                else:
                    tdat += e.text.encode("cp1252", errors="replace") + b"\x00"
            parts.append((t.name, tkey, tdat))
        tabl_sz = len(parts) * 12
        base = 4 + 8 + tabl_sz
        offsets = []
        pos = base
        for name, tkey, tdat in parts:
            offsets.append(pos)
            extra = 0 if name.upper() == "MAIN" else 8
            pos += extra + 8 + len(tkey) + 8 + len(tdat)
        with open(path, "wb") as f:
            f.write(self._ver_hdr or b"\x04\x00\x08\x00")
            f.write(b"TABL" + struct.pack("<I", tabl_sz))
            for i, (name, _, _) in enumerate(parts):
                nb = name.encode("latin-1", errors="replace")[:8]
                nb += b"\x00" * (8 - len(nb))
                f.write(nb + struct.pack("<I", offsets[i]))
            for name, tkey, tdat in parts:
                if name.upper() != "MAIN":
                    nb = name.encode("latin-1", errors="replace")[:8]
                    nb += b"\x00" * (8 - len(nb))
                    f.write(nb)
                f.write(b"TKEY" + struct.pack("<I", len(tkey)) + tkey)
                f.write(b"TDAT" + struct.pack("<I", len(tdat)) + tdat)

    def map_hash(self, h: int, name: str):
        self.user_map[h] = name
        save_user_dict(self.user_map)
        for t in self.tables:
            for e in t.entries:
                if e.key_hash == h:
                    e.readable = name


# ---------------------------------------------------------------------------
# IDE / IPL
# ---------------------------------------------------------------------------
@dataclass
class IdeObject:
    section: str
    obj_id: Optional[int]
    model_name: str = ""
    txd_name: str = ""
    raw: List[str] = field(default_factory=list)

class IdeDocument:
    KNOWN = {"OBJS", "TOBJ", "PEDS", "CARS", "HIER", "TXDP", "2DFX", "PATH", "WEAP", "ANIM"}
    def __init__(self):
        self.path = ""
        self.by_section: Dict[str, List[IdeObject]] = {}
        self.dirty = False
    def load(self, path: str) -> "IdeDocument":
        self.path = path
        self.by_section = {}
        cur = None
        with open(path, "r", encoding="latin-1", errors="replace") as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                u = line.upper()
                if u in self.KNOWN:
                    cur = u
                    self.by_section.setdefault(cur, [])
                    continue
                if u == "END":
                    cur = None
                    continue
                if cur is None:
                    continue
                fields = [t.strip() for t in line.split(",")]
                obj = IdeObject(cur, None, raw=fields)
                try:
                    if fields and fields[0].replace(".", "", 1).isdigit():
                        obj.obj_id = int(float(fields[0]))
                    if len(fields) > 1:
                        obj.model_name = fields[1]
                    if len(fields) > 2:
                        obj.txd_name = fields[2]
                except Exception:
                    pass
                self.by_section[cur].append(obj)
        self.dirty = False
        return self
    def save(self, path: Optional[str] = None):
        path = path or self.path
        lines = []
        for sec, objs in self.by_section.items():
            lines.append(sec)
            for o in objs:
                lines.append(", ".join(o.raw) if o.raw else f"{o.obj_id}, {o.model_name}, {o.txd_name}")
            lines.append("end")
            lines.append("")
        with open(path, "w", encoding="latin-1", errors="replace") as f:
            f.write("\n".join(lines))
        self.path = path
        self.dirty = False

@dataclass
class IplInstance:
    obj_id: int
    model_name: str
    interior: int
    position: Tuple[float, float, float]
    rotation: Tuple[float, float, float, float]
    lod: Optional[int] = None
    raw: List[str] = field(default_factory=list)

class IplDocument:
    KNOWN = {"INST", "ZONE", "CULL", "PICK", "PATH", "GRGE", "ENEX", "JUMP", "TCYC", "AUD", "MULT", "CARS", "OCCL"}
    def __init__(self):
        self.path = ""
        self.instances: List[IplInstance] = []
        self.is_binary = False
        self.dirty = False
    def load(self, path: str) -> "IplDocument":
        self.path = path
        with open(path, "rb") as f:
            head = f.read(4)
        if head == b"bnry":
            self.is_binary = True
            self._load_bin(path)
        else:
            self.is_binary = False
            self._load_text(path)
        self.dirty = False
        return self
    def _load_text(self, path: str):
        self.instances = []
        cur = None
        with open(path, "r", encoding="latin-1", errors="replace") as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                u = line.upper()
                if u in self.KNOWN:
                    cur = u
                    continue
                if u == "END":
                    cur = None
                    continue
                if cur != "INST":
                    continue
                fields = [t.strip() for t in line.split(",")]
                try:
                    if len(fields) >= 11:
                        oid = int(fields[0])
                        model = fields[1]
                        inter = int(fields[2])
                        pos = (float(fields[3]), float(fields[4]), float(fields[5]))
                        rot = (float(fields[6]), float(fields[7]), float(fields[8]), float(fields[9]))
                        lod = int(fields[10])
                        self.instances.append(IplInstance(oid, model, inter, pos, rot, lod, fields))
                except Exception:
                    pass
    def _load_bin(self, path: str):
        with open(path, "rb") as f:
            data = f.read()
        r = BinaryReader(data)
        try:
            r.read_bytes(4)
            n = r.read_u32()
            for _ in range(8):
                r.read_u32()
            self.instances = []
            for _ in range(n):
                px, py, pz = r.read_f32(), r.read_f32(), r.read_f32()
                rx, ry, rz, rw = r.read_f32(), r.read_f32(), r.read_f32(), r.read_f32()
                oid = r.read_i32()
                inter = r.read_i32()
                lod = r.read_i32()
                self.instances.append(IplInstance(oid, f"model_{oid}", inter, (px, py, pz), (rx, ry, rz, rw), lod))
        except BinaryReaderError:
            pass
    def save(self, path: Optional[str] = None):
        path = path or self.path
        if self.is_binary:
            messagebox.showwarning("Binary IPL", "Zapis binarnego IPL nie jest wspierany.")
            return
        lines = ["inst"]
        for i in self.instances:
            lod = i.lod if i.lod is not None else -1
            lines.append(f"{i.obj_id}, {i.model_name}, {i.interior}, "
                         f"{i.position[0]:.6f}, {i.position[1]:.6f}, {i.position[2]:.6f}, "
                         f"{i.rotation[0]:.6f}, {i.rotation[1]:.6f}, {i.rotation[2]:.6f}, {i.rotation[3]:.6f}, {lod}")
        lines.append("end")
        with open(path, "w", encoding="latin-1", errors="replace") as f:
            f.write("\n".join(lines) + "\n")
        self.path = path
        self.dirty = False


# ---------------------------------------------------------------------------
# Audio SDT → WAV
# ---------------------------------------------------------------------------
@dataclass
class SdtEntry:
    index: int
    offset: int
    size: int
    rate: int

class SdtArchive:
    def __init__(self, sdt: str, raw: str):
        self.sdt = sdt
        self.raw = raw
        self.entries: List[SdtEntry] = []
    def load(self) -> "SdtArchive":
        with open(self.sdt, "rb") as f:
            data = f.read()
        r = BinaryReader(data)
        idx = 0
        while r.remaining() >= 12:
            off = r.read_u32()
            sz = r.read_u32()
            rate = r.read_u32() or 22050
            self.entries.append(SdtEntry(idx, off, sz, rate))
            idx += 1
        return self
    def extract(self, e: SdtEntry) -> bytes:
        with open(self.raw, "rb") as f:
            f.seek(e.offset)
            return f.read(e.size)
    def to_wav(self, e: SdtEntry, out: str):
        raw = self.extract(e)
        if len(raw) % 2:
            raw += b"\x00"
        with wave.open(out, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(e.rate)
            w.writeframes(raw)


# ---------------------------------------------------------------------------
# DFF / COL / TXD parsers
# ---------------------------------------------------------------------------
class TxdParser:
    def __init__(self, path: str):
        self.path = path
        self.textures: List[str] = []
        self.rw_ver = ""
        self.raw = b""
    def parse(self) -> "TxdParser":
        with open(self.path, "rb") as f:
            self.raw = f.read()
        r = BinaryReader(self.raw)
        w = RwSectionReader(r)
        root = w.read_header()
        if root.type_id != RW_TEXDICTIONARY:
            raise ValueError("To nie jest TXD")
        self.rw_ver, _ = decode_rw_version(root.library_id)
        end = root.data_offset + root.size
        for ch in w.children(end):
            if ch.type_id == RW_TEXTURENATIVE:
                r.seek(ch.data_offset)
                try:
                    sc = w.read_header()
                    if sc.type_id == RW_STRUCT:
                        r.read_u32()
                        r.read_u16(); r.read_u8(); r.read_u8()
                        name = r.read_fixed_string(32)
                        self.textures.append(name)
                except Exception:
                    pass
        return self

class DffParser:
    def __init__(self, path: str):
        self.path = path
        self.rw_ver = ""
        self.frames = 0
        self.geoms = 0
        self.atomics = 0
        self.verts = 0
        self.tris = 0
        self.raw = b""
    def parse(self) -> "DffParser":
        with open(self.path, "rb") as f:
            self.raw = f.read()
        r = BinaryReader(self.raw)
        w = RwSectionReader(r)
        root = w.read_header()
        if root.type_id != RW_CLUMP:
            raise ValueError("To nie jest DFF")
        self.rw_ver, _ = decode_rw_version(root.library_id)
        end = root.data_offset + root.size
        for ch in w.children(end):
            if ch.type_id == RW_FRAMELIST:
                r.seek(ch.data_offset)
                for c in w.children(ch.data_offset + ch.size):
                    if c.type_id == RW_STRUCT:
                        r.seek(c.data_offset)
                        self.frames = r.read_u32()
            elif ch.type_id == RW_GEOMETRYLIST:
                r.seek(ch.data_offset)
                for c in w.children(ch.data_offset + ch.size):
                    if c.type_id == RW_GEOMETRY:
                        self.geoms += 1
                        r.seek(c.data_offset)
                        try:
                            sc = w.read_header()
                            if sc.type_id == RW_STRUCT:
                                r.read_u32()
                                ntri = r.read_u32()
                                nvert = r.read_u32()
                                self.tris += ntri
                                self.verts += nvert
                        except Exception:
                            pass
            elif ch.type_id == RW_ATOMIC:
                self.atomics += 1
        return self

class ColParser:
    SIGS = {b"COLL": 1, b"COL2": 2, b"COL3": 3, b"COL4": 4}
    def __init__(self, path: str):
        self.path = path
        self.models: List[Dict] = []
        self.raw = b""
    def parse(self) -> "ColParser":
        with open(self.path, "rb") as f:
            self.raw = f.read()
        r = BinaryReader(self.raw)
        while r.remaining() >= 8:
            start = r.tell()
            sig = r.read_bytes(4)
            if sig not in self.SIGS:
                if start == 0:
                    raise ValueError("To nie jest COL")
                break
            size = r.read_u32()
            end = r.tell() + size
            name = r.read_fixed_string(22)
            mid = r.read_i16()
            self.models.append({"name": name, "id": mid, "ver": sig.decode()})
            r.seek(end)
        return self


# ---------------------------------------------------------------------------
# Prosty SCM Opcode Viewer
# ---------------------------------------------------------------------------
KNOWN_OPCODES = {
    0x0001: "WAIT",
    0x0002: "GOTO",
    0x0003: "SHAKE_CAM",
    0x0050: "GOSUB",
    0x0051: "RETURN",
    0x00D6: "IF",
    0x004D: "GOTO_IF_FALSE",
    0x00A0: "IS_CHAR_DEAD",
    0x00A1: "IS_CHAR_IN_AREA",
    0x01B4: "SET_PLAYER_CONTROL",
    0x03E5: "PRINT_HELP",
    0x03E6: "CLEAR_HELP",
    0x00BA: "PRINT_BIG",
    0x00BC: "PRINT",
    0x00BE: "PRINT_NOW",
}

class ScmViewer:
    def __init__(self, path: str):
        self.path = path
        self.raw = b""
        self.lines: List[str] = []
    def load(self) -> "ScmViewer":
        with open(self.path, "rb") as f:
            self.raw = f.read()
        # MAIN.SCM ma nagłówek, potem skrypt. Pokazujemy proste dekodowanie opcode'ów.
        r = BinaryReader(self.raw)
        # pomijamy typowy header (różni się między grami) – bierzemy pierwsze 64 KB jako podgląd
        limit = min(len(self.raw), 65536)
        pos = 0
        while pos + 2 <= limit:
            try:
                op = struct.unpack_from("<H", self.raw, pos)[0]
                name = KNOWN_OPCODES.get(op, f"OP_{op:04X}")
                self.lines.append(f"0x{pos:06X}: {op:04X}  {name}")
                pos += 2
                # proste pominięcie argumentów (heurystyka)
                if op in (0x0001, 0x0002, 0x0050, 0x004D):
                    pos += 4
                elif op in (0x00BA, 0x00BC, 0x00BE, 0x03E5):
                    pos += 8
            except Exception:
                pos += 1
        return self


# ---------------------------------------------------------------------------
# Główna aplikacja
# ---------------------------------------------------------------------------
class GtaModToolkitApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("GTA Mod Toolkit – Ultimate Edition")
        self.root.geometry("1450x880")
        self.root.minsize(1050, 680)

        self.img: Optional[ImgArchive] = None
        self.gxt: Optional[GxtDocument] = None
        self.ide: Optional[IdeDocument] = None
        self.ipl: Optional[IplDocument] = None
        self.sdt: Optional[SdtArchive] = None
        self.scm: Optional[ScmViewer] = None

        self.status = tk.StringVar(value="Gotowy.")
        self.mono = self._font()

        self._build_menu()
        self._build_ui()
        self._bind()
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _font(self):
        for c in ("Consolas", "Courier New", "DejaVu Sans Mono"):
            if c in tkfont.families():
                return (c, 10)
        return ("TkFixedFont", 10)

    def _build_menu(self):
        m = tk.Menu(self.root)
        f = tk.Menu(m, tearoff=0)
        f.add_command(label="Otwórz IMG…", command=self.img_open, accelerator="Ctrl+O")
        f.add_command(label="Otwórz GXT…", command=self.gxt_open)
        f.add_command(label="Otwórz IDE…", command=self.ide_open)
        f.add_command(label="Otwórz IPL…", command=self.ipl_open)
        f.add_command(label="Otwórz SDT/RAW…", command=self.audio_open)
        f.add_command(label="Otwórz MAIN.SCM…", command=self.scm_open)
        f.add_separator()
        f.add_command(label="Zapisz", command=self._save, accelerator="Ctrl+S")
        f.add_separator()
        f.add_command(label="Wyjście", command=self._close)
        m.add_cascade(label="Plik", menu=f)
        h = tk.Menu(m, tearoff=0)
        h.add_command(label="O programie", command=lambda: messagebox.showinfo(
            "O programie",
            "GTA Mod Toolkit – Ultimate Edition\n\n"
            "IMG • GXT (CRC32 reverse) • IDE/IPL + 2D Map\n"
            "DFF/COL Inspector • Audio → WAV • MAIN.SCM Viewer\n\n"
            "Czysty Python 3 + tkinter"))
        m.add_cascade(label="Pomoc", menu=h)
        self.root.config(menu=m)

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=6)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)

        self.nb = ttk.Notebook(outer)
        self.nb.grid(row=0, column=0, sticky="nsew")

        self.t_img = ttk.Frame(self.nb)
        self.t_gxt = ttk.Frame(self.nb)
        self.t_map = ttk.Frame(self.nb)
        self.t_aud = ttk.Frame(self.nb)
        self.t_ins = ttk.Frame(self.nb)
        self.t_scm = ttk.Frame(self.nb)

        self.nb.add(self.t_img, text=" IMG Manager ")
        self.nb.add(self.t_gxt, text=" GXT Editor ")
        self.nb.add(self.t_map, text=" IDE / IPL + 2D Map ")
        self.nb.add(self.t_aud, text=" Audio / SAAT ")
        self.nb.add(self.t_ins, text=" Asset Inspector ")
        self.nb.add(self.t_scm, text=" MAIN.SCM Viewer ")

        self._ui_img()
        self._ui_gxt()
        self._ui_map()
        self._ui_aud()
        self._ui_ins()
        self._ui_scm()

        st = ttk.Frame(outer)
        st.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        ttk.Label(st, textvariable=self.status).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.prog = ttk.Progressbar(st, mode="indeterminate", length=150)
        self.prog.pack(side=tk.RIGHT)

    def _bind(self):
        self.root.bind("<Control-o>", lambda e: self.img_open())
        self.root.bind("<Control-s>", lambda e: self._save())
        self.root.bind("<Control-f>", lambda e: self._focus_search())

    def _thread(self, fn: Callable, done: Optional[Callable] = None):
        def worker():
            try:
                res = fn()
                if done:
                    self.root.after(0, lambda: done(res))
            except Exception as ex:
                tb = traceback.format_exc()
                self.root.after(0, lambda: messagebox.showerror("Błąd", f"{ex}\n\n{tb[:800]}"))
            finally:
                self.root.after(0, self.prog.stop)
        self.prog.start(12)
        threading.Thread(target=worker, daemon=True).start()

    # ==================== IMG ====================
    def _ui_img(self):
        p = ttk.Panedwindow(self.t_img, orient=tk.HORIZONTAL)
        p.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(p, padding=4)
        right = ttk.Frame(p, padding=4)
        p.add(left, weight=1)
        p.add(right, weight=3)

        for txt, cmd in [
            ("Otwórz IMG", self.img_open), ("Importuj pliki", self.img_import),
            ("Eksportuj zaznaczone", self.img_exp_sel), ("Eksportuj wszystko", self.img_exp_all),
            ("Zamień plik", self.img_replace), ("Usuń", self.img_delete),
            ("Rebuild IMG", self.img_rebuild), ("Weryfikuj", self.img_verify),
            ("Zapisz jako…", self.img_saveas)
        ]:
            ttk.Button(left, text=txt, command=cmd).pack(fill=tk.X, pady=1)

        ttk.Label(left, text="Filtr na żywo:").pack(anchor="w", pady=(8, 0))
        self.img_filt = tk.StringVar()
        self.img_filt.trace_add("write", lambda *_: self.img_refresh())
        self.img_filt_e = ttk.Entry(left, textvariable=self.img_filt)
        self.img_filt_e.pack(fill=tk.X)

        cols = ("name", "off", "size")
        self.img_tree = ttk.Treeview(right, columns=cols, show="headings", selectmode="extended")
        self.img_tree.heading("name", text="Nazwa")
        self.img_tree.heading("off", text="Offset (sektory)")
        self.img_tree.heading("size", text="Rozmiar (B)")
        self.img_tree.column("name", width=280)
        vsb = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.img_tree.yview)
        self.img_tree.configure(yscrollcommand=vsb.set)
        self.img_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

    def img_open(self):
        path = filedialog.askopenfilename(filetypes=[("IMG", "*.img"), ("Wszystkie", "*.*")])
        if not path:
            return
        def work():
            dirp = os.path.splitext(path)[0] + ".dir"
            dirp = dirp if os.path.isfile(dirp) else None
            return ImgArchive(path, dirp).load()
        def done(arch):
            self.img = arch
            self.img_refresh()
            self.status.set(f"IMG v{arch.version}: {len(arch.entries)} wpisów")
            self.nb.select(self.t_img)
        self._thread(work, done)

    def img_refresh(self):
        for i in self.img_tree.get_children():
            self.img_tree.delete(i)
        if not self.img:
            return
        f = self.img_filt.get().strip().lower()
        for e in self.img.entries:
            if f and f not in e.name.lower():
                continue
            self.img_tree.insert("", tk.END, iid=e.name, values=(e.name, e.offset_sectors, f"{e.size_bytes:,}"))

    def img_import(self):
        if not self.img:
            return
        paths = filedialog.askopenfilenames()
        if not paths:
            return
        for p in paths:
            name = os.path.basename(p)
            with open(p, "rb") as f:
                data = f.read()
            if any(e.name.lower() == name.lower() for e in self.img.entries):
                if messagebox.askyesno("Zamienić?", f"{name} już istnieje. Zamienić?"):
                    self.img.replace(name, data)
            else:
                self.img.add(name, data)
        self.img_refresh()
        self.status.set("Zaimportowano – zrób Rebuild")

    def img_exp_sel(self):
        if not self.img:
            return
        sels = self.img_tree.selection()
        if not sels:
            return
        folder = filedialog.askdirectory()
        if not folder:
            return
        for name in sels:
            e = next(x for x in self.img.entries if x.name == name)
            with open(os.path.join(folder, e.name), "wb") as f:
                f.write(self.img.extract(e))
        self.status.set(f"Wyeksportowano {len(sels)}")

    def img_exp_all(self):
        if not self.img:
            return
        folder = filedialog.askdirectory()
        if not folder:
            return
        def work():
            for e in self.img.entries:
                with open(os.path.join(folder, e.name), "wb") as f:
                    f.write(self.img.extract(e))
            return len(self.img.entries)
        self._thread(work, lambda n: self.status.set(f"Wyeksportowano {n} plików"))

    def img_replace(self):
        if not self.img:
            return
        sels = self.img_tree.selection()
        if len(sels) != 1:
            return
        path = filedialog.askopenfilename()
        if not path:
            return
        with open(path, "rb") as f:
            data = f.read()
        self.img.replace(sels[0], data)
        self.img_refresh()

    def img_delete(self):
        if not self.img:
            return
        for name in self.img_tree.selection():
            self.img.remove(name)
        self.img_refresh()

    def img_rebuild(self):
        if not self.img:
            return
        if not messagebox.askyesno("Rebuild", "Przebudować archiwum do VER2?"):
            return
        def work():
            return self.img.rebuild()
        self._thread(work, lambda p: (self.img_refresh(), self.status.set(f"Rebuild: {p}"), messagebox.showinfo("OK", p)))

    def img_verify(self):
        if not self.img:
            return
        issues = self.img.verify()
        if issues:
            messagebox.showwarning("Problemy", "\n".join(issues[:25]))
        else:
            messagebox.showinfo("OK", "Archiwum wygląda spójnie")

    def img_saveas(self):
        if not self.img:
            return
        path = filedialog.asksaveasfilename(defaultextension=".img")
        if path:
            self._thread(lambda: self.img.rebuild(path), lambda p: self.status.set(f"Zapisano {p}"))

    # ==================== GXT ====================
    def _ui_gxt(self):
        p = ttk.Panedwindow(self.t_gxt, orient=tk.HORIZONTAL)
        p.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(p, padding=4)
        right = ttk.Frame(p, padding=4)
        p.add(left, weight=2)
        p.add(right, weight=3)

        tb = ttk.Frame(left)
        tb.pack(fill=tk.X)
        ttk.Button(tb, text="Otwórz GXT", command=self.gxt_open).pack(side=tk.LEFT, padx=1)
        ttk.Button(tb, text="Zapisz", command=self.gxt_save).pack(side=tk.LEFT, padx=1)
        ttk.Button(tb, text="Dodaj", command=self.gxt_add).pack(side=tk.LEFT, padx=1)
        ttk.Button(tb, text="Usuń", command=self.gxt_del).pack(side=tk.LEFT, padx=1)
        ttk.Button(tb, text="Mapuj Hash", command=self.gxt_map).pack(side=tk.LEFT, padx=1)

        ttk.Label(left, text="Szukaj (nazwa / hash / treść / regex):").pack(anchor="w", pady=(6, 0))
        self.gxt_search = tk.StringVar()
        self.gxt_search.trace_add("write", lambda *_: self.gxt_refresh())
        self.gxt_search_e = ttk.Entry(left, textvariable=self.gxt_search)
        self.gxt_search_e.pack(fill=tk.X, pady=2)

        cols = ("table", "readable", "hash", "len", "frag")
        self.gxt_tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
        self.gxt_tree.heading("table", text="Tabela")
        self.gxt_tree.heading("readable", text="Nazwa klucza")
        self.gxt_tree.heading("hash", text="Hash (Hex)")
        self.gxt_tree.heading("len", text="Dł.")
        self.gxt_tree.heading("frag", text="Fragment treści")
        self.gxt_tree.column("table", width=70)
        self.gxt_tree.column("readable", width=130)
        self.gxt_tree.column("hash", width=100)
        self.gxt_tree.column("len", width=40)
        self.gxt_tree.column("frag", width=200)
        vsb = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.gxt_tree.yview)
        self.gxt_tree.configure(yscrollcommand=vsb.set)
        self.gxt_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=4)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.gxt_tree.bind("<<TreeviewSelect>>", self.gxt_select)

        # prawa strona – edytor + tagi
        tagf = ttk.Frame(right)
        tagf.pack(fill=tk.X)
        for label, tag in [
            ("~r~", "~r~"), ("~g~", "~g~"), ("~b~", "~b~"), ("~w~", "~w~"),
            ("~h~", "~h~"), ("~n~", "~n~"), ("~k~", "~k~~PED_FIREWEAPON~")
        ]:
            ttk.Button(tagf, text=label, width=6,
                       command=lambda t=tag: self._insert_tag(t)).pack(side=tk.LEFT, padx=1)

        ttk.Label(right, text="Tekst surowy (z tagami GTA):").pack(anchor="w")
        self.gxt_text = scrolledtext.ScrolledText(right, height=9, wrap=tk.WORD, font=self.mono)
        self.gxt_text.pack(fill=tk.BOTH, expand=True)
        self.gxt_text.bind("<KeyRelease>", self.gxt_edit)

        ttk.Label(right, text="Podgląd na żywo (bez tagów):").pack(anchor="w", pady=(4, 0))
        self.gxt_prev = scrolledtext.ScrolledText(right, height=5, wrap=tk.WORD, font=self.mono,
                                                  bg="#1e1e1e", fg="#dcdcdc")
        self.gxt_prev.pack(fill=tk.BOTH, expand=True)
        self.gxt_prev.configure(state=tk.DISABLED)
        self.gxt_info = ttk.Label(right, text="")
        self.gxt_info.pack(anchor="w")

    def _insert_tag(self, tag: str):
        self.gxt_text.insert(tk.INSERT, tag)
        self.gxt_edit()

    def gxt_open(self):
        path = filedialog.askopenfilename(filetypes=[("GXT", "*.gxt"), ("Wszystkie", "*.*")])
        if not path:
            return
        def work():
            return GxtDocument().load(path)
        def done(doc):
            self.gxt = doc
            self.gxt_refresh()
            fmt = "SA" if doc.is_sa else "III/VC"
            total = sum(len(t.entries) for t in doc.tables)
            self.status.set(f"GXT ({fmt}) – {total} wpisów, {len(doc.tables)} tabel")
            self.nb.select(self.t_gxt)
        self._thread(work, done)

    def gxt_refresh(self):
        for i in self.gxt_tree.get_children():
            self.gxt_tree.delete(i)
        if not self.gxt:
            return
        needle = self.gxt_search.get().strip()
        use_re = False
        cre = None
        if needle:
            try:
                cre = re.compile(needle, re.IGNORECASE)
                use_re = True
            except re.error:
                needle = needle.lower()
        for t in self.gxt.tables:
            for e in t.entries:
                if needle:
                    if use_re:
                        if not (cre.search(e.readable) or cre.search(e.key) or cre.search(e.text)):
                            continue
                    else:
                        if (needle not in e.readable.lower() and needle not in e.key.lower()
                                and needle not in e.text.lower()):
                            continue
                frag = e.text[:55].replace("\n", " ")
                self.gxt_tree.insert("", tk.END, values=(t.name, e.readable, e.key, len(e.text), frag),
                                     tags=(t.name, e.key))

    def gxt_select(self, _=None):
        sel = self.gxt_tree.selection()
        if not sel or not self.gxt:
            return
        vals = self.gxt_tree.item(sel[0], "values")
        table, readable, key = vals[0], vals[1], vals[2]
        for t in self.gxt.tables:
            if t.name == table:
                for e in t.entries:
                    if e.key == key:
                        self.gxt_text.delete("1.0", tk.END)
                        self.gxt_text.insert("1.0", e.text)
                        self._upd_prev(e.text)
                        self.gxt_info.config(text=f"Tabela: {table} | Nazwa: {readable} | Hash: {key}")
                        return

    def _upd_prev(self, text: str):
        clean = strip_gta_tags(text)
        self.gxt_prev.configure(state=tk.NORMAL)
        self.gxt_prev.delete("1.0", tk.END)
        self.gxt_prev.insert("1.0", clean)
        self.gxt_prev.configure(state=tk.DISABLED)

    def gxt_edit(self, _=None):
        if not self.gxt:
            return
        sel = self.gxt_tree.selection()
        if not sel:
            return
        vals = self.gxt_tree.item(sel[0], "values")
        table, key = vals[0], vals[2]
        new = self.gxt_text.get("1.0", tk.END).rstrip("\n")
        self._upd_prev(new)
        for t in self.gxt.tables:
            if t.name == table:
                for e in t.entries:
                    if e.key == key:
                        if e.text != new:
                            e.text = new
                            self.gxt.dirty = True
                        return

    def gxt_add(self):
        if not self.gxt:
            return
        key = simpledialog.askstring("Klucz", "Nazwa klucza lub 0xHASH:")
        if not key:
            return
        text = simpledialog.askstring("Tekst", "Treść:", initialvalue="") or ""
        t = self.gxt.tables[0]
        if self.gxt.is_sa:
            try:
                h = int(key, 16) if key.lower().startswith("0x") else gxt_hash_sa(key)
            except ValueError:
                h = gxt_hash_sa(key)
            e = GxtEntry(f"0x{h:08X}", text, key_hash=h)
            e.readable = resolve_key(e.key, self.gxt.user_map)
            t.entries.append(e)
        else:
            e = GxtEntry(key[:8], text)
            e.readable = key[:8]
            t.entries.append(e)
        self.gxt.dirty = True
        self.gxt_refresh()

    def gxt_del(self):
        if not self.gxt:
            return
        sel = self.gxt_tree.selection()
        if not sel:
            return
        vals = self.gxt_tree.item(sel[0], "values")
        table, key = vals[0], vals[2]
        for t in self.gxt.tables:
            if t.name == table:
                t.entries = [e for e in t.entries if e.key != key]
                self.gxt.dirty = True
                break
        self.gxt_refresh()

    def gxt_map(self):
        if not self.gxt:
            return
        sel = self.gxt_tree.selection()
        if not sel:
            messagebox.showinfo("Info", "Zaznacz wpis z hashem")
            return
        vals = self.gxt_tree.item(sel[0], "values")
        key = vals[2]
        if not key.lower().startswith("0x"):
            messagebox.showinfo("Info", "Tylko klucze SA (0x...) można mapować")
            return
        try:
            h = int(key, 16)
        except ValueError:
            return
        name = simpledialog.askstring("Mapuj Hash", f"Czytelna nazwa dla {key}:", initialvalue=vals[1])
        if name:
            self.gxt.map_hash(h, name)
            self.gxt_refresh()
            self.status.set(f"Zmapowano {key} → {name}")

    def gxt_save(self):
        if not self.gxt:
            return
        path = self.gxt.path or filedialog.asksaveasfilename(defaultextension=".gxt")
        if not path:
            return
        def work():
            self.gxt.save(path)
            return path
        self._thread(work, lambda p: (self.status.set(f"Zapisano {p}"), messagebox.showinfo("Zapisano", p)))

    # ==================== IDE/IPL + 2D Map ====================
    def _ui_map(self):
        top = ttk.Frame(self.t_map, padding=4)
        top.pack(fill=tk.X)
        ttk.Button(top, text="Otwórz IDE", command=self.ide_open).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="Otwórz IPL", command=self.ipl_open).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="Zapisz", command=self.map_save).pack(side=tk.LEFT, padx=2)
        ttk.Label(top, text="Filtr:").pack(side=tk.LEFT, padx=(10, 2))
        self.map_filt = tk.StringVar()
        self.map_filt.trace_add("write", lambda *_: self.map_refresh())
        ttk.Entry(top, textvariable=self.map_filt, width=22).pack(side=tk.LEFT)

        paned = ttk.Panedwindow(self.t_map, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        left = ttk.Frame(paned)
        paned.add(left, weight=2)
        self.map_tree = ttk.Treeview(left, show="headings", selectmode="browse")
        self.map_tree.pack(fill=tk.BOTH, expand=True)
        self.map_tree.bind("<<TreeviewSelect>>", self.map_select)

        right = ttk.Frame(paned)
        paned.add(right, weight=3)
        ttk.Label(right, text="Mapa 2D (przeciągnij = pan, kółko = zoom)").pack(anchor="w")
        self.canvas = tk.Canvas(right, bg="#0a0a12", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<ButtonPress-1>", self._map_down)
        self.canvas.bind("<B1-Motion>", self._map_move)
        self.canvas.bind("<MouseWheel>", self._map_wheel)
        self.scale = 0.04
        self.ox = self.oy = 0.0
        self._drag = None
        self._sel_idx = None

        edit = ttk.Frame(right)
        edit.pack(fill=tk.X, pady=4)
        ttk.Label(edit, text="X").pack(side=tk.LEFT)
        self.ex = ttk.Entry(edit, width=9); self.ex.pack(side=tk.LEFT, padx=2)
        ttk.Label(edit, text="Y").pack(side=tk.LEFT)
        self.ey = ttk.Entry(edit, width=9); self.ey.pack(side=tk.LEFT, padx=2)
        ttk.Label(edit, text="Z").pack(side=tk.LEFT)
        self.ez = ttk.Entry(edit, width=9); self.ez.pack(side=tk.LEFT, padx=2)
        ttk.Button(edit, text="Zastosuj pozycję", command=self.map_apply).pack(side=tk.LEFT, padx=6)
        self.map_mode = "none"

    def ide_open(self):
        path = filedialog.askopenfilename(filetypes=[("IDE", "*.ide")])
        if not path:
            return
        def work():
            return IdeDocument().load(path)
        def done(doc):
            self.ide = doc
            self.ipl = None
            self.map_mode = "ide"
            self.map_tree["columns"] = ("sec", "id", "model", "txd")
            for c, t in zip(("sec", "id", "model", "txd"), ("Sekcja", "ID", "Model", "TXD")):
                self.map_tree.heading(c, text=t)
                self.map_tree.column(c, width=90)
            self.map_refresh()
            self.status.set(f"IDE: {path}")
            self.nb.select(self.t_map)
        self._thread(work, done)

    def ipl_open(self):
        path = filedialog.askopenfilename(filetypes=[("IPL", "*.ipl")])
        if not path:
            return
        def work():
            return IplDocument().load(path)
        def done(doc):
            self.ipl = doc
            self.ide = None
            self.map_mode = "ipl"
            self.map_tree["columns"] = ("id", "model", "x", "y", "z")
            for c, t in zip(("id", "model", "x", "y", "z"), ("ID", "Model", "X", "Y", "Z")):
                self.map_tree.heading(c, text=t)
                self.map_tree.column(c, width=80)
            self.map_refresh()
            self._draw()
            self.status.set(f"IPL: {len(doc.instances)} instancji")
            self.nb.select(self.t_map)
        self._thread(work, done)

    def map_refresh(self):
        for i in self.map_tree.get_children():
            self.map_tree.delete(i)
        f = self.map_filt.get().strip().lower()
        if self.map_mode == "ide" and self.ide:
            for sec, objs in self.ide.by_section.items():
                for o in objs:
                    if f and f not in (o.model_name + str(o.obj_id)).lower():
                        continue
                    self.map_tree.insert("", tk.END, values=(sec, o.obj_id or "", o.model_name, o.txd_name))
        elif self.map_mode == "ipl" and self.ipl:
            for idx, inst in enumerate(self.ipl.instances):
                if f and f not in (inst.model_name + str(inst.obj_id)).lower():
                    continue
                self.map_tree.insert("", tk.END, iid=str(idx), values=(
                    inst.obj_id, inst.model_name,
                    f"{inst.position[0]:.2f}", f"{inst.position[1]:.2f}", f"{inst.position[2]:.2f}"))

    def map_select(self, _=None):
        if self.map_mode != "ipl" or not self.ipl:
            return
        sel = self.map_tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        self._sel_idx = idx
        inst = self.ipl.instances[idx]
        self.ex.delete(0, tk.END); self.ex.insert(0, f"{inst.position[0]:.4f}")
        self.ey.delete(0, tk.END); self.ey.insert(0, f"{inst.position[1]:.4f}")
        self.ez.delete(0, tk.END); self.ez.insert(0, f"{inst.position[2]:.4f}")
        self._draw()

    def map_apply(self):
        if self._sel_idx is None or not self.ipl:
            return
        try:
            x = float(self.ex.get()); y = float(self.ey.get()); z = float(self.ez.get())
            self.ipl.instances[self._sel_idx].position = (x, y, z)
            self.ipl.dirty = True
            self.map_refresh()
            self._draw()
        except ValueError:
            messagebox.showerror("Błąd", "Nieprawidłowe liczby")

    def _draw(self):
        c = self.canvas
        c.delete("all")
        if not self.ipl or not self.ipl.instances:
            return
        w = c.winfo_width() or 500
        h = c.winfo_height() or 400
        for idx, inst in enumerate(self.ipl.instances):
            sx = (inst.position[0] + self.ox) * self.scale + w / 2
            sy = h / 2 - (inst.position[1] + self.oy) * self.scale
            col = "#ff5555" if idx == self._sel_idx else "#55aaff"
            c.create_oval(sx - 3, sy - 3, sx + 3, sy + 3, fill=col, outline="")
        c.create_text(6, 6, anchor="nw", fill="#888",
                      text=f"scale={self.scale:.4f}  n={len(self.ipl.instances)}")

    def _map_down(self, e):
        self._drag = (e.x, e.y, self.ox, self.oy)

    def _map_move(self, e):
        if self._drag:
            x0, y0, ox, oy = self._drag
            self.ox = ox + (e.x - x0) / self.scale
            self.oy = oy + (y0 - e.y) / self.scale
            self._draw()

    def _map_wheel(self, e):
        self.scale = max(0.001, min(2.0, self.scale * (1.12 if e.delta > 0 else 0.89)))
        self._draw()

    def map_save(self):
        if self.ide:
            self.ide.save()
            self.status.set("IDE zapisane")
        elif self.ipl:
            self.ipl.save()
            self.status.set("IPL zapisane")

    # ==================== Audio ====================
    def _ui_aud(self):
        top = ttk.Frame(self.t_aud, padding=4)
        top.pack(fill=tk.X)
        ttk.Button(top, text="Otwórz SDT+RAW", command=self.audio_open).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="Eksport zaznaczonych → WAV", command=self.audio_wav_sel).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="Eksport wszystkich → WAV", command=self.audio_wav_all).pack(side=tk.LEFT, padx=2)

        cols = ("idx", "off", "size", "rate")
        self.aud_tree = ttk.Treeview(self.t_aud, columns=cols, show="headings")
        for c, t in zip(cols, ("Index", "Offset", "Size", "Rate")):
            self.aud_tree.heading(c, text=t)
            self.aud_tree.column(c, width=100)
        self.aud_tree.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

    def audio_open(self):
        sdt = filedialog.askopenfilename(filetypes=[("SDT", "*.sdt")])
        if not sdt:
            return
        raw = os.path.splitext(sdt)[0] + ".raw"
        if not os.path.isfile(raw):
            raw = filedialog.askopenfilename(title="Plik RAW", filetypes=[("RAW", "*.raw")])
            if not raw:
                return
        def work():
            return SdtArchive(sdt, raw).load()
        def done(arch):
            self.sdt = arch
            for i in self.aud_tree.get_children():
                self.aud_tree.delete(i)
            for e in arch.entries:
                self.aud_tree.insert("", tk.END, values=(e.index, e.offset, e.size, e.rate))
            self.status.set(f"SDT: {len(arch.entries)} próbek")
            self.nb.select(self.t_aud)
        self._thread(work, done)

    def audio_wav_sel(self):
        if not self.sdt:
            return
        sel = self.aud_tree.selection()
        if not sel:
            return
        folder = filedialog.askdirectory()
        if not folder:
            return
        for item in sel:
            idx = int(self.aud_tree.item(item, "values")[0])
            e = self.sdt.entries[idx]
            try:
                self.sdt.to_wav(e, os.path.join(folder, f"sfx_{idx:04d}.wav"))
            except Exception as ex:
                messagebox.showerror("WAV", str(ex))
        self.status.set("Eksport WAV zakończony")

    def audio_wav_all(self):
        if not self.sdt:
            return
        folder = filedialog.askdirectory()
        if not folder:
            return
        def work():
            for e in self.sdt.entries:
                self.sdt.to_wav(e, os.path.join(folder, f"sfx_{e.index:04d}.wav"))
            return len(self.sdt.entries)
        self._thread(work, lambda n: self.status.set(f"Wyeksportowano {n} WAV"))

    # ==================== Inspector ====================
    def _ui_ins(self):
        top = ttk.Frame(self.t_ins, padding=4)
        top.pack(fill=tk.X)
        ttk.Button(top, text="Otwórz DFF / TXD / COL", command=self.ins_open).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="Eksportuj surowe", command=self.ins_export).pack(side=tk.LEFT, padx=2)

        paned = ttk.Panedwindow(self.t_ins, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.ins_tree = ttk.Treeview(paned, show="tree")
        paned.add(self.ins_tree, weight=1)
        self.ins_txt = scrolledtext.ScrolledText(paned, font=self.mono, state=tk.DISABLED)
        paned.add(self.ins_txt, weight=2)
        self.ins_raw = None

    def ins_open(self):
        path = filedialog.askopenfilename(filetypes=[("Asset", "*.dff *.txd *.col"), ("Wszystkie", "*.*")])
        if not path:
            return
        ext = os.path.splitext(path)[1].lower()
        def work():
            if ext == ".txd":
                return "txd", TxdParser(path).parse()
            if ext == ".dff":
                return "dff", DffParser(path).parse()
            if ext == ".col":
                return "col", ColParser(path).parse()
            raise ValueError("Nieobsługiwany format")
        def done(res):
            kind, p = res
            for i in self.ins_tree.get_children():
                self.ins_tree.delete(i)
            self.ins_txt.configure(state=tk.NORMAL)
            self.ins_txt.delete("1.0", tk.END)
            if kind == "txd":
                self.ins_raw = p.raw
                root = self.ins_tree.insert("", tk.END, text=f"TXD  RW {p.rw_ver}", open=True)
                for n in p.textures:
                    self.ins_tree.insert(root, tk.END, text=n)
                self.ins_txt.insert("1.0", f"Tekstur: {len(p.textures)}\nWersja RW: {p.rw_ver}\n")
            elif kind == "dff":
                self.ins_raw = p.raw
                root = self.ins_tree.insert("", tk.END, text=f"DFF  RW {p.rw_ver}", open=True)
                self.ins_tree.insert(root, tk.END, text=f"Frames: {p.frames}")
                self.ins_tree.insert(root, tk.END, text=f"Geometries: {p.geoms}")
                self.ins_tree.insert(root, tk.END, text=f"Atomics: {p.atomics}")
                self.ins_tree.insert(root, tk.END, text=f"Vertices: {p.verts}")
                self.ins_tree.insert(root, tk.END, text=f"Triangles: {p.tris}")
                self.ins_txt.insert("1.0", f"Frames={p.frames} Geoms={p.geoms}\nVerts={p.verts} Tris={p.tris}\n")
            elif kind == "col":
                self.ins_raw = p.raw
                root = self.ins_tree.insert("", tk.END, text=f"COL ({len(p.models)} modeli)", open=True)
                for m in p.models:
                    self.ins_tree.insert(root, tk.END, text=f"{m['name']} id={m['id']} {m['ver']}")
                self.ins_txt.insert("1.0", f"Modele: {len(p.models)}\n")
            self.ins_txt.configure(state=tk.DISABLED)
            self.status.set(f"Zbadano: {path}")
            self.nb.select(self.t_ins)
        self._thread(work, done)

    def ins_export(self):
        if not self.ins_raw:
            return
        path = filedialog.asksaveasfilename(defaultextension=".bin")
        if path:
            with open(path, "wb") as f:
                f.write(self.ins_raw)
            self.status.set(f"Surowe dane zapisane: {path}")

    # ==================== SCM ====================
    def _ui_scm(self):
        top = ttk.Frame(self.t_scm, padding=4)
        top.pack(fill=tk.X)
        ttk.Button(top, text="Otwórz MAIN.SCM", command=self.scm_open).pack(side=tk.LEFT, padx=2)
        self.scm_txt = scrolledtext.ScrolledText(self.t_scm, font=self.mono, wrap=tk.NONE)
        self.scm_txt.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

    def scm_open(self):
        path = filedialog.askopenfilename(filetypes=[("SCM", "*.scm"), ("Wszystkie", "*.*")])
        if not path:
            return
        def work():
            return ScmViewer(path).load()
        def done(v):
            self.scm = v
            self.scm_txt.delete("1.0", tk.END)
            self.scm_txt.insert("1.0", "\n".join(v.lines[:5000]))
            if len(v.lines) > 5000:
                self.scm_txt.insert(tk.END, f"\n… i {len(v.lines) - 5000} kolejnych linii")
            self.status.set(f"SCM: {len(v.lines)} opcode'ów (podgląd)")
            self.nb.select(self.t_scm)
        self._thread(work, done)

    # ==================== wspólne ====================
    def _save(self):
        tab = self.nb.index(self.nb.select())
        if tab == 0:
            self.img_rebuild()
        elif tab == 1:
            self.gxt_save()
        elif tab == 2:
            self.map_save()

    def _focus_search(self):
        tab = self.nb.index(self.nb.select())
        if tab == 0:
            self.img_filt_e.focus_set()
        elif tab == 1:
            self.gxt_search_e.focus_set()

    def _close(self):
        if (self.img and self.img.dirty) or (self.gxt and self.gxt.dirty):
            if not messagebox.askyesno("Niezapisane", "Są niezapisane zmiany. Wyjść?"):
                return
        self.root.destroy()


def main():
    root = tk.Tk()
    GtaModToolkitApp(root)
    root.mainloop()
    return 0

if __name__ == "__main__":
    sys.exit(main())