#!/usr/bin/env python3
"""One-shot generator for ``webapp/data/gen3.json``.

Downloads a small set of authoritative Gen III data files from the pret/pokefirered
decompilation (pinned commit) and compiles them into a single JSON blob that the web
application loads at startup. The generated JSON is vendored into the repository, so the
running application never needs network access and the test suite runs offline.

Sources (all at the pinned commit):
  include/constants/species.h          internal species id -> constant name
  include/constants/pokedex.h          NATIONAL_DEX_* enum (1..386, plus old-Unown slots)
  src/pokemon.c                        sSpeciesToNationalPokedexNum: internal -> national
  src/data/pokemon/species_info.h      base stats, types, gender ratio, growth, abilities
  src/data/text/move_names.h           authoritative in-game English move names
  include/constants/items.h            item id -> constant name
  include/constants/moves.h            move id -> constant name
  include/constants/abilities.h        ability id -> constant name
  include/constants/pokemon.h          TYPE_*/GROWTH_*/MON_* constant values
  src/data/region_map/region_map_sections.json   FRLG map-section (met location) names

Special met-location values (0xFC..0xFE) are the documented FRLG sentinels, cross-checked
against PKHeX's Gen 3 FRLG location table:
  0xFC = METLOC_SPECIAL_EGG     ("Gift Egg")
  0xFD = METLOC_IN_GAME_TRADE   ("In-game Trade")
  0xFE = METLOC_FATEFUL_ENCOUNTER ("Fateful Encounter")

Usage:  python3 tools/build_metadata.py [--out webapp/data/gen3.json]
"""

import argparse
import json
import re
import ssl
import sys
import tempfile
import urllib.request
from pathlib import Path

COMMIT = "c75f352304d529f6ba92d4f74b9cf8b5c3810788"  # pinned pokefirered master
BASE = f"https://raw.githubusercontent.com/pret/pokefirered/{COMMIT}"

FILES = {
    "species.h": "include/constants/species.h",
    "pokedex.h": "include/constants/pokedex.h",
    "pokemon_constants.h": "include/constants/pokemon.h",
    "items.h": "include/constants/items.h",
    "moves.h": "include/constants/moves.h",
    "abilities.h": "include/constants/abilities.h",
    "species_info.h": "src/data/pokemon/species_info.h",
    "move_names.h": "src/data/text/move_names.h",
    "pokemon.c": "src/pokemon.c",
    "region_map_sections.json": "src/data/region_map/region_map_sections.json",
}

# ── small, well-documented static tables (not per-species data) ─────────────────────
NATURES = [
    "Hardy", "Lonely", "Brave", "Adamant", "Naughty",
    "Bold", "Docile", "Relaxed", "Impish", "Lax",
    "Timid", "Hasty", "Serious", "Jolly", "Naive",
    "Modest", "Mild", "Quiet", "Bashful", "Rash",
    "Calm", "Gentle", "Sassy", "Careful", "Quirky",
]
# Gen III met-game codes (PokemonSubstruct3.originsInfo & 0x7).
GAMES = {
    0: "Sapphire", 1: "Ruby", 2: "Emerald", 3: "FireRed",
    4: "LeafGreen", 5: "HeartGold", 6: "SoulSilver", 15: "Colosseum/XD",
}
# Gen III language codes (struct BoxPokemon.language, u16; low byte holds the value).
LANGUAGES = {
    1: "Japanese", 2: "English", 3: "French", 4: "Italian", 5: "German", 6: "Spanish",
}
# Gen III ball ids (PokemonSubstruct3.originsInfo bits 3..6).
BALLS = [
    "Master Ball", "Ultra Ball", "Great Ball", "Poké Ball", "Safari Ball",
    "Net Ball", "Dive Ball", "Nest Ball", "Repeat Ball", "Timer Ball",
    "Luxury Ball", "Premier Ball",
]
SPECIAL_MET_LOCATIONS = {
    252: "Gift Egg",
    253: "In-game Trade",
    254: "Fateful Encounter",
}

# Species constants whose display name cannot be derived by title-casing.
SPECIES_DISPLAY_OVERRIDES = {
    "NIDORAN_F": "Nidoran♀", "NIDORAN_M": "Nidoran♂",
    "FARFETCHD": "Farfetch'd", "MR_MIME": "Mr. Mime",
    "HO_OH": "Ho-Oh", "PORYGON2": "Porygon2",
}

# Item constants whose display name cannot be derived by title-casing (Gen III set).
ITEM_DISPLAY_OVERRIDES = {
    "POKE_BALL": "Poké Ball", "GREAT_BALL": "Great Ball", "ULTRA_BALL": "Ultra Ball",
    "MASTER_BALL": "Master Ball", "SAFARI_BALL": "Safari Ball", "NET_BALL": "Net Ball",
    "DIVE_BALL": "Dive Ball", "NEST_BALL": "Nest Ball", "REPEAT_BALL": "Repeat Ball",
    "TIMER_BALL": "Timer Ball", "LUXURY_BALL": "Luxury Ball", "PREMIER_BALL": "Premier Ball",
    "S_S_TICKET": "S.S. Ticket", "SS_TICKET": "S.S. Ticket",
    "SILPH_SCOPE": "Silph Scope", "DEVON_SCOPE": "Devon Scope", "GO_GOGGLES": "Go-Goggles",
    "EXP_SHARE": "Exp. Share", "PP_UP": "PP Up", "PP_MAX": "PP Max", "HP_UP": "HP Up",
    "BLACK_GLASSES": "BlackGlasses", "PINK_BOW": "Pink Bow", "POLKADOT_BOW": "Polkadot Bow",
    "LUCKY_EGG": "Lucky Egg", "LUCKY_PUNCH": "Lucky Punch", "METAL_COAT": "Metal Coat",
    "METAL_POWDER": "Metal Powder", "KING_S_ROCK": "King's Rock", "DRAGON_SCALE": "Dragon Scale",
    "UP_GRADE": "Up-Grade", "EVERSTONE": "Everstone", "FOCUS_BAND": "Focus Band",
    "SMOKE_BALL": "Smoke Ball", "SILVER_POWDER": "Silver Powder", "QUICK_CLAW": "Quick Claw",
    "SOFT_SAND": "Soft Sand", "HARD_STONE": "Hard Stone", "MIRACLE_SEED": "Miracle Seed",
    "MYSTIC_WATER": "Mystic Water", "SHARP_BEAK": "Sharp Beak", "POISON_BARB": "Poison Barb",
    "NEVER_MELT_ICE": "Never-Melt Ice", "MAGNET": "Magnet", "SPELL_TAG": "Spell Tag",
    "TWISTED_SPOON": "Twisted Spoon", "CHARCOAL": "Charcoal", "SEA_INCENSE": "Sea Incense",
    "LAX_INCENSE": "Lax Incense", "LIGHT_BALL": "Light Ball", "SOUL_DEW": "Soul Dew",
    "DEEP_SEA_TOOTH": "Deep Sea Tooth", "DEEP_SEA_SCALE": "Deep Sea Scale",
    "THICK_CLUB": "Thick Club", "STICK": "Stick", "LEEK": "Leek",
    "SACRED_ASH": "Sacred Ash", "TINY_MUSHROOM": "Tiny Mushroom", "BIG_MUSHROOM": "Big Mushroom",
    "PEARL": "Pearl", "BIG_PEARL": "Big Pearl", "STARDUST": "Stardust",
    "STAR_PIECE": "Star Piece", "NUGGET": "Nugget", "HEART_SCALE": "Heart Scale",
    "RED_SHARD": "Red Shard", "YELLOW_SHARD": "Yellow Shard", "BLUE_SHARD": "Blue Shard",
    "GREEN_SHARD": "Green Shard", "SUN_STONE": "Sun Stone", "MOON_STONE": "Moon Stone",
    "FIRE_STONE": "Fire Stone", "THUNDER_STONE": "Thunder Stone", "WATER_STONE": "Water Stone",
    "LEAF_STONE": "Leaf Stone", "OLD_AMBER": "Old Amber", "HELIX_FOSSIL": "Helix Fossil",
    "DOME_FOSSIL": "Dome Fossil", "ROOT_FOSSIL": "Root Fossil", "CLAW_FOSSIL": "Claw Fossil",
    "RARE_CANDY": "Rare Candy", "BLUE_FLUTE": "Blue Flute", "YELLOW_FLUTE": "Yellow Flute",
    "RED_FLUTE": "Red Flute", "BLACK_FLUTE": "Black Flute", "WHITE_FLUTE": "White Flute",
    "MYSTIC_TICKET": "MysticTicket", "AURORA_TICKET": "AuroraTicket", "EON_TICKET": "Eon Ticket",
    "OLD_SEA_MAP": "Old Sea Map", "SUPER_ROD": "Super Rod", "GOOD_ROD": "Good Rod",
    "OLD_ROD": "Old Rod", "WATER_STONE": "Water Stone", "POKE_RADAR": "Poké Radar",
}


def fetch(cache_dir: Path, local: str) -> str:
    """Download (once, cached) a decomp file and return its text."""
    target = cache_dir / local
    if target.exists():
        return target.read_text(encoding="utf-8")
    url = f"{BASE}/{FILES[local]}"
    print(f"  downloading {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "frlg-web-metadata"})
    try:
        response = urllib.request.urlopen(request, timeout=60)  # noqa: S310 - pinned HTTPS URL
    except urllib.error.URLError as exc:
        # Some Pythons (e.g. the macOS python.org build) ship without CA bundles, so the
        # default context cannot verify. The URL is pinned to an exact git commit, and the
        # generated output is committed and diff-reviewable, so an unverified retry is an
        # acceptable convenience fallback for this one-shot dev tool.
        if "CERTIFICATE_VERIFY_FAILED" not in str(getattr(exc, "reason", "")):
            raise
        print("  WARNING: system CA bundle unavailable - retrying without certificate "
              "verification (pinned commit URL)")
        context = ssl._create_unverified_context()  # noqa: SLF001
        response = urllib.request.urlopen(request, timeout=60, context=context)
    with response:
        data = response.read()
    cache_dir.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return data.decode("utf-8")


def parse_defines(text: str, prefix: str) -> dict:
    """{NAME: int} for ``#define <prefix>NAME <int-or-hex>`` lines."""
    out = {}
    for m in re.finditer(rf"^#define\s+({prefix}\w+)\s+(0x[0-9a-fA-F]+|\d+)\s*$", text, re.M):
        name, value = m.groups()
        out[name] = int(value, 0)
    return out


def parse_first_enum(text: str) -> dict:
    """{NAME: index} for the first ``enum { ... };`` block (0-based indices)."""
    m = re.search(r"enum\s*\{(.*?)\};", text, re.S)
    if not m:
        raise SystemExit("no enum block found")
    body = re.sub(r"//.*", "", m.group(1))
    names = re.findall(r"\b([A-Z][A-Z0-9_]*)\b", body)
    return {name: i for i, name in enumerate(names)}


def parse_species_to_national(pokemon_c: str, national_enum: dict) -> tuple:
    """Parse sSpeciesToNationalPokedexNum -> (internal_to_national, national_to_internal)."""
    m = re.search(
        r"static const u16 sSpeciesToNationalPokedexNum\[NUM_SPECIES - 1\]\s*=\s*\{(.*?)\};",
        pokemon_c, re.S)
    if not m:
        raise SystemExit("sSpeciesToNationalPokedexNum not found in pokemon.c")
    entries = re.findall(r"SPECIES_TO_NATIONAL\((\w+)\)", m.group(1))
    if len(entries) != 411:
        raise SystemExit(f"expected 411 entries, got {len(entries)}")
    i2n, n2i = {}, {}
    for index, const in enumerate(entries, 1):          # internal id = array position + 1
        national = national_enum.get(f"NATIONAL_DEX_{const}")
        if national is None or not 1 <= national <= 386:
            continue                                    # old-Unown slots / no national number
        i2n[index] = national
        n2i.setdefault(national, index)
    if len(i2n) != 386:
        raise SystemExit(f"expected 386 mappable species, got {len(i2n)}")
    return i2n, n2i


def parse_species_info(text: str, types: dict, abilities: dict,
                       growth: dict, mon_consts: dict, item_ids: dict) -> dict:
    """{SPECIES_NAME: {...}} from gSpeciesInfo designated initializers."""
    out = {}
    pattern = re.compile(r"\[SPECIES_(\w+)\]\s*=\s*\{((?:[^{}]|\{[^{}]*\})*)\}", re.S)
    for m in pattern.finditer(text):
        name, body = m.groups()
        if name.startswith("OLD_UNOWN") or name in ("NONE", "EGG"):
            continue
        stats = {key: int(val)
                 for key, val in re.findall(r"\.(baseHP|baseAttack|baseDefense|baseSpeed|"
                                            r"baseSpAttack|baseSpDefense|catchRate|friendship)"
                                            r"\s*=\s*(\d+)", body)}
        types_m = re.search(r"\.types\s*=\s*\{(\w+),\s*(\w+)\}", body)
        abil_m = re.search(r"\.abilities\s*=\s*\{(\w+),\s*(\w+)\}", body)
        gender_m = re.search(r"\.genderRatio\s*=\s*([\w().]+)", body)
        growth_m = re.search(r"\.growthRate\s*=\s*(\w+)", body)
        if not all((types_m, abil_m, gender_m, growth_m, len(stats) == 8)):
            raise SystemExit(f"could not fully parse species info for {name}")

        def resolve_gender(expr):
            if expr == "MON_MALE":
                return 0
            if expr == "MON_FEMALE":
                return 254
            if expr == "MON_GENDERLESS":
                return 255
            p = re.match(r"PERCENT_FEMALE\(([\d.]+)\)", expr)
            if p:
                return min(254, int(float(p.group(1)) * 255 / 100))
            return int(expr)

        abilities_used = []
        for a in abil_m.groups():
            if a != "ABILITY_NONE":
                abilities_used.append({"id": abilities[a], "name": ability_display(a)})
        raw_types = [t for t in types_m.groups()]
        # Gen III stores mono-types as {X, X}; collapse to a unique list.
        type_list = [raw_types[0]] if raw_types[1] == raw_types[0] else raw_types
        out[name] = {
            "base_stats": {
                "hp": stats["baseHP"], "atk": stats["baseAttack"], "def": stats["baseDefense"],
                "spe": stats["baseSpeed"], "spa": stats["baseSpAttack"],
                "spd": stats["baseSpDefense"],
            },
            "types": [{"id": types[t], "name": t.removeprefix("TYPE_")} for t in type_list],
            "gender_ratio": resolve_gender(gender_m.group(1)),
            "growth": growth[growth_m.group(1)],
            "abilities": abilities_used,
            "friendship": stats["friendship"],
            "catch_rate": stats["catchRate"],
        }
    return out


def parse_move_names(text: str) -> dict:
    """{MOVE_NAME: in-game string} from gMoveNames."""
    out = {}
    for m in re.finditer(r"\[(MOVE_\w+)\]\s*=\s*_\(\"([^\"]+)\"\)", text):
        out[m.group(1)] = m.group(2)
    return out


def parse_map_sections(text: str) -> dict:
    """{met-location index: display name} for FRLG (Kanto/Sevii named sections only)."""
    data = json.loads(text)
    out = {}
    for index, section in enumerate(data["map_sections"]):
        if section.get("name"):
            out[index] = section["name"].title()
    out.update(SPECIAL_MET_LOCATIONS)
    return out


def species_display(const: str) -> str:
    return SPECIES_DISPLAY_OVERRIDES.get(const, const.replace("_", " ").title())


def item_display(const: str) -> str:
    if const in ITEM_DISPLAY_OVERRIDES:
        return ITEM_DISPLAY_OVERRIDES[const]
    if m := re.fullmatch(r"(TM|HM)(\d+)", const):
        return f"{m.group(1)}{m.group(2)}"
    return const.replace("_", " ").title()


def ability_display(const: str) -> str:
    return const.removeprefix("ABILITY_").replace("_", " ").title()


def move_display(in_game: str) -> str:
    if in_game == "-$$$$$$":
        return "—"
    return in_game.title()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="webapp/data/gen3.json")
    ap.add_argument("--commit", default=COMMIT,
                    help="pokefirered commit to fetch (default: pinned)")
    args = ap.parse_args()
    global BASE  # noqa: PLW0603
    BASE = f"https://raw.githubusercontent.com/pret/pokefirered/{args.commit}"

    cache = Path(tempfile.gettempdir()) / f"frlgweb-decomp-{args.commit[:12]}"
    print(f"fetching decomp data (cache: {cache})")
    src = {local: fetch(cache, local) for local in FILES}

    species = parse_defines(src["species.h"], "SPECIES_")
    items = parse_defines(src["items.h"], "ITEM_")
    moves = parse_defines(src["moves.h"], "MOVE_")
    abilities = parse_defines(src["abilities.h"], "ABILITY_")
    constants = parse_defines(src["pokemon_constants.h"], "")
    national_enum = parse_first_enum(src["pokedex.h"])
    i2n, n2i = parse_species_to_national(src["pokemon.c"], national_enum)
    info = parse_species_info(src["species_info.h"], constants, abilities,
                              constants, constants, items)
    move_names = parse_move_names(src["move_names.h"])
    met_locations = parse_map_sections(src["region_map_sections.json"])

    dex = []
    for national in range(1, 387):
        internal = n2i[national]
        const = next(c for c, v in species.items() if v == internal)
        si = info[const.removeprefix("SPECIES_")]
        dex.append({
            "national": national,
            "internal": internal,
            "const": const.removeprefix("SPECIES_"),
            "name": species_display(const.removeprefix("SPECIES_")),
            "sprite": f"sprites/{national:03d}.png",
            **si,
        })

    species_out = {}
    for national, entry in enumerate(dex, 1):
        species_out[national] = {
            "internal": entry["internal"], "const": entry["const"], "name": entry["name"],
            "types": [t["name"] for t in entry["types"]],
            "gender_ratio": entry["gender_ratio"], "growth": entry["growth"],
            "abilities": [a["name"] for a in entry["abilities"]],
            "base_stats": entry["base_stats"],
            "friendship": entry["friendship"], "catch_rate": entry["catch_rate"],
            "sprite": entry["sprite"],
        }
    # fallback lookups for internal ids outside the National Dex (bad eggs, etc.)
    internal_names = {v: c for c, v in species.items()}
    for internal, national in sorted(i2n.items()):
        pass  # keep static analysers calm; i2n/n2i are serialized below

    item_out = {0: {"name": "—", "display": "—"}}
    for const, value in items.items():
        if const == "ITEM_NONE":
            continue
        short = const.removeprefix("ITEM_")
        item_out[value] = {"name": short, "display": item_display(short)}
    move_out = {0: {"name": "—", "display": "—"}}
    for const, value in moves.items():
        if const == "MOVE_NONE":
            continue
        in_game = move_names.get(const, const.removeprefix("MOVE_").replace("_", " "))
        move_out[value] = {"name": const.removeprefix("MOVE_"), "display": move_display(in_game)}
    ability_out = {0: "—"}
    for const, value in abilities.items():
        if const == "ABILITY_NONE":
            continue
        ability_out[value] = ability_display(const)
    type_out = {v: k.removeprefix("TYPE_") for k, v in constants.items()
                if k.startswith("TYPE_") and v <= 17}

    out = {
        "meta": {
            "source": "pret/pokefirered",
            "commit": args.commit,
            "generator": "tools/build_metadata.py",
        },
        "species": species_out,                      # keyed by national dex number (1..386)
        "internal_to_national": {str(k): v for k, v in i2n.items()},
        "national_to_internal": {str(k): v for k, v in n2i.items()},
        "internal_names": {str(k): v for k, v in internal_names.items()},
        "items": item_out,
        "moves": move_out,
        "abilities": ability_out,
        "types": type_out,
        "balls": {i: name for i, name in enumerate(BALLS)},
        "games": {str(k): v for k, v in GAMES.items()},
        "natures": {i: name for i, name in enumerate(NATURES)},
        "languages": {str(k): v for k, v in LANGUAGES.items()},
        "met_locations": {str(k): v for k, v in sorted(met_locations.items())},
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out_path} ({out_path.stat().st_size} bytes, "
          f"{len(dex)} species, {len(item_out)} items, {len(move_out)} moves)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
