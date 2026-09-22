"""API tests against the FastAPI app (in-process, dry-run, no Switch)."""

from __future__ import annotations

import time

import pytest


# ---- Pokédex endpoints -------------------------------------------------------------
def test_pokedex_complete_and_ordered(client):
    res = client.get("/api/pokedex")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 386
    nationals = [s["national"] for s in data["species"]]
    assert nationals == list(range(1, 387))
    bulbasaur = data["species"][0]
    assert bulbasaur["name"] == "Bulbasaur"
    assert bulbasaur["types"] == ["GRASS", "POISON"]
    assert bulbasaur["count"] == 0 and bulbasaur["available"] is False
    assert bulbasaur["sprite"] == "/static/sprites/001.png"


def test_pokedex_counts_instances(client, web_config, make_pk3, add_file):
    add_file("pika1.pk3", make_pk3(species=25))
    add_file("pika2.pk3", make_pk3(species=25, pid=0x11112222))
    client.post("/api/library/rescan")
    data = client.get("/api/pokedex").json()
    pikachu = next(s for s in data["species"] if s["national"] == 25)
    assert pikachu["count"] == 2 and pikachu["available"] is True
    assert data["species"][5]["count"] == 0       # Charizard: no instances


def test_species_detail_with_instances(client, web_config, make_pk3, add_file):
    _, id_a = add_file("a.pk3", make_pk3(species=25, nickname="SPARKY"))
    _, id_b = add_file("b.pk3", make_pk3(species=25, pid=0x11112222, nickname="ZAP"))
    client.post("/api/library/rescan")
    res = client.get("/api/species/25")
    assert res.status_code == 200
    data = res.json()
    assert data["name"] == "Pikachu" and data["internal"] == 25
    ids = {i["id"] for i in data["instances"]}
    assert ids == {id_a, id_b}
    by_id = {i["id"]: i for i in data["instances"]}
    assert by_id[id_a]["nickname"] == "SPARKY" and by_id[id_a]["tradeable"] is True
    assert by_id[id_b]["nickname"] == "ZAP"


def test_species_out_of_range_404(client):
    assert client.get("/api/species/0").status_code == 404
    assert client.get("/api/species/387").status_code == 404
    assert client.get("/api/species/9999").status_code == 404


def test_pokemon_detail(client, web_config, make_pk3, add_file):
    _, entry_id = add_file("pika.pk3", make_pk3(species=25, level=42))
    client.post("/api/library/rescan")
    res = client.get(f"/api/pokemon/{entry_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["species_name"] == "Pikachu" and data["level"] == 42
    assert data["status"] == "ok" and data["tradeable"] is True
    assert data["id"] == entry_id


def test_pokemon_detail_shows_failures(client, web_config, make_pk3, add_file):
    bad = bytearray(make_pk3(species=25))
    bad[40] ^= 0xFF
    _, entry_id = add_file("bad.pk3", bytes(bad))
    client.post("/api/library/rescan")
    data = client.get(f"/api/pokemon/{entry_id}").json()
    assert data["status"] == "checksum_failure"
    assert data["tradeable"] is False
    assert data["checksum_ok"] is False


# ---- path traversal / hostile ids ----------------------------------------------------
@pytest.mark.parametrize("evil", [
    "..%2f..%2fetc%2fpasswd",
    "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    "..%5c..%5cetc%5cpasswd",
    "/etc/passwd",
    "../../../etc/passwd",
    "a" * 63, "a" * 65, "g" * 64, "A" * 64,
    "mon.pk3", "sub/../../mon.pk3",
])
def test_hostile_ids_never_resolve(client, evil):
    status = client.get(f"/api/pokemon/{evil}").status_code
    assert status in (400, 404, 422)
    status = client.post("/api/trade", json={"id": evil}).status_code
    assert status in (400, 404, 422)


def test_unknown_valid_shaped_id_404(client):
    assert client.get("/api/pokemon/" + "0" * 64).status_code == 404
    assert client.post("/api/trade", json={"id": "0" * 64}).status_code == 404


# ---- library endpoints ----------------------------------------------------------------
def test_library_stats_and_rescan(client, web_config, make_pk3, add_file):
    add_file("one.pk3", make_pk3(species=25))
    res = client.post("/api/library/rescan")
    assert res.status_code == 200
    assert res.json()["valid"] == 1
    data = client.get("/api/library").json()
    assert data["entries"] == 1 and data["root"] == str(web_config.library.path)


# ---- trade lifecycle over HTTP ----------------------------------------------------------
def _wait_terminal(client, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = client.get("/api/state").json()
        if payload["state"] in ("COMPLETED", "FAILED", "CANCELLED"):
            return payload
        time.sleep(0.2)
    raise AssertionError("trade did not reach a terminal state in time")


def test_dry_run_trade_lifecycle(client, web_config, make_pk3, add_file):
    _, entry_id = add_file("pika.pk3", make_pk3(species=25))
    client.post("/api/library/rescan")
    res = client.post("/api/trade", json={"id": entry_id})
    assert res.status_code == 200
    payload = res.json()
    assert payload["state"] == "SCANNING" and payload["active"] is True
    assert payload["current"]["species_name"] == "Pikachu"

    final = _wait_terminal(client)
    assert final["state"] == "COMPLETED"
    assert final["dry_run"] is True
    received = list(web_config.library.received_path.glob("*.pk3"))
    assert len(received) == 1
    result = final["last_result"]
    assert result["state"] == "COMPLETED" and result["received"]["decodable"]

    history = client.get("/api/history").json()["records"]
    assert len(history) == 1
    record_id = history[0]["id"]
    add = client.post(f"/api/history/{record_id}/add-to-library")
    assert add.status_code == 200
    assert (web_config.library.path / add.json()["relpath"]).is_file()
    assert client.post("/api/library/rescan").json()["entries"] >= 2

    # the selected file was never modified
    assert (web_config.library.path / "pika.pk3").read_bytes() == make_pk3(species=25)


def test_trade_bad_instance_400(client, web_config, make_pk3, add_file):
    bad = bytearray(make_pk3(species=25))
    bad[40] ^= 0xFF
    _, entry_id = add_file("bad.pk3", bytes(bad))
    client.post("/api/library/rescan")
    res = client.post("/api/trade", json={"id": entry_id})
    assert res.status_code == 400
    assert client.get("/api/state").json()["state"] == "IDLE"


def test_one_trade_at_a_time_409(client, web_config, make_pk3, add_file):
    _, id_a = add_file("a.pk3", make_pk3(species=25))
    _, id_b = add_file("b.pk3", make_pk3(species=6, pid=0xDEAD0001))
    client.post("/api/library/rescan")
    assert client.post("/api/trade", json={"id": id_a}).status_code == 200
    res = client.post("/api/trade", json={"id": id_b})
    assert res.status_code == 409
    _wait_terminal(client)


def test_cancel_trade(client, web_config, make_pk3, add_file):
    _, entry_id = add_file("a.pk3", make_pk3(species=25))
    client.post("/api/library/rescan")
    client.post("/api/trade", json={"id": entry_id})
    res = client.post("/api/trade/cancel")
    assert res.status_code == 200
    assert res.json()["state"] == "CANCELLED"
    final = _wait_terminal(client)
    assert final["state"] == "CANCELLED"


def test_cancel_when_idle_is_noop(client):
    res = client.post("/api/trade/cancel")
    assert res.status_code == 200
    assert res.json()["state"] == "IDLE"


# NOTE: the SSE route (/api/events) is an infinite stream that TestClient's portal
# cannot consume; its generator is exercised end-to-end in test_trader.py.


def test_history_unknown_record_404(client):
    assert client.post("/api/history/0123456789abcdef/add-to-library").status_code == 404


def test_index_page_renders(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "FRLG Trade Library" in res.text
    assert "/static/app.js" in res.text
