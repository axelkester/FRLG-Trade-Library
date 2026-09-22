"""FastAPI application for the FRLG trade library.

Thin controllers only: the Pokédex metadata comes from webapp/metadata.py (vendored
decomp data), Pokémon parsing from frlgsim.mon via webapp/moninfo.py, the library
index from webapp/library.py, and live trading from the EXISTING frlgtrade.py CLI
driven by webapp/trader.py. The browser never supplies filesystem paths - only
indexed content-hash ids validated against ``^[0-9a-f]{64}$``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .config import ConfigError, WebConfig, load_config
from .library import Library
from .metadata import Gen3Metadata, load_metadata
from .trader import TradeBusyError, TradeManager, TradeNotAllowedError

log = logging.getLogger("frlgweb.app")

PACKAGE_DIR = Path(__file__).resolve().parent
_TEMPLATES = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))


class TradeRequest(BaseModel):
    id: str = Field(pattern=r"^[0-9a-f]{64}$", description="indexed Pokémon id")


class AppState:
    def __init__(self, cfg: WebConfig):
        self.cfg = cfg
        self.meta: Gen3Metadata = load_metadata(cfg.metadata_path)
        self.library = Library(cfg.library.path, self.meta)
        self.trader = TradeManager(cfg, self.library, self.meta)


def resolve_config(config_path: Path | str | None = None, *,
                   dry_run: bool | None = None,
                   host: str | None = None, port: int | None = None) -> WebConfig:
    """Load config.toml and apply the optional CLI overrides."""
    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        raise SystemExit(f"configuration error: {exc}") from exc
    if dry_run is not None or host is not None or port is not None:
        from dataclasses import replace
        app_cfg = replace(cfg.app,
                          host=host if host is not None else cfg.app.host,
                          port=port if port is not None else cfg.app.port)
        trade_cfg = replace(cfg.trade,
                            dry_run=dry_run if dry_run is not None else cfg.trade.dry_run)
        cfg = replace(cfg, app=app_cfg, trade=trade_cfg)
    return cfg


async def event_stream(request: Request, trader: TradeManager):
    """Yield SSE frames for one subscriber: state, log and received events.

    Extracted from the route so the generator is directly unit-testable; the
    keepalive comment (``: keepalive``) doubles as the client reconnection guide
    via the ``retry:`` field.
    """
    queue = trader.bus.subscribe()
    try:
        yield "retry: 2000\n\n"
        yield f"event: state\ndata: {json.dumps(trader.state_payload())}\n\n"
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield (f"event: {event['type']}\n"
                   f"data: {json.dumps(event, default=str)}\n\n")
    finally:
        trader.bus.unsubscribe(queue)


def create_app(config_path: Path | str | None = None, *,
               dry_run: bool | None = None,
               host: str | None = None, port: int | None = None,
               cfg: WebConfig | None = None) -> FastAPI:
    """Build the ASGI app. All parameters are optional (config.toml defaults)."""
    if cfg is None:
        cfg = resolve_config(config_path, dry_run=dry_run, host=host, port=port)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state = AppState(cfg)
        app.state.frlg = state
        for warning in cfg.validate_runtime():
            log.warning("%s", warning)
        state.library.scan()
        log.info("library root=%s (received=%s) dry_run=%s",
                 state.library.root, state.trader.received_dir, cfg.trade.dry_run)
        yield
        await state.trader.shutdown()

    app = FastAPI(title="FRLG Trade Library", lifespan=lifespan)
    sprites_dir = cfg.sprites_dir
    sprites_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static"),
                                     check_dir=False), name="static")

    def state() -> AppState:
        return app.state.frlg

    # ---- pages ---------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return _TEMPLATES.TemplateResponse(
            request, "index.html", {"app_title": "FRLG Trade Library"})

    # ---- Pokédex --------------------------------------------------------------
    @app.get("/api/pokedex")
    async def pokedex() -> dict[str, Any]:
        st = state()
        counts = st.library.counts_by_national()
        species = []
        for entry in st.meta.dex_list():
            total, shiny = counts.get(entry["national"], (0, 0))
            species.append({
                "national": entry["national"],
                "name": entry["name"],
                "types": entry["types"],
                "sprite": f"/static/{entry['sprite']}",
                "count": total,
                "shiny_count": shiny,
                "available": total > 0,
            })
        return {"count": len(species), "species": species}

    @app.get("/api/species/{national}")
    async def species_detail(national: int) -> dict[str, Any]:
        st = state()
        if not 1 <= national <= st.meta.dex_count():
            raise HTTPException(status_code=404, detail="unknown species")
        entry = st.meta.species_entry(national)
        instances = [e.to_card() for e in st.library.by_national(national)]
        return {
            "national": national,
            "internal": entry["internal"],
            "name": entry["name"],
            "types": entry["types"],
            "sprite": f"/static/{entry['sprite']}",
            "abilities": entry["abilities"],
            "base_stats": entry["base_stats"],
            "gender_ratio": entry["gender_ratio"],
            "instances": instances,
        }

    # ---- Pokémon instances ----------------------------------------------------
    @app.get("/api/pokemon/{entry_id}")
    async def pokemon_detail(entry_id: str) -> dict[str, Any]:
        st = state()
        entry = st.library.get(entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="unknown Pokémon id")
        payload = dict(entry.summary)
        payload.update({
            "id": entry.id,
            "relpath": entry.relpath,
            "mtime": entry.mtime,
            "tradeable": entry.tradeable,
        })
        return payload

    # ---- library ---------------------------------------------------------------
    @app.get("/api/library")
    async def library_status() -> dict[str, Any]:
        st = state()
        stats = st.library.stats().to_dict()
        stats["root"] = str(st.library.root)
        return stats

    @app.post("/api/library/rescan")
    async def library_rescan() -> dict[str, Any]:
        st = state()
        stats = st.library.scan().to_dict()
        log.info("library rescanned: %s files", stats["files"])
        return stats

    # ---- trade ------------------------------------------------------------------
    @app.get("/api/state")
    async def trade_state() -> dict[str, Any]:
        return state().trader.state_payload()

    @app.post("/api/trade")
    async def start_trade(request: TradeRequest) -> dict[str, Any]:
        trader = state().trader
        try:
            return await trader.start(request.id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown Pokémon id") from exc
        except TradeNotAllowedError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except TradeBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/trade/cancel")
    async def cancel_trade() -> dict[str, Any]:
        return await state().trader.cancel()

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        """Server-Sent Events: state changes, log lines, received Pokémon."""
        return StreamingResponse(
            event_stream(request, state().trader),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ---- history -----------------------------------------------------------------
    @app.get("/api/history")
    async def history() -> dict[str, Any]:
        trader = state().trader
        return {"records": list(reversed(trader.history))}

    @app.post("/api/history/{record_id}/add-to-library")
    async def add_to_library(record_id: str) -> dict[str, Any]:
        st = state()
        try:
            return st.trader.add_to_library(record_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown history record") from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app
