"""Command-line entry point: ``python -m webapp``.

Launches the FastAPI app with uvicorn. All settings come from config.toml (see the
repository root), with optional command-line overrides. For live trades the existing
LDN transport needs elevated network privileges: run the whole launcher with
``sudo -E`` for development (see README for the privileged-worker alternative).
"""

from __future__ import annotations

import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

from . import __version__
from .app import create_app, resolve_config
from .config import DEFAULT_CONFIG, REPO_ROOT


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m webapp",
        description="Self-hosted FRLG Gen III Pokémon trade library.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(DEFAULT_CONFIG),
                    help=f"path to config.toml (default: {DEFAULT_CONFIG})")
    ap.add_argument("--host", default=None, help="override [app].host")
    ap.add_argument("--port", type=int, default=None, help="override [app].port")
    ap.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=None,
                    help="simulate trades without a Switch (override [trade].dry_run)")
    ap.add_argument("--debug", action="store_true", help="debug logging")
    return ap


def configure_log_file(path: Path | None) -> RotatingFileHandler | None:
    """Attach a rotating file handler to the ROOT logger so every webapp message
    AND every frlgtrade.py child line (re-logged by the trade manager) lands in
    one greppable file. Returns the handler (for tests/cleanup) or None when file
    logging is disabled or the file cannot be opened."""
    if path is None:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=4,
                                      encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        logging.getLogger().addHandler(handler)
        logging.getLogger("frlgweb").info("log file: %s", path)
        return handler
    except OSError as exc:
        logging.getLogger("frlgweb").warning(
            "cannot open log file %s (%s); continuing with stderr logging only",
            path, exc)
        return None


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    log = logging.getLogger("frlgweb")
    log.info("FRLG trade library webapp v%s (repo: %s)", __version__, REPO_ROOT)
    cfg = resolve_config(args.config, dry_run=args.dry_run,
                         host=args.host, port=args.port)
    configure_log_file(cfg.app.log_file)
    for warning in cfg.validate_runtime():
        log.warning("%s", warning)
    log.info("serving on http://%s:%s (library=%s, received=%s, phy=%s, dry_run=%s)",
             cfg.app.host, cfg.app.port, cfg.library.path,
             cfg.library.received_path, cfg.trade.phy, cfg.trade.dry_run)
    app = create_app(cfg=cfg)
    uvicorn.run(app, host=cfg.app.host, port=cfg.app.port, log_level="info",
                access_log=not args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
