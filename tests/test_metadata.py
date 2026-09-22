"""Tests for the vendored Gen III metadata (generated from pret/pokefirered).

The critical property under test: internal species ids are NOT National Dex
numbers after Celebi (e.g. internal 411 is Chimecho, National #358).
"""

from __future__ import annotations

import pytest


def test_complete_national_dex(meta):
    assert meta.dex_count() == 386
    dex = meta.dex_list()
    assert [e["national"] for e in dex] == list(range(1, 387))
    assert dex[0]["name"] == "Bulbasaur"
    assert dex[-1]["name"] == "Deoxys"


# ---- internal species id <-> National Dex mapping ------------------------------
@pytest.mark.parametrize("national,internal,name", [
    (1, 1, "Bulbasaur"),
    (151, 151, "Mew"),
    (251, 251, "Celebi"),
    (252, 277, "Treecko"),      # the OLD_UNOWN gap: Hoenn starts at internal 277
    (286, 307, "Breloom"),
    (313, 386, "Volbeat"),
    (314, 387, "Illumise"),
    (358, 411, "Chimecho"),
    (369, 381, "Relicanth"),
    (385, 409, "Jirachi"),
    (386, 410, "Deoxys"),
])
def test_internal_to_national(meta, national, internal, name):
    assert meta.national_for_internal(internal) == national
    assert meta.internal_for_national(national) == internal
    assert meta.species_entry(national)["internal"] == internal
    assert meta.species_name(national) == name


def test_gap_species_have_no_national_number(meta):
    # 252..276 = SPECIES_OLD_UNOWN_B..Z: internal ids, but no National Dex entry.
    for internal in range(252, 277):
        assert meta.national_for_internal(internal) is None
    assert meta.national_for_internal(0) is None       # SPECIES_NONE
    assert meta.national_for_internal(412) is None     # SPECIES_EGG


def test_mapping_is_a_bijection(meta):
    assert len(meta.internal_to_national) == 386
    assert len(meta.national_to_internal) == 386


# ---- type data ------------------------------------------------------------------
@pytest.mark.parametrize("national,types", [
    (1, ["GRASS", "POISON"]),
    (4, ["FIRE"]),                  # mono-type collapses to one entry
    (81, ["ELECTRIC", "STEEL"]),    # Gen III Magnemite
    (386, ["PSYCHIC"]),
])
def test_species_types(meta, national, types):
    assert meta.species_types(national) == types


# ---- item / move / ability names ------------------------------------------------
@pytest.mark.parametrize("item_id,expected", [
    (0, "—"), (1, "Master Ball"), (13, "Potion"), (68, "Rare Candy"),
    (196, "Focus Band"),
])
def test_item_names(meta, item_id, expected):
    assert meta.item_name(item_id) == expected


@pytest.mark.parametrize("move_id,expected", [
    (0, "—"), (1, "Pound"), (94, "Psychic"), (354, "Psycho Boost"),
])
def test_move_names(meta, move_id, expected):
    assert meta.move_name(move_id) == expected


def test_unknown_ids_get_fallbacks(meta):
    assert meta.item_name(9999) == "Item #9999"
    assert meta.move_name(9999) == "Move #9999"
    assert meta.ball_name(99) == "Ball #99"
    assert meta.game_name(99) == "Game #99"
    assert meta.met_location_name(200) == "Location #200"   # MAPSEC_NONE gap


# ---- balls / games / languages / natures / met locations -------------------------
def test_balls(meta):
    assert meta.ball_name(0) == "Master Ball"
    assert meta.ball_name(3) == "Poké Ball"
    assert meta.ball_name(11) == "Premier Ball"


def test_origin_games(meta):
    assert meta.game_name(0) == "Sapphire"
    assert meta.game_name(3) == "FireRed"
    assert meta.game_name(4) == "LeafGreen"
    assert meta.game_name(15) == "Colosseum/XD"


def test_languages(meta):
    assert meta.language_name(2) == "English"
    assert meta.language_name(1) == "Japanese"


def test_natures(meta):
    assert len(meta.natures) == 25
    assert meta.nature_name(0) == "Hardy"
    assert meta.nature_name(24) == "Quirky"


def test_met_locations(meta):
    assert meta.met_location_name(88) == "Pallet Town"
    assert meta.met_location_name(89) == "Viridian City"
    assert meta.met_location_name(196) == "Celadon Dept."
    # documented FRLG sentinels (cross-checked with PKHeX's Gen 3 table)
    assert meta.met_location_name(252) == "Gift Egg"
    assert meta.met_location_name(253) == "In-game Trade"
    assert meta.met_location_name(254) == "Fateful Encounter"


# ---- gender ratios ----------------------------------------------------------------
def test_gender_ratios(meta):
    assert meta.species_entry(1)["gender_ratio"] == 31       # 12.5% female
    assert meta.species_entry(29)["gender_ratio"] == 254     # Nidoran♀
    assert meta.species_entry(32)["gender_ratio"] == 0       # Nidoran♂
    assert meta.species_entry(151)["gender_ratio"] == 255    # genderless Mew
    assert meta.species_entry(313)["gender_ratio"] == 0      # male-only Volbeat
    assert meta.species_entry(314)["gender_ratio"] == 254    # female-only Illumise
