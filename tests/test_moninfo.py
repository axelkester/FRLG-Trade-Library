"""Tests for the extended Gen III decoder (frlgsim.mon) and webapp.moninfo.

Covers 80-byte box, 100-byte party, .ek3 wire form, checksum validation, all the
newly exposed fields (TID/SID, IVs, EVs, friendship, ability slot, nature, shiny,
Pokérus, met location, ball, origin game, language) and the "original bytes are
never modified" guarantee.
"""

from __future__ import annotations

from frlgsim import mon as monmod

from webapp import moninfo
from webapp.metadata import GENDERLESS, MON_FEMALE


def _flip(data: bytes, offset: int) -> bytes:
    out = bytearray(data)
    out[offset] ^= 0xFF
    return bytes(out)


# ---- sizes / formats ---------------------------------------------------------
def test_decode_100_byte_pk3(make_pk3, meta):
    pk3 = make_pk3()
    d = monmod.decode_file(pk3)
    assert d is not None and d["checksum_ok"]
    assert d["is_ek3"] is False and d["size"] == 100
    info = moninfo.decode_entry(pk3, meta)
    assert info["status"] == "ok" and info["variant"] == "party"
    assert info["format"] == "pk3"


def test_decode_80_byte_box_pk3(make_pk3, meta):
    box = make_pk3(level=50)[:80]
    d = monmod.decode_file(box)
    assert d is not None and d["checksum_ok"] and d["size"] == 80
    assert d["level"] is None and d["level_derived"] == 50
    info = moninfo.decode_entry(box, meta)
    assert info["variant"] == "box" and info["level"] == 50


def test_decode_ek3_wire_form(make_pk3, meta):
    ek3 = monmod.to_encrypted(make_pk3())
    d = monmod.decode_file(ek3)
    assert d["checksum_ok"] and d["is_ek3"] is True
    assert moninfo.decode_entry(ek3, meta)["format"] == "ek3"


def test_checksum_failure(make_pk3, meta):
    # Flip one byte INSIDE the secure region: the header stays intact but the
    # checksum no longer matches.
    bad = _flip(make_pk3(), 40)
    d = monmod.decode_file(bad)
    assert d is not None and d["checksum_ok"] is False
    info = moninfo.decode_entry(bad, meta)
    assert info["status"] == "checksum_failure" and info["checksum_ok"] is False


def test_invalid_sizes(meta):
    for size in (0, 1, 79, 81, 99, 101):
        info = moninfo.decode_entry(b"\x00" * size, meta)
        assert info["error"] == "invalid_size" and info["decodable"] is False


def test_undecodable_unknown_species(make_pk3, meta):
    # Internal id 252 is the OLD_UNOWN_B gap: not a National Pokédex species.
    weird = make_pk3(species=252)
    d = monmod.decode_file(weird)
    assert d["checksum_ok"]
    info = moninfo.decode_entry(weird, meta)
    assert info["error"] == "undecodable" and info["decodable"] is False


# ---- extended fields ----------------------------------------------------------
def test_extended_fields(make_pk3, meta):
    info = moninfo.decode_entry(make_pk3(
        species=25, pid=0x12345678, otid=0x9ABCDEFF, level=50,
        nickname="PIKACHU", ot_name="RED", moves=(84, 85, 86, 87),
        evs=(10, 20, 30, 40, 50, 60), ivs=(31, 30, 29, 28, 27, 26),
        friendship=70, held_item=13, pokerus=0x34, met_location=0x59,
        met_game=3, ball=3, ot_gender=0), meta)
    assert info["species"] == 25
    assert info["species_national"] == 25
    assert info["species_name"] == "Pikachu"
    assert info["nickname"] == "PIKACHU" and info["ot_name"] == "RED"
    assert info["tid"] == 0xDEFF and info["sid"] == 0x9ABC
    assert info["ivs"] == [31, 30, 29, 28, 27, 26]
    assert info["evs"] == [10, 20, 30, 40, 50, 60]
    assert info["friendship"] == 70
    assert info["ability_slot"] == 0 and info["ability_name"] == "Static"
    assert info["nature_name"] == "Gentle"          # 0x12345678 % 25 == 21
    assert info["shiny"] is False
    assert info["pokerus"] == {"value": 0x34, "days": 3, "strain": 4,
                               "infected": True}
    assert info["met_location"] == {"id": 0x59, "name": "Viridian City"}
    assert info["ball"] == {"id": 3, "name": "Poké Ball"}
    assert info["met_game"] == {"id": 3, "name": "FireRed"}
    assert info["met_level"] == 50
    assert info["language"] == {"id": 2, "name": "English"}
    assert info["held_item"] == {"id": 13, "name": "Potion"}
    assert [m["name"] for m in info["moves"]] == ["Thundershock", "Thunderbolt",
                                                  "Thunder Wave", "Thunder"]


def test_level_derived_for_box(make_pk3, meta):
    box = make_pk3(level=32)[:80]
    info = moninfo.decode_entry(box, meta)
    assert info["level"] == 32 and info["level_stored"] is None


def test_shiny_detection(make_pk3, meta):
    tid, sid = 0x1234, 0x5678
    pid = next(p for p in range(100_000)
               if ((tid ^ sid ^ (p >> 16) ^ (p & 0xFFFF)) & 0xFFFF) < 8)
    info = moninfo.decode_entry(
        make_pk3(pid=pid, otid=(sid << 16) | tid, shiny=True), meta)
    assert info["shiny"] is True
    plain = moninfo.decode_entry(
        make_pk3(pid=pid + 8, otid=(sid << 16) | tid, shiny=False), meta)
    assert plain["shiny"] is False


def test_gender_from_pid(meta):
    assert meta.gender_from_pid(0x00, 31) == "female"     # below the 12.5% cut
    assert meta.gender_from_pid(0x1E, 31) == "female"     # 30 < 31 -> female
    assert meta.gender_from_pid(0x1F, 31) == "male"       # 31 is NOT < 31
    assert meta.gender_from_pid(0x20, 31) == "male"
    assert meta.gender_from_pid(0xFF, 0) == "male"        # male-only species
    assert meta.gender_from_pid(0x00, MON_FEMALE) == "female"
    assert meta.gender_from_pid(0xAB, GENDERLESS) == "genderless"


def test_original_bytes_never_modified(make_pk3, meta):
    original = bytearray(make_pk3())
    snapshot = bytes(original)
    moninfo.decode_entry(bytes(original), meta)
    monmod.decode_file(bytes(original))
    monmod.to_encrypted(bytes(original))
    assert bytes(original) == snapshot


# ---- existing Mon API still works ---------------------------------------------
def test_mon_class_backwards_compatible(make_pk3):
    mon = monmod.Mon.from_pk3(make_pk3())
    assert mon.checksum_ok and mon.species == 25 and mon.species_name
    assert len(mon.party_bytes()) == 100 and len(mon.box_bytes()) == 80
    assert "lv=" in mon.describe()


def test_decode_mon_still_has_legacy_keys(make_pk3):
    d = monmod.decode_file(make_pk3())
    for key in ("pid", "otid", "nickname", "otName", "language", "checksum_ok",
                "stored", "calc", "species", "species_name", "heldItem", "exp",
                "moves", "level"):
        assert key in d, key
