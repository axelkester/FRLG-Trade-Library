"""Configuration for the frlg-ldn-trade web application.

Loads ``config.toml`` (TOML), validates every field, and exposes typed, frozen
dataclasses. The trade section only describes HOW the existing ``frlgtrade.py`` CLI
is invoked - the web application never reimplements LDN.

Privilege note: the existing LDN transport needs elevated network privileges, so the
whole stack is normally run with ``sudo -E`` (development) or a narrowly-scoped
privileged wrapper; see README. ``[trade].prefix`` exists for that wrapper (it is
admin-controlled config, never browser input).
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - depends on interpreter
    import tomli as tomllib  # type: ignore[no-redef]

from frlgsim import charmap

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "config.toml"

_PHY_RE = re.compile(r"^phy\d+$")
_VERSION_CHOICES = ("firered", "leafgreen")
_OT_LIMIT = 7  # Gen III trainer names are at most 7 encoded characters


class ConfigError(ValueError):
    """Raised for an invalid or unreadable configuration file."""


@dataclass(frozen=True)
class AppConfig:
    host: str = "127.0.0.1"
    port: int = 8000

    def __post_init__(self) -> None:
        if not isinstance(self.host, str) or not self.host:
            raise ConfigError("[app].host must be a non-empty string")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ConfigError("[app].port must be an integer in 1..65535")


@dataclass(frozen=True)
class LibraryConfig:
    path: Path = REPO_ROOT / "pokemon_library"
    received_path: Path = REPO_ROOT / "received"
    auto_add_received: bool = False

    def __post_init__(self) -> None:
        if type(self.auto_add_received) is not bool:
            raise ConfigError("[library].auto_add_received must be a boolean")


@dataclass(frozen=True)
class TradeConfig:
    phy: str = "phy1"
    keys: Path = Path("~/.switch/prod.keys").expanduser()
    python: str = sys.executable
    password: str = ""          # hex LDN passphrase; empty = the built-in emulator phrase
    comm_id: str = ""           # hex local_communication_id; empty = join the only network
    ot: str = "EMU"
    version: str = "leafgreen"
    timeout: float = 600.0      # seconds before a hung live trade is force-stopped
    prefix: tuple[str, ...] = ()  # argv prefix, e.g. ("sudo", "-E") for the LDN privileges
    verbose: bool = True
    dry_run: bool = False
    dry_run_scale: float = 1.0    # multiplies the simulated milestone delays (dev/tests)
    # Seconds after a FINISHED trade (FAILED/CANCELLED/COMPLETED) before the state
    # machine returns to IDLE on its own (0 = never).
    idle_reset_seconds: float = 15.0

    def __post_init__(self) -> None:
        if not isinstance(self.phy, str) or not _PHY_RE.match(self.phy):
            raise ConfigError("[trade].phy must look like 'phy1'")
        if not isinstance(self.keys, (str, Path)) or not str(self.keys):
            raise ConfigError("[trade].keys must be a path to prod.keys")
        if type(self.python) is not str or not self.python:
            raise ConfigError("[trade].python must name the Python interpreter to use")
        for hex_field in ("password", "comm_id"):
            value = getattr(self, hex_field)
            if type(value) is not str:
                raise ConfigError(f"[trade].{hex_field} must be a hex string")
            if value:
                try:
                    bytes.fromhex(value)
                except ValueError as exc:
                    raise ConfigError(f"[trade].{hex_field} must contain hex digits") from exc
        if type(self.ot) is not str or not self.ot:
            raise ConfigError("[trade].ot must be a non-empty trainer name")
        encoded = charmap.encode(self.ot)
        if charmap.decode(encoded) != self.ot or len(encoded) > _OT_LIMIT:
            raise ConfigError(f"[trade].ot must encode to at most {_OT_LIMIT} "
                              "Gen III characters")
        if self.version not in _VERSION_CHOICES:
            raise ConfigError(f"[trade].version must be one of {_VERSION_CHOICES}")
        if type(self.timeout) not in (int, float) or self.timeout <= 0:
            raise ConfigError("[trade].timeout must be a positive number of seconds")
        if type(self.prefix) is tuple and all(type(tok) is str and tok for tok in self.prefix):
            pass  # validated list form
        else:
            raise ConfigError("[trade].prefix must be a list of non-empty argv tokens")
        if type(self.verbose) is not bool or type(self.dry_run) is not bool:
            raise ConfigError("[trade].verbose / .dry_run must be booleans")
        if type(self.dry_run_scale) not in (int, float) or self.dry_run_scale <= 0:
            raise ConfigError("[trade].dry_run_scale must be a positive number")
        if type(self.idle_reset_seconds) not in (int, float) \
                or self.idle_reset_seconds < 0:
            raise ConfigError("[trade].idle_reset_seconds must be a non-negative "
                              "number of seconds (0 = never auto-reset)")


@dataclass(frozen=True)
class WebConfig:
    app: AppConfig
    library: LibraryConfig
    trade: TradeConfig
    repo_root: Path = REPO_ROOT
    metadata_path: Path = REPO_ROOT / "webapp" / "data" / "gen3.json"
    sprites_dir: Path = REPO_ROOT / "webapp" / "static" / "sprites"

    @property
    def frlgtrade_script(self) -> Path:
        return self.repo_root / "frlgtrade.py"

    @property
    def python_resolved(self) -> str:
        return str(Path(self.trade.python).expanduser())

    def validate_runtime(self) -> list[str]:
        """Soft runtime checks; returns human-readable warnings (never raises)."""
        warnings: list[str] = []
        if not self.frlgtrade_script.is_file():
            warnings.append(f"frlgtrade.py not found at {self.frlgtrade_script}")
        if not self.trade.dry_run and not Path(self.trade.keys).expanduser().is_file():
            warnings.append(f"prod.keys not found at {self.trade.keys} "
                            "(live trades will fail until it exists)")
        if not self.trade.dry_run and not Path(self.python_resolved).exists():
            warnings.append(f"configured Python interpreter not found: {self.python_resolved}")
        return warnings


def _path(value: Any, key: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{key} must be a non-empty path string")
    return Path(value).expanduser()


def load_config(path: Path | str | None = None) -> WebConfig:
    """Load and validate ``config.toml``. Missing optional sections use defaults."""
    config_path = Path(path) if path else DEFAULT_CONFIG
    raw: dict[str, Any] = {}
    if config_path.exists():
        try:
            with open(config_path, "rb") as fh:
                raw = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"cannot read {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path} must contain a TOML table")

    app_raw = raw.get("app", {}) or {}
    lib_raw = raw.get("library", {}) or {}
    trade_raw = raw.get("trade", {}) or {}
    if not all(isinstance(section, dict) for section in (app_raw, lib_raw, trade_raw)):
        raise ConfigError("the [app], [library] and [trade] sections must be TOML tables")

    app = AppConfig(
        host=app_raw.get("host", "127.0.0.1"),
        port=app_raw.get("port", 8000),
    )
    library = LibraryConfig(
        path=_path(lib_raw.get("path", "./pokemon_library"), "[library].path"),
        received_path=_path(lib_raw.get("received_path", "./received"),
                            "[library].received_path"),
        auto_add_received=lib_raw.get("auto_add_received", False),
    )
    # Relative paths resolve against the repository root, not the process cwd.
    if not library.path.is_absolute():
        library = LibraryConfig(
            path=(REPO_ROOT / library.path),
            received_path=(REPO_ROOT / library.received_path),
            auto_add_received=library.auto_add_received,
        )
    python_value = str(trade_raw.get("python", sys.executable))
    if python_value.startswith("."):  # e.g. "./bin/python" = the project venv
        python_value = str((REPO_ROOT / python_value))
    trade = TradeConfig(
        phy=trade_raw.get("phy", "phy1"),
        keys=_path(trade_raw.get("keys", "~/.switch/prod.keys"), "[trade].keys"),
        python=python_value,
        password=str(trade_raw.get("password", "")),
        comm_id=str(trade_raw.get("comm_id", "")),
        ot=str(trade_raw.get("ot", "EMU")),
        version=str(trade_raw.get("version", "leafgreen")),
        timeout=float(trade_raw.get("timeout", 600.0)),
        prefix=tuple(str(tok) for tok in trade_raw.get("prefix", ())),
        verbose=bool(trade_raw.get("verbose", True)),
        dry_run=bool(trade_raw.get("dry_run", False)),
        dry_run_scale=float(trade_raw.get("dry_run_scale", 1.0)),
        idle_reset_seconds=float(trade_raw.get("idle_reset_seconds", 15.0)),
    )
    return WebConfig(
        app=app,
        library=library,
        trade=trade,
        repo_root=REPO_ROOT,
        metadata_path=REPO_ROOT / "webapp" / "data" / "gen3.json",
        sprites_dir=REPO_ROOT / "webapp" / "static" / "sprites",
    )
