"""Build synthetic Gen III Pokémon bytes using ONLY the existing frlgsim primitives.

Two use cases:

1. The live trade CLI needs a filler party member: the real game's
   ``CheckValidityOfTradeMons`` refuses a trainer's LAST alive Pokémon, so a
   single-file party would be rejected by the host. The web app therefore always
   offers the selected Pokémon in slot 0 backed by a valid filler in slot 1 - the
   minimum arrangement, with the selected file itself untouched.

2. ``--dry-run`` mode fabricates a "received" Pokémon so the whole UI/trade
   lifecycle can be developed without a Switch.

Construction mirrors what PKHeX would write: a canonical DECRYPTED 100-byte struct
(header + G/A/E/M substructs, checksum over the decrypted region, party tail derived
by ``frlgsim.stats.build_party_tail``), then encrypted/shuffled to the wire form by
``frlgsim.mon.to_encrypted``. No offsets are guessed: the layout is the one already
encoded in ``frlgsim.mon`` / documented against include/pokemon.h (pret/pokefirered).
"""

from __future__ import annotations

import secrets

from frlgsim import charmap, mon as monmod, stats as statsmod

# The filler is cosmetic (it is never offered): a low-level, common species.
FILLER_SPECIES = 16  # PIDGEY
FILLER_LEVEL = 5
FILLER_NICKNAME = "PARTNER"
FILLER_OT = "EMU"


def build_mon(species: int, *, level: int = FILLER_LEVEL, pid: int | None = None,
              otid: int | None = None, nickname: str | None = None,
              ot_name: str = FILLER_OT, held_item: int = 0,
              moves: tuple[int, int, int, int] = (0, 0, 0, 0),
              ivs: tuple[int, int, int, int, int, int] = (15, 15, 15, 15, 15, 15),
              evs: tuple[int, int, int, int, int, int] = (0, 0, 0, 0, 0, 0),
              friendship: int = 70, ability_slot: int = 0, shiny: bool = False,
              pokerus: int = 0, met_location: int = 0x59,  # MAPSEC_ROUTE1 area
              met_game: int = 3, ball: int = 3, ot_gender: int = 0,
              nickname_override: str | None = None) -> bytes:
    """Return a DECRYPTED canonical 100-byte .pk3 for `species`.

    The personality is random (and never equals the trainer id, so the two file
    formats stay distinguishable); ``shiny=True`` retries until the PID satisfies
    the Gen III shiny formula."""
    if not 1 <= species <= 411:  # SPECIES_EGG-1: internal ids with base stats
        raise ValueError(f"species out of range: {species}")
    if not 1 <= level <= 100:
        raise ValueError("level must be 1..100")
    growth_rate = statsmod.basestats.BASE_STATS.get(species, (0,))[6:7] or [3]
    growth_rate = growth_rate[0] if growth_rate else 3
    base_iv_word = sum(max(0, min(31, iv)) << (5 * i) for i, iv in enumerate(ivs))

    provided_pid = pid
    provided_otid = otid
    while True:
        if provided_pid is None:
            pid = secrets.randbelow(0xFFFFFFFF)
        if provided_otid is None:
            tid = secrets.randbelow(0x10000)
            sid = secrets.randbelow(0x10000)
            otid = (sid << 16) | tid
        else:
            tid = otid & 0xFFFF
            sid = (otid >> 16) & 0xFFFF
        if pid == otid:
            pid = pid ^ 1  # keep the wire/decrypted forms distinguishable
        shiny_value = ((tid ^ sid ^ (pid >> 16) ^ (pid & 0xFFFF)) & 0xFFFF)
        if shiny == (shiny_value < 8):
            break
        if provided_pid is not None or provided_otid is not None:
            raise ValueError("explicit pid/otid does not satisfy the shiny/plain request")

    nickname = nickname_override if nickname_override is not None else nickname
    out = bytearray(100)
    out[0:4] = pid.to_bytes(4, "little")
    out[4:8] = otid.to_bytes(4, "little")
    out[8:18] = charmap.encode(nickname or "POKEMON", width=10)
    out[18] = 2                      # language: English
    out[19] = 0x02                   # hasSpecies
    out[20:27] = charmap.encode(ot_name, width=7)
    # Growth substruct (canonical position 0)
    out[32:34] = species.to_bytes(2, "little")
    out[34:36] = held_item.to_bytes(2, "little")
    out[36:40] = statsmod.EXP_TABLES[growth_rate][level].to_bytes(4, "little")
    out[40] = 0                      # ppBonuses
    out[41] = max(0, min(255, friendship))
    # Attacks substruct (canonical position 1)
    for i, move in enumerate(moves):
        out[44 + i * 2:46 + i * 2] = (move & 0xFFFF).to_bytes(2, "little")
    # EVs substruct (canonical position 2)
    out[56:62] = bytes(max(0, min(255, ev)) for ev in evs)
    # Misc substruct (canonical position 3)
    out[68] = pokerus & 0xFF
    out[69] = met_location & 0xFF
    origins = ((met_game & 0x7) | ((ball & 0xF) << 3) | ((ot_gender & 1) << 7)
               | ((level & 0x7F) << 8))
    out[70:72] = origins.to_bytes(2, "little")
    out[72:76] = (base_iv_word | ((ability_slot & 1) << 31)).to_bytes(4, "little")
    # Checksum over the decrypted 48-byte secure region (PKHeX .pk3 convention).
    checksum = sum(int.from_bytes(out[32 + i * 2:34 + i * 2], "little")
                   for i in range(24)) & 0xFFFF
    out[28:30] = checksum.to_bytes(2, "little")
    # Party tail: level + stats derived from the box data (existing frlgsim code).
    tail = statsmod.build_party_tail(bytes(out))
    if tail is not None:
        out[80:100] = tail
    else:  # pragma: no cover - only for species without base stats
        out[84] = level
    assert monmod.decode_file(bytes(out))["checksum_ok"], "filler checksum must validate"
    return bytes(out)


def build_filler_mon() -> bytes:
    """The filler that backs up the selected Pokémon in slot 1."""
    return build_mon(FILLER_SPECIES, level=FILLER_LEVEL, nickname=FILLER_NICKNAME,
                     ot_name=FILLER_OT, moves=(16, 33, 0, 0))  # GUST, TACKLE


def save_filler(path: str) -> str:
    """Write the filler .pk3 to `path` (used once per live trade)."""
    with open(path, "wb") as fh:
        fh.write(build_filler_mon())
    return path
