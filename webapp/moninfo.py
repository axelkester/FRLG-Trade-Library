"""Metadata-enriched, read-only decoding of .pk3/.ek3 files for the web UI.

The binary decoding itself lives in ``frlgsim.mon`` (``decode_mon`` /
``decode_file``): this module only ADDS metadata lookups (species names, item/move
names, ball, origin game, met location, gender) so the decoder stays standalone.
The original file bytes are never modified - only read and decoded in memory.
"""

from __future__ import annotations

from typing import Any

from frlgsim import mon as monmod

from .metadata import Gen3Metadata

# Library entries with these statuses are safe to offer to the live trade path.
TRADEABLE_STATUSES = frozenset(("ok",))


def decode_entry(data: bytes, meta: Gen3Metadata) -> dict[str, Any]:
    """Decode one .pk3/.ek3 buffer into a JSON-safe display dict.

    Returns ``{"error": "<reason>", "decodable": False, ...}`` for undecodable
    buffers; otherwise a rich dict whose ``status`` is one of:
      "ok"               - valid checksum, known species
      "checksum_failure" - decoded best-effort, checksum mismatch
      "undecodable"      - unknown species id (bad egg / corrupt)
    """
    if len(data) not in (monmod.BOX_SIZE, monmod.PARTY_MON_SIZE):
        return {
            "error": "invalid_size", "decodable": False,
            "size": len(data),
        }
    d = monmod.decode_file(data)
    if d is None:
        return {"error": "undecodable", "decodable": False, "size": len(data)}

    national = meta.national_for_internal(d["species"])
    if national is None:
        return {
            "error": "undecodable", "decodable": False, "size": len(data),
            "species_internal": d["species"],
            "checksum_ok": d["checksum_ok"],
        }
    species = meta.species_entry(national) or {}
    gender = meta.gender_from_pid(d["pid"], species.get("gender_ratio", 255))
    abilities = species.get("abilities", [])
    ability_name = (abilities[d["iv_ability"]] if d["iv_ability"] < len(abilities)
                    else "—")
    level = d["level"] if d["level"] is not None else d["level_derived"]
    return {
        "decodable": True,
        "status": "ok" if d["checksum_ok"] else "checksum_failure",
        "format": "ek3" if d["is_ek3"] else "pk3",
        "size": d["size"],
        "variant": "party" if d["size"] == 100 else "box",
        "species": d["species"],
        "species_national": national,
        "species_name": species.get("name", f"#{d['species']}"),
        "types": species.get("types", []),
        "nickname": d["nickname"],
        "ot_name": d["otName"],
        "level": level,
        "level_stored": d["level"],
        "exp": d["exp"],
        "held_item": {"id": d["heldItem"], "name": meta.item_name(d["heldItem"])},
        "moves": [{"id": move_id, "name": meta.move_name(move_id)}
                  for move_id in d["moves"]],
        "pid": d["pid"],
        "tid": d["tid"],
        "sid": d["sid"],
        "checksum_ok": d["checksum_ok"],
        "checksum": {"stored": d["stored"], "calc": d["calc"]},
        "nature": d["nature"],
        "nature_name": d["nature_name"] or meta.nature_name(d["nature"]),
        "shiny": d["shiny"],
        "ivs": d["ivs"],
        "evs": d["evs"],
        "friendship": d["friendship"],
        "ability_slot": d["iv_ability"],
        "ability_name": ability_name,
        "gender": gender,
        "gender_ratio": species.get("gender_ratio", 255),
        "pokerus": {
            "value": d["pokerus"], "days": d["pokerus_days"],
            "strain": d["pokerus_strain"],
            "infected": d["pokerus"] not in (0,),
        },
        "met_location": {
            "id": d["met_location"], "name": meta.met_location_name(d["met_location"])},
        "ball": {"id": d["ball"], "name": meta.ball_name(d["ball"])},
        "met_game": {"id": d["met_game"], "name": meta.game_name(d["met_game"])},
        "met_level": d["met_level"],
        "language": {"id": d["language"], "name": meta.language_name(d["language"])},
        "is_egg": d["is_egg"],
        "is_bad_egg": d["is_bad_egg"],
        "ot_gender": "male" if d["ot_gender"] == 0 else "female",
        "base_stats": species.get("base_stats"),
        "growth_rate": species.get("growth"),
        "catch_rate": species.get("catch_rate"),
    }
