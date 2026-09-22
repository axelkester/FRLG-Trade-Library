"""Configuration loading and validation tests (no server needed)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from webapp.config import ConfigError, REPO_ROOT, load_config


def test_defaults_when_config_missing(tmp_path):
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg.app.host == "127.0.0.1" and cfg.app.port == 8000
    assert cfg.trade.phy == "phy1"
    assert cfg.trade.dry_run is False
    assert cfg.library.path.is_absolute()


def test_full_config_parsed(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(textwrap.dedent(f"""
        [app]
        host = "0.0.0.0"
        port = 8080

        [library]
        path = "{tmp_path / 'lib'}"
        received_path = "{tmp_path / 'received'}"
        auto_add_received = true

        [trade]
        phy = "phy2"
        keys = "{tmp_path / 'keys'}"
        python = "/usr/bin/python3"
        password = "aabb"
        comm_id = "1122"
        ot = "ASH"
        version = "firered"
        timeout = 120
        prefix = ["sudo", "-E"]
        verbose = false
        dry_run = true
        dry_run_scale = 0.5
        idle_reset_seconds = 7.5
    """), encoding="utf-8")
    cfg = load_config(config)
    assert cfg.app.host == "0.0.0.0" and cfg.app.port == 8080
    assert cfg.library.path == tmp_path / "lib"
    assert cfg.library.auto_add_received is True
    assert cfg.trade.phy == "phy2"
    assert cfg.trade.keys == tmp_path / "keys"
    assert cfg.trade.password == "aabb" and cfg.trade.comm_id == "1122"
    assert cfg.trade.ot == "ASH" and cfg.trade.version == "firered"
    assert cfg.trade.timeout == 120.0
    assert cfg.trade.prefix == ("sudo", "-E")
    assert cfg.trade.verbose is False and cfg.trade.dry_run is True
    assert cfg.trade.dry_run_scale == 0.5
    assert cfg.trade.idle_reset_seconds == 7.5


def test_relative_paths_resolve_against_repo(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(textwrap.dedent("""
        [library]
        path = "./pokemon_library"
        received_path = "./received"
    """), encoding="utf-8")
    cfg = load_config(config)
    assert cfg.library.path == REPO_ROOT / "pokemon_library"
    assert cfg.library.received_path == REPO_ROOT / "received"


@pytest.mark.parametrize("section,line,message", [
    ("app", "port = 0", "port"),
    ("app", "port = 70000", "port"),
    ("trade", 'phy = "wlan0"', "phy"),
    ("trade", 'phy = "phy"', "phy"),
    ("trade", 'version = "emerald"', "version"),
    ("trade", 'password = "not-hex!"', "password"),
    ("trade", 'comm_id = "xyz"', "comm_id"),
    ("trade", 'ot = "TOO_LONG_NAME"', "ot"),
    ("trade", 'ot = "日本語"', "ot"),
    ("trade", "timeout = 0", "timeout"),
    ('trade', 'prefix = ["ok", ""]', "prefix"),
    ("trade", "idle_reset_seconds = -1", "idle_reset_seconds"),
    ("library", 'auto_add_received = "yes"', "auto_add_received"),
])
def test_invalid_values_rejected(tmp_path, section, line, message):
    config = tmp_path / "bad.toml"
    config.write_text(f"[{section}]\n{line}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_config(config)


def test_malformed_toml_rejected(tmp_path):
    config = tmp_path / "bad.toml"
    config.write_text("this is [ not toml", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(config)
