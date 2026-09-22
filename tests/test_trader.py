"""Tests for the trade manager: exact argv construction, one-trade-at-a-time
locking, the dry-run lifecycle, cancellation, filler validity and the guarantee
that the selected file is never modified.

No real Switch and no live subprocess anywhere in this suite (dry-run only).
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
from dataclasses import replace
from pathlib import Path

import pytest

import frlgtrade
from frlgsim import config as frlg_config
from frlgsim import mon as monmod

from webapp import filler
from webapp.config import TradeConfig
from webapp.library import Library
from webapp.trader import TradeBusyError, TradeManager, TradeNotAllowedError, build_argv

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "frlgtrade.py"


def _trade_cfg(**overrides) -> TradeConfig:
    kwargs = dict(phy="phy1", keys="~/.switch/prod.keys", python=sys.executable,
                  dry_run=True)
    kwargs.update(overrides)
    return TradeConfig(**kwargs)


# ---- trade command construction ---------------------------------------------------
def test_build_argv_exact_shape(web_config):
    argv = build_argv(_trade_cfg(), SCRIPT,
                      Path("/lib/pokemon.pk3"), Path("/tmp/filler.pk3"),
                      Path("/received/out.pk3"))
    assert argv[0] == sys.executable                       # no shell, no sudo
    assert argv[1] == "-u"                                 # UNBUFFERED child output
    assert argv[2] == str(SCRIPT)
    assert argv[3:6] == ["--live", "--phy", "phy1"]
    assert "--keys" in argv
    assert argv[argv.index("--slot") + 1] == "0"           # selected mon in slot 0
    assert argv[argv.index("--trades") + 1] == "1"
    assert argv[argv.index("-o") + 1] == "/received/out.pk3"
    assert argv[argv.index("--out-size") + 1] == "100"
    assert argv[argv.index("--out-format") + 1] == "pk3"
    assert argv[-2:] == ["/lib/pokemon.pk3", "/tmp/filler.pk3"]  # selected first
    assert "|" not in argv and ";" not in argv             # never shell syntax


def test_build_argv_optional_flags(web_config):
    argv = build_argv(_trade_cfg(password="AABB", comm_id="1122", verbose=False),
                      SCRIPT, Path("/a.pk3"), Path("/f.pk3"), Path("/o.pk3"))
    assert argv[argv.index("--password") + 1] == "AABB"
    assert argv[argv.index("--comm-id") + 1] == "1122"
    assert "--verbose" not in argv


def test_build_argv_prefix(web_config):
    argv = build_argv(_trade_cfg(prefix=("sudo", "-E")), SCRIPT,
                      Path("/a.pk3"), Path("/f.pk3"), Path("/o.pk3"))
    assert argv[:2] == ["sudo", "-E"]


def test_build_argv_keys_path_expanded(web_config):
    argv = build_argv(_trade_cfg(keys="~/keys/prod.keys"), SCRIPT,
                      Path("/a.pk3"), Path("/f.pk3"), Path("/o.pk3"))
    assert argv[argv.index("--keys") + 1] == str(Path("~/keys/prod.keys").expanduser())


def test_build_argv_parses_with_existing_cli(web_config):
    """The generated argv must satisfy the REAL frlgtrade.py argument parser."""
    argv = build_argv(_trade_cfg(), SCRIPT, Path("/a.pk3"), Path("/f.pk3"),
                      Path("/o.pk3"))
    args = frlgtrade.build_parser().parse_args(argv[3:])   # skip python -u script
    run_config = frlgtrade._build_run_config(frlgtrade.build_parser(), args)
    assert run_config.plan.trade_slot == 0
    assert run_config.plan.trades == 1
    assert tuple(run_config.plan.party_paths) == ("/a.pk3", "/f.pk3")
    assert run_config.plan.offered_slots is None        # resolved = [0]
    assert run_config.ldn.phy == "phy1"
    assert run_config.role.live is True


def test_single_mon_party_and_slot_zero_semantics():
    """Document the CLI semantics the webapp relies on:
    - a 1-file party with --slot 0 is structurally VALID for the CLI;
    - the webapp still sends a 2-mon party because the real host refuses a
      trainer's LAST alive Pokémon (CheckValidityOfTradeMons, trade.c)."""
    plan = frlg_config.TradePlan(party_paths=("only.pk3",), trade_slot=0, trades=1)
    assert plan.trade_slot == 0
    plan2 = frlg_config.TradePlan(party_paths=("sel.pk3", "filler.pk3"),
                                  trade_slot=0, trades=1)
    assert plan2.trade_slot == 0
    from frlgsim.trade import resolve_offered_slots
    assert resolve_offered_slots(None, 0, 1, party_size=2) == [0]
    assert resolve_offered_slots(None, 0, 1, party_size=1) == [0]


# ---- filler ------------------------------------------------------------------------
def test_filler_is_valid_and_tradeable(make_pk3):
    data = filler.build_filler_mon()
    assert len(data) == 100
    mon = monmod.Mon.from_pk3(data)
    assert mon.checksum_ok
    assert mon.species == filler.FILLER_SPECIES
    assert mon.checksum_ok and mon.decode()["checksum_ok"]
    assert filler.build_filler_mon() != filler.build_filler_mon()  # fresh pid each


def test_filler_has_valid_party_tail():
    mon = monmod.Mon.from_pk3(filler.build_filler_mon())
    raw = mon.party_bytes()
    assert raw[84] == filler.FILLER_LEVEL          # level byte
    assert int.from_bytes(raw[86:88], "little") > 0  # max HP


# ---- manager lifecycle (dry-run) ----------------------------------------------------
def _manager(web_config, meta):
    lib = Library(web_config.library.path, meta)
    lib.scan()
    return TradeManager(web_config, lib, meta)


def test_dry_run_lifecycle(web_config, meta, make_pk3, add_file):
    data = make_pk3(species=25)
    _, entry_id = add_file("pikachu.pk3", data)
    manager = _manager(web_config, meta)
    result = asyncio.run(_run_and_wait(manager, entry_id))
    assert result["state"] == "COMPLETED"
    assert manager.state == "COMPLETED"
    received = web_config.library.received_path
    files = list(received.glob("*.pk3"))
    assert len(files) == 1
    assert files[0].read_bytes() != data           # a different mon came back
    assert len(manager.history) == 1
    record = manager.history[0]
    assert record["dry_run"] is True
    assert record["sha256"] == hashlib.sha256(files[0].read_bytes()).hexdigest()
    # the selected library file is untouched
    assert (web_config.library.path / "pikachu.pk3").read_bytes() == data


async def _run_and_wait(manager, entry_id):
    await manager.start(entry_id)
    task = manager._trade_task
    assert task is not None
    await task
    return manager.state_payload()


def test_one_trade_at_a_time(web_config, meta, make_pk3, add_file):
    _, id_a = add_file("a.pk3", make_pk3(species=25))
    _, id_b = add_file("b.pk3", make_pk3(species=6, pid=0xDEAD0001))
    manager = _manager(web_config, meta)
    errors = []

    async def scenario():
        await manager.start(id_a)
        with pytest.raises(TradeBusyError):
            await manager.start(id_b)             # second trade while running
        await manager._trade_task
    asyncio.run(scenario())
    assert manager.state == "COMPLETED"


def test_trade_unknown_entry(web_config, meta, make_pk3):
    manager = _manager(web_config, meta)
    with pytest.raises(KeyError):
        asyncio.run(manager.start("a" * 64))


def test_trade_rejects_invalid_instance(web_config, meta, make_pk3, add_file):
    data = bytearray(make_pk3(species=25))
    data[40] ^= 0xFF
    _, entry_id = add_file("bad.pk3", bytes(data))
    manager = _manager(web_config, meta)
    with pytest.raises(TradeNotAllowedError):
        asyncio.run(manager.start(entry_id))
    assert manager.state == "IDLE"


def test_cancel_during_dry_run(web_config, meta, make_pk3, add_file):
    _, entry_id = add_file("a.pk3", make_pk3(species=25))
    manager = _manager(web_config, meta)

    async def scenario():
        await manager.start(entry_id)
        assert manager.active
        payload = await manager.cancel()
        assert payload["state"] == "CANCELLED"
        await manager._trade_task
    asyncio.run(scenario())
    assert manager.state == "CANCELLED"
    assert manager.last_result is None
    assert not list(web_config.library.received_path.glob("*.pk3"))


def test_received_filenames_never_collide(web_config, meta, make_pk3, add_file):
    _, id_a = add_file("a.pk3", make_pk3(species=25))
    _, id_b = add_file("b.pk3", make_pk3(species=6, pid=0xDEAD0001))
    manager = _manager(web_config, meta)

    async def scenario():
        for entry_id in (id_a, id_b):
            await manager.start(entry_id)
            await manager._trade_task
    asyncio.run(scenario())
    files = sorted(web_config.library.received_path.glob("*.pk3"))
    assert len(files) == 2 and files[0].name != files[1].name


def test_add_received_to_library(web_config, meta, make_pk3, add_file):
    _, entry_id = add_file("a.pk3", make_pk3(species=25))
    manager = _manager(web_config, meta)
    asyncio.run(_run_and_wait(manager, entry_id))
    record_id = manager.history[0]["id"]
    out = manager.add_to_library(record_id)
    assert "from_trade" in out["relpath"]
    assert (web_config.library.path / out["relpath"]).is_file()
    assert manager.library.counts_by_national()           # fresh index includes it


def test_bus_fanout_drops_for_slow_subscribers():
    from webapp.trader import EventBus
    bus = EventBus(maxsize=2)
    q = bus.subscribe()
    for i in range(5):
        bus.publish({"n": i})
    items = [q.get_nowait() for _ in range(q.qsize())]
    assert len(items) == 2 and items == [{"n": 3}, {"n": 4}]


def test_bus_receives_full_trade_event_pipeline(web_config, meta, make_pk3, add_file):
    """A subscriber must see state, log AND received events for one dry-run trade
    (this is exactly what the SSE endpoint forwards to the browser)."""
    _, entry_id = add_file("a.pk3", make_pk3(species=25))
    manager = _manager(web_config, meta)

    async def scenario():
        queue = manager.bus.subscribe()
        await manager.start(entry_id)
        await manager._trade_task
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        manager.bus.unsubscribe(queue)
        kinds = {e["type"] for e in events}
        assert {"state", "log", "received"} <= kinds, kinds
        received = next(e for e in events if e["type"] == "received")
        assert received["received"]["decodable"] is True
        milestones = [e.get("milestone") for e in events if e["type"] == "log"]
        assert any("Scanning" in (m or "") for m in milestones)
        assert any("Trade confirmed" in (m or "") for m in milestones)
    asyncio.run(scenario())


def test_sse_event_stream_generator(web_config, meta, make_pk3, add_file):
    """The SSE generator itself: initial state frame, then log/state/received
    frames forwarded from the event bus, and unsubscription on close."""
    from webapp.app import event_stream
    _, entry_id = add_file("a.pk3", make_pk3(species=25))
    manager = _manager(web_config, meta)

    class StubRequest:
        def __init__(self):
            self.disconnected = False

        async def is_disconnected(self):
            return self.disconnected

    async def scenario():
        request = StubRequest()
        frames = []
        stream = event_stream(request, manager)
        # first frame: retry + initial state, no trade yet
        frames.append(await stream.__anext__())
        frames.append(await stream.__anext__())
        assert "retry: 2000" in frames[0]
        assert frames[1].startswith("event: state") and '"state": "IDLE"' in frames[1]
        # start a trade; the generator must forward its events
        await manager.start(entry_id)
        seen = []
        while len(seen) < 12:              # log/state frames keep flowing
            frame = await asyncio.wait_for(stream.__anext__(), timeout=5.0)
            if frame.startswith("event: "):
                seen.append(frame.splitlines()[0].removeprefix("event: "))
            if "received" in seen:
                break
        assert "state" in seen and "log" in seen and "received" in seen
        request.disconnected = True
        await stream.aclose()             # runs the finally: unsubscribes
    asyncio.run(scenario())


def test_milestones_match_verbose_lines():
    """The child runs with --verbose, where ConsoleLog.info() is SUPPRESSED - so
    every state-changing milestone must fire off a verbose line that ALWAYS
    prints (the root cause of the state sticking at CONNECTED)."""
    from webapp.trader import MILESTONES
    cases = [
        ("[  1.0s] [live] scanning for FRLG LDN network (nickname=EMU)...", "SCANNING"),
        ("[live] joined LDN; awaiting the host's Pia connection handshake", "JOINING"),
        ("[live] Pia connection ESTABLISHED - host confirmed us", "CONNECTED"),
        ("[live] entry: complete (P0..P5) - trade menu is live; entering the trade FSM.",
         "TRADING"),
        ("-> READY_TO_TRADE cursor=0 (trade 1/1)", "TRADING"),
        ("entry: -> P5_IN_TRADE", "TRADING"),
        ("-> READY_FINISH_TRADE", None),
        ("RECEIVED (trade 1/1): Pikachu (#25) nick='SPARKY' OT='RED' PID=12345678 "
         "lv=50 checksum=OK", None),
        ("all 1 trade(s) committed -> entering cancel-to-leave", None),
        ("-> REQUEST_CANCEL (leaving: CANCEL selected) [trade.c:2049]", None),
    ]
    for line, expected in cases:
        hit = next(((state, label) for pattern, state, label in MILESTONES
                    if pattern.search(line)), (None, None))
        assert hit[0] == expected, (line, hit)


# ---- auto-return to IDLE after FAILED / CANCELLED / COMPLETED -------------------------
def test_unbuffered_child_output_arrives_live(tmp_path):
    """The `-u` fix: a child's print() must reach the reader WHILE it runs, not
    only at exit (the root cause of the state machine sitting on SCANNING)."""
    child = tmp_path / "child.py"
    child.write_text("import time\nprint('MARKER-LINE')\ntime.sleep(30)\n",
                     encoding="utf-8")

    async def scenario():
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-u", str(child),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=2.0)
            assert b"MARKER-LINE" in line        # flushed before the 30s exit
        finally:
            proc.terminate()
            await proc.wait()
    asyncio.run(scenario())


def test_spawn_failure_goes_failed_then_idle(web_config, meta, make_pk3, add_file):
    """A child that cannot even start (missing interpreter) must surface a log
    line, land on FAILED, and auto-reset to IDLE after idle_reset_seconds."""
    cfg = replace(web_config, trade=replace(
        web_config.trade, python="/nonexistent/python", dry_run=False,
        idle_reset_seconds=0.05))
    lib = Library(cfg.library.path, meta)
    lib.scan()
    manager = TradeManager(cfg, lib, meta)
    _, entry_id = add_file("a.pk3", make_pk3(species=25))
    lib.scan()

    async def scenario():
        queue = manager.bus.subscribe()
        await manager.start(entry_id)
        await manager._trade_task
        assert manager.state == "FAILED"
        assert manager.last_result["state"] == "FAILED"
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        manager.bus.unsubscribe(queue)
        log_texts = [e.get("text", "") for e in events if e["type"] == "log"]
        assert any("Trade error" in t for t in log_texts)   # panel shows the reason
        await asyncio.sleep(0.15)                            # idle_reset_seconds=0.05
        assert manager.state == "IDLE"
        assert manager.current is None
    asyncio.run(scenario())


def test_cancel_returns_to_idle(web_config, meta, make_pk3, add_file):
    cfg = replace(web_config, trade=replace(web_config.trade,
                                            idle_reset_seconds=0.05))
    manager = _manager(cfg, meta)
    _, entry_id = add_file("a.pk3", make_pk3(species=25))
    manager.library.scan()

    async def scenario():
        await manager.start(entry_id)
        await manager.cancel()
        await manager._trade_task
        assert manager.state == "CANCELLED"
        await asyncio.sleep(0.15)
        assert manager.state == "IDLE"
    asyncio.run(scenario())


def test_completed_returns_to_idle(web_config, meta, make_pk3, add_file):
    """COMPLETED must also auto-return to IDLE (the received card stays visible)."""
    cfg = replace(web_config, trade=replace(web_config.trade,
                                            idle_reset_seconds=0.05))
    manager = _manager(cfg, meta)
    _, entry_id = add_file("a.pk3", make_pk3(species=25))
    manager.library.scan()

    async def scenario():
        await manager.start(entry_id)
        await manager._trade_task
        assert manager.state == "COMPLETED"
        await asyncio.sleep(0.15)
        assert manager.state == "IDLE"
        assert manager.last_result["state"] == "COMPLETED"   # result card kept
    asyncio.run(scenario())


def test_new_trade_cancels_pending_idle_reset(web_config, meta, make_pk3, add_file):
    """Starting a new trade before the auto-reset fires must cancel the reset, so
    the reset can never clobber the new trade's state."""
    cfg = replace(web_config, trade=replace(web_config.trade,
                                            idle_reset_seconds=0.3))
    manager = _manager(cfg, meta)
    _, id_a = add_file("a.pk3", make_pk3(species=25))
    _, id_b = add_file("b.pk3", make_pk3(species=6, pid=0xDEAD0001))
    manager.library.scan()

    async def scenario():
        await manager.start(id_a)
        await manager.cancel()
        await manager._trade_task
        assert manager.state == "CANCELLED"
        assert manager._idle_reset_task is not None        # reset pending
        await manager.start(id_b)                          # cancels the reset
        assert manager._idle_reset_task is None
        await manager._trade_task
        assert manager.state == "COMPLETED"                # trade 2's own state
        await asyncio.sleep(0.4)                           # trade 2's own reset fires
        assert manager.state == "IDLE"
    asyncio.run(scenario())
