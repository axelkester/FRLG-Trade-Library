"""Live-trade orchestration for the web application.

A small, strict state machine (IDLE -> SCANNING -> JOINING -> CONNECTED -> TRADING
-> COMPLETED/FAILED/CANCELLED) drives ONE trade at a time. The actual trading is
performed by the EXISTING ``frlgtrade.py`` CLI, spawned with
``asyncio.create_subprocess_exec`` (never ``shell=True``, always explicit argv); the
web application only streams its stdout/stderr over SSE and interprets well-known
milestone lines. ``--dry-run`` replaces the subprocess with a scripted simulation so
the whole UI lifecycle works without a Switch.

The live CLI needs a 2-mon party: the selected Pokémon in slot 0 (``--slot 0``) plus
a generated filler in slot 1, because the real game refuses a trainer's LAST alive
Pokémon. The selected file itself is never modified - it is passed straight through
to the CLI.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import signal
import tempfile
from pathlib import Path
from typing import Any

from . import filler as fillermod
from .config import TradeConfig, WebConfig
from .library import Library, LibraryEntry
from .metadata import Gen3Metadata
from .moninfo import decode_entry

log = logging.getLogger("frlgweb.trade")

STATES = ("IDLE", "SCANNING", "JOINING", "CONNECTED", "TRADING",
          "COMPLETED", "FAILED", "CANCELLED")
ACTIVE_STATES = frozenset(("SCANNING", "JOINING", "CONNECTED", "TRADING"))
TERMINAL_STATES = frozenset(("COMPLETED", "FAILED", "CANCELLED"))

HISTORY_FILE = ".history.json"
PID_FILE = ".trade.pid"

# Milestone patterns are anchored to the EXISTING frlgtrade.py log lines (verified
# against frlgtrade.py / frlgsim/trade.py). Matching is case-insensitive. Order
# matters: first match wins.
#
# IMPORTANT: the webapp spawns frlgtrade.py with --verbose, and ConsoleLog.info()
# prints ONLY when NOT verbose - so the lg.info(...) milestone lines are suppressed
# in our runs. Every state-changing milestone therefore also matches a VERBOSE line
# that always prints (the "-> LINKCMD ..." / "entry: -> ..." lines).
MILESTONES: tuple[tuple[re.Pattern[str], str | None, str], ...] = (
    (re.compile(r"scanning for FRLG LDN network", re.IGNORECASE), "SCANNING",
     "Scanning for FRLG network…"),
    (re.compile(r"joined LDN", re.IGNORECASE), "JOINING", "Joining host session…"),
    (re.compile(r"Pia connection ESTABLISHED", re.IGNORECASE), "CONNECTED",
     "Connected — host confirmed us"),
    (re.compile(r"Exchanged player info", re.IGNORECASE), None,
     "Exchanged player info"),
    (re.compile(r"Host entered the trade room", re.IGNORECASE), None,
     "Host entered the trade room"),
    (re.compile(r"Host sat down", re.IGNORECASE), None, "Host sat down"),
    # verbose-only lines that always print under --verbose:
    (re.compile(r"trade menu is live", re.IGNORECASE), "TRADING", "Trade menu open"),
    (re.compile(r"-> READY_TO_TRADE", re.IGNORECASE), "TRADING",
     "Offered Pokémon — waiting on host"),
    (re.compile(r"entry: -> P5_IN_TRADE", re.IGNORECASE), "TRADING",
     "Trade menu open"),
    # the info() lines (fire when --verbose is OFF; harmless duplicates above):
    (re.compile(r"Trade menu open", re.IGNORECASE), "TRADING", "Trade menu open"),
    (re.compile(r"Offered our Pok.mon", re.IGNORECASE), "TRADING",
     "Offered Pokémon — waiting on host"),
    (re.compile(r"-> READY_FINISH_TRADE", re.IGNORECASE), None, "Trading Pokémon…"),
    (re.compile(r"Confirming trade", re.IGNORECASE), None, "Trading Pokémon…"),
    (re.compile(r"RECEIVED \(trade", re.IGNORECASE), None, "Pokémon received!"),
    (re.compile(r"Trade confirmed", re.IGNORECASE), None, "Trade confirmed"),
    (re.compile(r"all \d+ trade\(s\) committed", re.IGNORECASE), None,
     "Trade complete — leaving the menu"),
    (re.compile(r"Received:", re.IGNORECASE), None, "Pokémon received!"),
    (re.compile(r"-> REQUEST_CANCEL|-> READY_CANCEL_TRADE", re.IGNORECASE), None,
     "Cancelling trade menu…"),
    (re.compile(r"Cancelling the trade menu", re.IGNORECASE), None,
     "Cancelling trade menu…"),
    (re.compile(r"Closing the link", re.IGNORECASE), None, "Closing the link…"),
)


class TradeBusyError(RuntimeError):
    """A trade is already running."""


class TradeNotAllowedError(RuntimeError):
    """The selected instance is not tradeable."""


class EventBus:
    """Fan-out of JSON-safe events to any number of SSE subscribers."""

    def __init__(self, maxsize: int = 256):
        self._queues: set[asyncio.Queue[dict[str, Any]]] = set()
        self._maxsize = maxsize

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._maxsize)
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._queues.discard(queue)

    def publish(self, event: dict[str, Any]) -> None:
        for queue in list(self._queues):
            if queue.full():  # drop the oldest for slow clients
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)


def build_argv(cfg: TradeConfig, script: Path, selected_path: Path,
               filler_path: Path, out_path: Path) -> list[str]:
    """The exact argv for one live trade. Pure function - heavily unit-tested.

    Single Pokémon in slot 0, filler in slot 1, one trade, received mon written as
    a 100-byte decrypted .pk3 to the (already collision-safe) output path.

    ``-u`` forces UNBUFFERED child output: frlgtrade.py reports via print(), and
    with a piped stdout Python would otherwise block-buffer it (~8KB) - the trade
    log panel would stay empty and the state machine would sit on SCANNING until
    the buffer flushed or the process exited."""
    argv: list[str] = list(cfg.prefix)
    argv += [
        cfg.python, "-u", str(script),
        "--live", "--phy", cfg.phy,
        "--keys", str(Path(cfg.keys).expanduser()),
        "--slot", "0", "--trades", "1",
        "-o", str(out_path),
        "--out-size", "100", "--out-format", "pk3",
        "--ot", cfg.ot, "--version", cfg.version,
    ]
    if cfg.password:
        argv += ["--password", cfg.password]
    if cfg.comm_id:
        argv += ["--comm-id", cfg.comm_id]
    if cfg.verbose:
        argv.append("--verbose")
    argv += [str(selected_path), str(filler_path)]
    return argv


_SECRET_FLAGS = ("--keys", "--password", "--comm-id")


def _redact_argv(argv: list[str]) -> list[str]:
    """Mask secret option VALUES for server-side logging (never the CLI itself)."""
    out: list[str] = []
    skip = False
    for token in argv:
        if skip:
            out.append("***")
            skip = False
            continue
        out.append(token)
        if token in _SECRET_FLAGS:
            skip = True
    return out


def _safe_filename(text: str, fallback: str = "mon") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("._") or fallback
    return cleaned[:32]


class TradeManager:
    def __init__(self, cfg: WebConfig, library: Library, meta: Gen3Metadata):
        self.cfg = cfg
        self.library = library
        self.meta = meta
        self.received_dir = Path(cfg.library.received_path)
        self.received_dir.mkdir(parents=True, exist_ok=True)
        self.bus = EventBus()
        self._lock = asyncio.Lock()
        self._proc: asyncio.subprocess.Process | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._trade_task: asyncio.Task[None] | None = None
        self._idle_reset_task: asyncio.Task[None] | None = None
        self._cancel_requested = False
        self.state: str = "IDLE"
        self.current: dict[str, Any] | None = None
        self.last_result: dict[str, Any] | None = None
        self.history: list[dict[str, Any]] = self._load_history()
        self._pidfile = self.received_dir / PID_FILE
        self._cleanup_stale_pidfile()

    # ---- public API ---------------------------------------------------------
    @property
    def active(self) -> bool:
        return self.state in ACTIVE_STATES

    def state_payload(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "active": self.active,
            "dry_run": bool(self.cfg.trade.dry_run),
            "phy": self.cfg.trade.phy,
            "current": self.current,
            "last_result": self.last_result,
        }

    async def start(self, entry_id: str) -> dict[str, Any]:
        async with self._lock:
            if self.state in ACTIVE_STATES:
                raise TradeBusyError(f"a trade is already running ({self.state})")
            # wait out a previous run's teardown before starting a new one
            if self._trade_task is not None and not self._trade_task.done():
                await self._trade_task
            entry = self.library.get(entry_id)
            if entry is None:
                raise KeyError(entry_id)
            if not entry.tradeable:
                raise TradeNotAllowedError(
                    f"instance {entry_id} is not tradeable "
                    f"(status={entry.status})")
            out_path = self._received_path_for(entry)
            self._cancel_requested = False
            self._cancel_idle_reset()          # a new trade cancels any pending reset
            self.state = "SCANNING"
            self.last_result = None
            self.current = {
                "entry_id": entry.id,
                "species_national": entry.summary.get("species_national"),
                "species_name": entry.summary.get("species_name"),
                "nickname": entry.summary.get("nickname"),
                "level": entry.summary.get("level"),
                "shiny": entry.summary.get("shiny"),
                "started": dt.datetime.now().isoformat(timespec="seconds"),
                "out_path": str(out_path),
            }
            self._trade_task = asyncio.create_task(self._run(entry, out_path))
            self._tasks.add(self._trade_task)
            self._trade_task.add_done_callback(self._tasks.discard)
            self._publish({"type": "state", **self.state_payload()})
            return self.state_payload()

    async def cancel(self) -> dict[str, Any]:
        """Gracefully stop the running trade (SIGINT -> SIGTERM -> SIGKILL)."""
        if self.state not in ACTIVE_STATES:
            return self.state_payload()
        self._cancel_requested = True
        if self.state != "CANCELLED":
            self.state = "CANCELLED"
        self._log_line("Cancel requested — stopping frlgtrade…")
        proc = self._proc
        if proc is not None:
            await self._terminate(proc)
        self._publish({"type": "state", **self.state_payload()})
        return self.state_payload()

    async def shutdown(self) -> None:
        """Kill any running child and stop the reader/watcher tasks."""
        self._cancel_requested = True
        if self._proc is not None:
            await self._terminate(self._proc)
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - teardown
                pass

    # ---- run loop ------------------------------------------------------------
    async def _run(self, entry: LibraryEntry, out_path: Path) -> None:
        workdir = Path(tempfile.mkdtemp(prefix="frlgweb-trade-"))
        try:
            filler_path = workdir / "filler.pk3"
            fillermod.save_filler(str(filler_path))
            if self.cfg.trade.dry_run:
                await self._run_dry(entry, out_path)
            else:
                await self._run_live(entry, filler_path, out_path)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - report, never take the app down
            log.exception("trade run failed")
            if self.state in ACTIVE_STATES:
                self.state = "FAILED"
            self.last_result = {"state": "FAILED", "reason": str(exc)}
            # Surface the reason in the trade log panel too (e.g. a missing
            # interpreter / spawn failure would otherwise leave the panel empty).
            self._log_line(f"Trade error: {exc}")
            self._publish({"type": "state", **self.state_payload()})
        finally:
            self._proc = None
            shutil.rmtree(workdir, ignore_errors=True)
            try:
                self._pidfile.unlink()
            except OSError:
                pass
            if self.state in ("FAILED", "CANCELLED", "COMPLETED"):
                self._schedule_idle_reset()

    async def _run_live(self, entry: LibraryEntry, filler_path: Path,
                        out_path: Path) -> None:
        argv = build_argv(self.cfg.trade, self.cfg.frlgtrade_script,
                          entry.path, filler_path, out_path)
        log.info("spawning frlgtrade: %s", " ".join(_redact_argv(argv)))
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(self.cfg.repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        self._proc = proc
        self._write_pidfile(proc.pid)
        reader = asyncio.create_task(self._read_stream(proc))
        self._tasks.add(reader)
        reader.add_done_callback(self._tasks.discard)
        timed_out = False
        try:
            try:
                code = await asyncio.wait_for(proc.wait(),
                                              timeout=self.cfg.trade.timeout)
            except asyncio.TimeoutError:
                timed_out = True
                self._log_line(f"Trade timed out after {self.cfg.trade.timeout}s "
                               "— stopping frlgtrade…")
                await self._terminate(proc)
                code = await proc.wait()
            except asyncio.CancelledError:
                await self._terminate(proc)
                raise
        finally:
            await asyncio.gather(reader, return_exceptions=True)
        self._finish(entry, out_path, code=code, timed_out=timed_out)

    async def _run_dry(self, entry: LibraryEntry, out_path: Path) -> None:
        """Scripted simulation: same states, same SSE events, no Switch."""
        scale = self.cfg.trade.dry_run_scale
        steps = (
            (1.0 * scale, "SCANNING", "Scanning for FRLG LDN network…"),
            (1.5 * scale, "JOINING", "Joined LDN; awaiting the host handshake…"),
            (1.2 * scale, "CONNECTED", "Pia connection ESTABLISHED — host confirmed us"),
            (2.0 * scale, "TRADING", "Trade menu open; offered Pokémon…"),
            (1.5 * scale, None, "Trade confirmed — Pokémon received!"),
        )
        for delay, new_state, label in steps:
            await asyncio.sleep(delay)
            if self._cancel_requested:
                self.state = "CANCELLED"
                self._publish({"type": "state", **self.state_payload()})
                return
            if new_state is not None and not self._regress(new_state):
                self.state = new_state
            # _log_line publishes the log frame AND the state frame when the
            # milestone maps to a state transition.
            self._log_line(f"[dry-run] {label}")
        # Fabricate the "received" Pokémon and persist it exactly like a live trade.
        internal = self.meta.national_to_internal[
            secrets.choice(list(self.meta.national_to_internal))]
        received = fillermod.build_mon(
            internal, level=secrets.randbelow(49) + 2,
            nickname="TRADED", ot_name=self.cfg.trade.ot)
        out_path.write_bytes(received)
        self._finish(entry, out_path, code=0, timed_out=False)

    # ---- finishing -----------------------------------------------------------
    def _finish(self, entry: LibraryEntry, out_path: Path, *, code: int,
                timed_out: bool) -> None:
        if self._cancel_requested or self.state == "CANCELLED":
            self.state = "CANCELLED"
            self._publish({"type": "state", **self.state_payload()})
            return
        received_summary: dict[str, Any] | None = None
        if code == 0 and out_path.is_file():
            try:
                received_summary = decode_entry(out_path.read_bytes(), self.meta)
            except OSError as exc:  # pragma: no cover - read failure
                log.warning("cannot read received file: %s", exc)
        if code == 0 and received_summary is not None:
            self.state = "COMPLETED"
            record = self._record_received(out_path, received_summary)
            self.last_result = {
                "state": "COMPLETED",
                "received": received_summary,
                "record_id": record["id"],
                "file": record["file"],
            }
            self.current = None
            self._publish({"type": "received", **self.last_result,
                           "state": self.state})
            if self.cfg.library.auto_add_received:
                self._add_to_library(record)
        else:
            self.state = "FAILED"
            reason = ("timed out" if timed_out else
                      f"frlgtrade exited with code {code}"
                      if code != 0 else
                      "no Pokémon received")
            self.last_result = {"state": "FAILED", "reason": reason,
                                "exit_code": code}
            log.warning("trade failed: %s", reason)
            self.current = None
        self._publish({"type": "state", **self.state_payload()})

    # ---- helpers -------------------------------------------------------------
    def _regress(self, new_state: str) -> bool:
        """True when `new_state` would move the state machine backwards."""
        return STATES.index(new_state) <= STATES.index(self.state)

    def _schedule_idle_reset(self) -> None:
        """Auto-return FAILED/CANCELLED/COMPLETED to IDLE after
        [trade].idle_reset_seconds, so a finished trade never leaves the UI stuck
        on a terminal state. 0 = off."""
        delay = self.cfg.trade.idle_reset_seconds
        if delay <= 0 or self._idle_reset_task is not None:
            return

        async def reset_after_delay() -> None:
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise
            finally:
                self._idle_reset_task = None     # the slot is free again either way
            if self.state in ("FAILED", "CANCELLED", "COMPLETED") \
                    and not self.active:
                log.info("auto-resetting trade state %s -> IDLE", self.state)
                self.state = "IDLE"
                self.current = None
                self._publish({"type": "state", **self.state_payload()})

        self._idle_reset_task = asyncio.create_task(reset_after_delay())
        self._tasks.add(self._idle_reset_task)
        self._idle_reset_task.add_done_callback(self._tasks.discard)

    def _cancel_idle_reset(self) -> None:
        if self._idle_reset_task is not None:
            self._idle_reset_task.cancel()
            self._idle_reset_task = None

    def _received_path_for(self, entry: LibraryEntry) -> Path:
        """A collision-safe file name inside received/ (never overwrite)."""
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        species = _safe_filename(entry.summary.get("species_name", "mon"),
                                 f"sp{entry.summary.get('species_national', '0')}")
        base = f"{stamp}_{species}_{entry.id[:8]}"
        candidate = self.received_dir / f"{base}.pk3"
        counter = 1
        while candidate.exists():
            candidate = self.received_dir / f"{base}_{counter}.pk3"
            counter += 1
        return candidate

    def _record_received(self, out_path: Path, summary: dict[str, Any]) -> dict[str, Any]:
        data = out_path.read_bytes()
        record = {
            "id": hashlib.sha256(data).hexdigest()[:16],
            "ts": dt.datetime.now().isoformat(timespec="seconds"),
            "file": out_path.relative_to(self.received_dir).as_posix(),
            "sha256": hashlib.sha256(data).hexdigest(),
            "species_national": summary.get("species_national"),
            "species_name": summary.get("species_name"),
            "nickname": summary.get("nickname"),
            "level": summary.get("level"),
            "shiny": summary.get("shiny"),
            "dry_run": bool(self.cfg.trade.dry_run),
            "summary": summary,
        }
        self.history.append(record)
        self._save_history()
        return record

    def add_to_library(self, record_id: str) -> dict[str, Any]:
        """Copy a received Pokémon into the library (never the other way)."""
        record = next((r for r in self.history if r.get("id") == record_id), None)
        if record is None:
            raise KeyError(record_id)
        return self._add_to_library(record)

    def _add_to_library(self, record: dict[str, Any]) -> dict[str, Any]:
        source = (self.received_dir / record["file"]).resolve()
        root = self.library.root.resolve()
        if not source.is_relative_to(root) and not source.is_file():
            raise ValueError("received file missing")
        target_dir = root / "from_trade"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{record['id']}_{record['file']}"
        if not target.exists():
            shutil.copyfile(source, target)
        self.library.scan()  # pick the new file up immediately
        return {"record_id": record["id"], "relpath": target.relative_to(root).as_posix()}

    # ---- history persistence --------------------------------------------------
    def _load_history(self) -> list[dict[str, Any]]:
        path = self.received_dir / HISTORY_FILE
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _save_history(self) -> None:
        path = self.received_dir / HISTORY_FILE
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.history, indent=1), encoding="utf-8")
        tmp.replace(path)

    # ---- subprocess plumbing --------------------------------------------------
    def _publish(self, event: dict[str, Any]) -> None:
        self.bus.publish(event)

    async def _read_stream(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            self._log_line(line.decode("utf-8", "replace").rstrip())

    def _log_line(self, text: str) -> None:
        log.info("frlgtrade: %s", text)
        milestone = None
        new_state = None
        for pattern, state, label in MILESTONES:
            if pattern.search(text):
                new_state, milestone = state, label
                break
        if new_state is not None and not self._regress(new_state):
            self.state = new_state
        event: dict[str, Any] = {"type": "log", "text": text}
        if milestone:
            event["milestone"] = milestone
        self._publish(event)
        if new_state is not None:
            self._publish({"type": "state", **self.state_payload()})

    async def _terminate(self, proc: asyncio.subprocess.Process) -> None:
        """SIGINT (graceful leave) -> SIGTERM -> SIGKILL against the process group."""
        if proc.returncode is not None:
            return
        for sig, grace in ((signal.SIGINT, 15.0), (signal.SIGTERM, 5.0),
                           (signal.SIGKILL, None)):
            try:
                os.killpg(os.getpgid(proc.pid), sig)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            if grace is None:
                await proc.wait()
                return
            try:
                await asyncio.wait_for(proc.wait(), timeout=grace)
                return
            except asyncio.TimeoutError:
                continue

    # ---- pidfile / orphan cleanup --------------------------------------------
    def _write_pidfile(self, pid: int) -> None:
        try:
            self._pidfile.write_text(str(pid), encoding="utf-8")
        except OSError:  # pragma: no cover - non-fatal
            pass

    def _cleanup_stale_pidfile(self) -> None:
        """Reap an frlgtrade.py left behind by a hard-killed web app."""
        try:
            pid = int(self._pidfile.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return
        if pid == os.getpid() or not self._pid_is_frlgtrade(pid):
            return
        log.warning("terminating orphaned frlgtrade pid %s", pid)
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            self._pidfile.unlink()
        except OSError:
            pass

    @staticmethod
    def _pid_is_frlgtrade(pid: int) -> bool:
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            return False
        return b"frlgtrade.py" in cmdline
