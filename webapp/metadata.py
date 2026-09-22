"""Gen III metadata lookups for the web application.

Loads the vendored ``webapp/data/gen3.json`` (generated once by
``tools/build_metadata.py`` from the pret/pokefirered decompilation) and exposes
small, typed lookups: internal species id -> National Dex number, item/move/ability
names, met locations, balls, origin games, languages and PID-based gender.

IMPORTANT: Generation III internal species ids are NOT National Dex numbers in
general (e.g. internal 411 is Chimecho, National #358). Every mapping here comes
from the decomp's ``sSpeciesToNationalPokedexNum`` table.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

GENDERLESS = 255
MON_FEMALE = 254


class Gen3Metadata:
    """Read-only lookup tables derived from pret/pokefirered."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        with open(self.path, encoding="utf-8") as fh:
            raw = json.load(fh)
        self.meta: dict[str, Any] = raw["meta"]
        self.species: dict[str, Any] = raw["species"]            # "national" -> entry
        self.internal_to_national: dict[int, int] = {
            int(k): v for k, v in raw["internal_to_national"].items()}
        self.national_to_internal: dict[int, int] = {
            int(k): v for k, v in raw["national_to_internal"].items()}
        self.items: dict[int, dict[str, str]] = {
            int(k): v for k, v in raw["items"].items()}
        self.moves: dict[int, dict[str, str]] = {
            int(k): v for k, v in raw["moves"].items()}
        self.abilities: dict[int, str] = {int(k): v for k, v in raw["abilities"].items()}
        self.types: dict[int, str] = {int(k): v for k, v in raw["types"].items()}
        self.balls: dict[int, str] = {int(k): v for k, v in raw["balls"].items()}
        self.games: dict[int, str] = {int(k): v for k, v in raw["games"].items()}
        self.natures: dict[int, str] = {int(k): v for k, v in raw["natures"].items()}
        self.languages: dict[int, str] = {int(k): v for k, v in raw["languages"].items()}
        self.met_locations: dict[int, str] = {
            int(k): v for k, v in raw["met_locations"].items()}

    # ---- species -----------------------------------------------------------
    def dex_count(self) -> int:
        return len(self.species)

    def species_entry(self, national: int) -> dict[str, Any] | None:
        return self.species.get(str(national))

    def national_for_internal(self, internal: int) -> int | None:
        return self.internal_to_national.get(internal)

    def internal_for_national(self, national: int) -> int | None:
        return self.national_to_internal.get(national)

    @lru_cache(maxsize=1024)
    def species_name(self, national: int) -> str:
        entry = self.species_entry(national)
        return entry["name"] if entry else f"#{national}"

    def species_types(self, national: int) -> list[str]:
        entry = self.species_entry(national)
        return list(entry["types"]) if entry else []

    def ability_names(self, national: int) -> list[str]:
        entry = self.species_entry(national)
        return list(entry["abilities"]) if entry else []

    # ---- flat id -> name tables --------------------------------------------
    def item_name(self, item_id: int) -> str:
        entry = self.items.get(item_id)
        return entry["display"] if entry else f"Item #{item_id}"

    def move_name(self, move_id: int) -> str:
        entry = self.moves.get(move_id)
        return entry["display"] if entry else ("—" if move_id == 0 else f"Move #{move_id}")

    def ability_name(self, ability_id: int) -> str:
        return self.abilities.get(ability_id, "—")

    def ball_name(self, ball_id: int) -> str:
        return self.balls.get(ball_id, f"Ball #{ball_id}")

    def game_name(self, game_id: int) -> str:
        return self.games.get(game_id, f"Game #{game_id}")

    def language_name(self, language_id: int) -> str:
        return self.languages.get(language_id, f"Language #{language_id}")

    def nature_name(self, nature_id: int) -> str:
        return self.natures.get(nature_id, f"Nature #{nature_id}")

    def met_location_name(self, location_id: int) -> str:
        return self.met_locations.get(location_id, f"Location #{location_id}")

    # ---- derived ------------------------------------------------------------
    @staticmethod
    def gender_from_pid(pid: int, gender_ratio: int) -> str:
        """Gen III gender from the personality low byte and the species ratio."""
        if gender_ratio == GENDERLESS:
            return "genderless"
        if gender_ratio == MON_FEMALE:
            return "female"
        if gender_ratio == 0:
            return "male"
        return "female" if (pid & 0xFF) < gender_ratio else "male"

    def dex_list(self) -> list[dict[str, Any]]:
        """The complete National Pokédex (#1..#386) in dex order, with sprite paths."""
        return [
            {
                "national": int(national),
                "internal": entry["internal"],
                "name": entry["name"],
                "types": entry["types"],
                "sprite": entry["sprite"],
                "abilities": entry["abilities"],
                "gender_ratio": entry["gender_ratio"],
                "growth": entry["growth"],
                "base_stats": entry["base_stats"],
                "friendship": entry["friendship"],
                "catch_rate": entry["catch_rate"],
            }
            for national, entry in sorted(self.species.items(), key=lambda kv: int(kv[0]))
        ]


def load_metadata(path: Path | str) -> Gen3Metadata:
    return Gen3Metadata(path)
