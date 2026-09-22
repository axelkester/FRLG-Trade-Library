"""Compatibility tests for the EXISTING CLI and sim (must keep working unchanged).

The live networking implementation is not exercised here (no Switch, no wifi) -
these tests pin the CLI surface the web application relies on: the documented
working invocation, the single-mon/slot-0 semantics, and the offline TradeEngine
construction.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import frlgtrade
from frlgsim import config as configmod
from frlgsim import mon as monmod
from frlgsim import trade
from frlgsim import trade_runtime as runtime

from webapp.filler import build_mon

REPO = Path(__file__).resolve().parent.parent


def test_documented_live_invocation_still_parses():
    """The README's working command must keep parsing exactly as before."""
    argv = ["--live", "--phy", "phy1", "-o", "output.pk3",
            "PARTY1.pk3", "PARTY2.pk3", "PARTY3.pk3"]
    ap = frlgtrade.build_parser()
    args = ap.parse_args(argv)
    run_config = frlgtrade._build_run_config(ap, args)
    assert run_config.role.live is True
    assert run_config.ldn.phy == "phy1"
    assert len(run_config.plan.party_paths) == 3
    assert run_config.plan.trade_slot == 1           # the README's 2nd party member
    assert run_config.plan.trades == 1
    assert run_config.plan.output_path == "output.pk3"


def test_default_offered_slots_backwards_compatible():
    assert trade.resolve_offered_slots(None, 1, 1, party_size=3) == [1]
    assert trade.resolve_offered_slots(None, 0, 1, party_size=2) == [0]
    assert trade.resolve_offered_slots([0, 2], 1, 2, party_size=3) == [0, 2]


def test_trade_plan_validates_party_arrangement():
    """The webapp's arrangement (selected slot 0 + filler slot 1, one trade) is a
    valid TradePlan, and a single-mon party is also structurally valid."""
    plan = configmod.TradePlan(party_paths=("sel.pk3", "filler.pk3"),
                               trade_slot=0, trades=1)
    assert plan.trade_slot == 0
    single = configmod.TradePlan(party_paths=("only.pk3",), trade_slot=0, trades=1)
    assert single.trade_slot == 0
    with pytest.raises(ValueError):
        configmod.TradePlan(party_paths=("a.pk3",), trade_slot=1, trades=1)
    with pytest.raises(ValueError):  # re-offering the same slot is invalid
        configmod.TradePlan(party_paths=("a.pk3", "b.pk3"), trade_slot=0, trades=2,
                            offered_slots=(0, 0))


def test_offline_trade_engine_constructs(tmp_path):
    """TradeEngine builds offline with the webapp's party arrangement."""
    party = [monmod.Mon.from_file(str(p)) for p in
             (write_mon(tmp_path / "sel.pk3", build_mon(25)),
              write_mon(tmp_path / "filler.pk3", build_mon(16)))]
    engine = trade.TradeEngine(party, trade_slot=0, anim_delay=1, trades=1, mpid=1)
    assert engine.state == trade.S1_LINK
    assert engine.offered_slots == [0]
    assert engine.trade_slot == 0


def test_runtime_load_party(tmp_path):
    paths = [write_mon(tmp_path / "a.pk3", build_mon(25)),
             write_mon(tmp_path / "b.pk3", build_mon(16))]
    party = runtime.load_party([str(p) for p in paths])
    assert [m.species for m in party] == [25, 16]


def write_mon(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path
