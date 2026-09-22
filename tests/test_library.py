"""Tests for the library indexer: recursive scan, stable ids, dedupe, error
classification, and the "never accept a filesystem path" rule."""

from __future__ import annotations

import hashlib

from webapp.library import ID_RE, Library


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_index_same_species_multiple_files(web_config, meta, make_pk3, add_file):
    _, id_a = add_file("a.pk3", make_pk3(species=25))
    _, id_b = add_file("b.pk3", make_pk3(species=25, pid=0x11112222))
    add_file("c.pk3", make_pk3(species=6, pid=0x22223333))
    lib = Library(web_config.library.path, meta)
    stats = lib.scan()
    assert stats.files == 3 and stats.entries == 3 and stats.valid == 3
    assert len(lib.by_national(25)) == 2
    assert len(lib.by_national(6)) == 1
    assert lib.counts_by_national() == {25: (2, 0), 6: (1, 0)}
    assert lib.get(id_a).tradeable and lib.get(id_b).tradeable


def test_recursive_scan(web_config, meta, make_pk3, add_file):
    add_file("deep.pk3", make_pk3(species=25), subdir="nested/deeper")
    lib = Library(web_config.library.path, meta)
    stats = lib.scan()
    assert stats.files == 1
    entry = lib.by_national(25)[0]
    assert entry.relpath == "nested/deeper/deep.pk3"


def test_case_insensitive_extensions(web_config, meta, make_pk3, add_file):
    add_file("UPPER.PK3", make_pk3(species=25))
    add_file("Mixed.Ek3", make_pk3(species=6, pid=0xDEAD0001))
    lib = Library(web_config.library.path, meta)
    stats = lib.scan()
    assert stats.files == 2 and stats.entries == 2


def test_duplicate_content_deduplicated(web_config, meta, make_pk3, add_file):
    data = make_pk3(species=25)
    _, id_1 = add_file("one.pk3", data)
    _, id_2 = add_file("two.pk3", data)          # identical bytes
    lib = Library(web_config.library.path, meta)
    stats = lib.scan()
    assert stats.files == 2 and stats.entries == 1 and stats.duplicates == 1
    assert id_1 == id_2
    assert len(lib.by_national(25)) == 1


def test_invalid_size_marked(web_config, meta, add_file):
    add_file("short.pk3", b"\x00" * 79)
    add_file("long.pk3", b"\x00" * 101)
    lib = Library(web_config.library.path, meta)
    stats = lib.scan()
    assert stats.valid == 0 and stats.invalid == 2
    entry = next(iter(lib._entries.values()))
    assert entry.status == "invalid_size" and not entry.tradeable
    assert any(e["error"] == "invalid_size" for e in stats.errors)


def test_checksum_failure_marked(web_config, meta, make_pk3, add_file):
    data = bytearray(make_pk3(species=25))
    data[40] ^= 0xFF                              # corrupt the secure region
    relpath, entry_id = add_file("bad.pk3", bytes(data))
    lib = Library(web_config.library.path, meta)
    lib.scan()
    entry = lib.get(entry_id)
    assert entry.status == "checksum_failure"
    assert not entry.tradeable
    assert entry.to_card()["checksum_ok"] is False


def test_undecodable_marked(web_config, meta, make_pk3, add_file):
    _, entry_id = add_file("unown-gap.pk3", make_pk3(species=252))
    lib = Library(web_config.library.path, meta)
    lib.scan()
    entry = lib.get(entry_id)
    assert entry.status == "undecodable"
    assert not entry.tradeable


def test_shiny_counted(web_config, meta, make_pk3, add_file):
    add_file("plain.pk3", make_pk3(species=25, pid=0x01020304, otid=0x05060708))
    add_file("shiny.pk3", make_pk3(species=25, pid=None, otid=None, shiny=True))
    lib = Library(web_config.library.path, meta)
    lib.scan()
    assert lib.counts_by_national() == {25: (2, 1)}


def test_rescan_picks_up_new_files(web_config, meta, make_pk3, add_file):
    add_file("first.pk3", make_pk3(species=25))
    lib = Library(web_config.library.path, meta)
    lib.scan()
    assert lib.stats().files == 1
    add_file("second.pk3", make_pk3(species=6))
    lib.scan()
    assert lib.stats().files == 2
    assert lib.by_national(6)


def test_ids_stable_across_rescans(web_config, meta, make_pk3, add_file):
    data = make_pk3(species=25)
    _, entry_id = add_file("mon.pk3", data)
    lib = Library(web_config.library.path, meta)
    lib.scan()
    first = lib.get(entry_id)
    lib.scan()
    second = lib.get(entry_id)
    assert first.id == second.id == _sha(data)


def test_path_traversal_never_resolves(web_config, meta, make_pk3, add_file):
    add_file("mon.pk3", make_pk3(species=25))
    lib = Library(web_config.library.path, meta)
    lib.scan()
    for evil in ("../../etc/passwd", "/etc/passwd", "..%2f..%2fetc",
                 "mon.pk3", "sub/../../mon.pk3", "A" * 63, "A" * 65, "g" * 64):
        assert lib.get(evil) is None, evil
    assert ID_RE.match("a" * 64)
