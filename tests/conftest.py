"""Shared fixtures for the frlg-ldn-trade web application tests.

Every fixture is built from the existing frlgsim primitives (charmap, stats,
mon.to_encrypted) plus webapp.filler.build_mon - no hand-rolled byte offsets, and no
real Switch anywhere in the suite.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from frlgsim import mon as monmod

from webapp.app import create_app
from webapp.config import WebConfig
from webapp.filler import build_mon
from webapp.metadata import Gen3Metadata, load_metadata

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / "webapp" / "data" / "gen3.json"


@pytest.fixture(scope="session")
def meta() -> Gen3Metadata:
    return load_metadata(DATA)


@pytest.fixture
def make_pk3():
    """Factory for deterministic, valid decrypted .pk3 bytes (100B party).

    The default personality/otId pair is deliberately NOT shiny (xor value 15),
    so tests can opt into shiny with build_mon(shiny=True)."""
    def _make(*, species: int = 25, pid: int = 0x12345678, otid: int = 0x9ABCDEFF,
              **kwargs) -> bytes:
        return build_mon(species, pid=pid, otid=otid, **kwargs)
    return _make


@pytest.fixture
def web_config(tmp_path: Path) -> WebConfig:
    """A fully isolated dry-run configuration rooted in a temp directory."""
    from webapp.config import AppConfig, LibraryConfig, TradeConfig
    cfg = WebConfig(
        app=AppConfig(host="127.0.0.1", port=8000),
        library=LibraryConfig(path=tmp_path / "pokemon_library",
                              received_path=tmp_path / "received",
                              auto_add_received=False),
        trade=TradeConfig(phy="phy1", keys=Path("~/.switch/prod.keys").expanduser(),
                          python=sys.executable, dry_run=True, dry_run_scale=0.01),
        repo_root=REPO_ROOT,
        metadata_path=DATA,
        sprites_dir=tmp_path / "sprites",
    )
    return cfg


@pytest.fixture
def client(web_config: WebConfig):
    """A TestClient whose lifespan already ran (library scanned, etc.)."""
    app = create_app(cfg=web_config)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def add_file(web_config: WebConfig):
    """Write bytes into the configured library root, returning the (relpath, id)."""
    import hashlib

    def _add(name: str, data: bytes, subdir: str = "") -> tuple[str, str]:
        target = web_config.library.path / subdir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return (f"{subdir}/{name}" if subdir else name,
                hashlib.sha256(data).hexdigest())
    return _add


@pytest.fixture
def wire_mon():
    """A valid .ek3 (encrypted wire form) of the PIKACHU fixture."""
    return monmod.to_encrypted(build_mon(25, pid=0x12345678, otid=0x9ABCDEFF))
