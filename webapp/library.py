"""Recursive .pk3/.ek3 library indexer.

Scans the configured library root for ``*.pk3`` and ``*.ek3`` files (case-
insensitive), assigns every file a stable internal id = the SHA-256 of its bytes,
and classifies each file. Identical byte content is de-duplicated (same Pokémon
instance = same id). Invalid files never crash the app - they are marked
"invalid_size", "checksum_failure" or "undecodable" and shown disabled in the UI.

The browser NEVER supplies filesystem paths: it only ever sends an indexed id.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .metadata import Gen3Metadata
from .moninfo import TRADEABLE_STATUSES, decode_entry

log = logging.getLogger("frlgweb.library")

ID_RE = re.compile(r"^[0-9a-f]{64}$")
INDEX_EXTENSIONS = (".pk3", ".ek3")

# "ok" entries are tradeable; everything else is marked with one of these.
INVALID_SIZE = "invalid_size"
CHECKSUM_FAILURE = "checksum_failure"
UNDECODABLE = "undecodable"
UNREADABLE = "unreadable"


@dataclass
class LibraryEntry:
    id: str                      # sha256 hex of the file bytes
    path: Path                   # absolute path inside the library root
    relpath: str                 # path relative to the library root
    size: int
    mtime: float
    summary: dict[str, Any]      # the decode_entry() display dict (or error dict)

    @property
    def tradeable(self) -> bool:
        return self.summary.get("status") in TRADEABLE_STATUSES

    @property
    def status(self) -> str:
        return self.summary.get("status", self.summary.get("error", "unknown"))

    def to_card(self) -> dict[str, Any]:
        """The per-instance card shown in a species detail view."""
        s = self.summary
        return {
            "id": self.id,
            "relpath": self.relpath,
            "format": s.get("format", "?"),
            "variant": s.get("variant", "?"),
            "size": self.size,
            "status": self.status,
            "tradeable": self.tradeable,
            "error": s.get("error"),
            "species_national": s.get("species_national"),
            "species_name": s.get("species_name"),
            "nickname": s.get("nickname"),
            "ot_name": s.get("ot_name"),
            "level": s.get("level"),
            "shiny": s.get("shiny"),
            "checksum_ok": s.get("checksum_ok"),
            "mtime": self.mtime,
        }


@dataclass
class LibraryStats:
    files: int = 0
    entries: int = 0             # unique content hashes
    valid: int = 0
    invalid: int = 0
    duplicates: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "files": self.files,
            "entries": self.entries,
            "valid": self.valid,
            "invalid": self.invalid,
            "duplicates": self.duplicates,
            "errors": self.errors,
        }


class Library:
    """Thread-safe index of every Pokémon file under the library root."""

    def __init__(self, root: Path, meta: Gen3Metadata):
        self.root = Path(root)
        self.meta = meta
        self._lock = threading.RLock()
        self._entries: dict[str, LibraryEntry] = {}
        self._stats = LibraryStats()

    # ---- indexing -----------------------------------------------------------
    def scan(self) -> LibraryStats:
        """Full rescan of the library root; returns fresh stats."""
        root = self.root
        root.mkdir(parents=True, exist_ok=True)
        entries: dict[str, LibraryEntry] = {}
        seen_hashes: dict[str, str] = {}
        stats = LibraryStats()
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in INDEX_EXTENSIONS:
                continue
            stats.files += 1
            relpath = path.relative_to(root).as_posix()
            try:
                data = path.read_bytes()
            except OSError as exc:
                stats.invalid += 1
                stats.errors.append({"relpath": relpath, "error": UNREADABLE,
                                     "detail": str(exc)})
                continue
            digest = hashlib.sha256(data).hexdigest()
            if digest in seen_hashes:  # same content = same instance
                stats.duplicates += 1
                log.info("duplicate content %s: %s and %s", digest,
                         seen_hashes[digest], relpath)
                continue
            seen_hashes[digest] = relpath
            summary = decode_entry(data, self.meta)
            if summary.get("status") == "ok":
                stats.valid += 1
            else:
                stats.invalid += 1
                if summary.get("error") == INVALID_SIZE:
                    stats.errors.append({"relpath": relpath, "error": INVALID_SIZE,
                                         "detail": f"{len(data)} bytes"})
                else:
                    stats.errors.append({"relpath": relpath,
                                         "error": summary.get("error", "invalid")})
            entries[digest] = LibraryEntry(
                id=digest, path=path, relpath=relpath, size=len(data),
                mtime=path.stat().st_mtime, summary=summary)
        stats.entries = len(entries)
        with self._lock:
            self._entries = entries
            self._stats = stats
        log.info("library scan: %s files -> %s entries (%s valid, %s invalid, "
                 "%s duplicates)", stats.files, stats.entries, stats.valid,
                 stats.invalid, stats.duplicates)
        return stats

    # ---- lookups ------------------------------------------------------------
    def get(self, entry_id: str) -> LibraryEntry | None:
        """Look up an indexed entry by its content-hash id (never a path)."""
        if not isinstance(entry_id, str) or not ID_RE.match(entry_id):
            return None
        with self._lock:
            return self._entries.get(entry_id)

    def stats(self) -> LibraryStats:
        with self._lock:
            return self._stats

    def counts_by_national(self) -> dict[int, tuple[int, int]]:
        """{national dex number: (instance count, shiny count)}."""
        counts: dict[int, list[int]] = {}
        with self._lock:
            entries = list(self._entries.values())
        for entry in entries:
            national = entry.summary.get("species_national")
            if national is None:
                continue
            pair = counts.setdefault(national, [0, 0])
            pair[0] += 1
            if entry.summary.get("shiny"):
                pair[1] += 1
        return {n: (pair[0], pair[1]) for n, pair in counts.items()}

    def by_national(self, national: int) -> list[LibraryEntry]:
        with self._lock:
            entries = [e for e in self._entries.values()
                       if e.summary.get("species_national") == national]
        return sorted(entries, key=lambda e: (e.mtime, e.relpath))
