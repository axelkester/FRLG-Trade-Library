## Web application (Pokédex trade library)

The repository now ships a self-hosted web UI that turns the project into a visual
Gen III Pokémon trade library: browse the complete National Pokédex (#001-#386),
inspect every `.pk3`/`.ek3` in a library folder, and send the exact selected
Pokémon to the Switch. The web app is a **frontend/controller for the existing
`frlgtrade.py` implementation** - it never reimplements LDN, and the existing CLI
keeps working exactly as before.

Stack: Python 3, FastAPI, Uvicorn, Jinja2 + vanilla JS/CSS, Server-Sent Events.
No React/Node, no runtime network dependency (all Gen III metadata is vendored in
`webapp/data/gen3.json`, generated once from the [pret/pokefirered](https://github.com/pret/pokefirered) decompilation).

## Installation

```bash
# 1. the existing CLI dependencies (ldn, pycryptodome, trio, zstandard)
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt

# 2. the web application dependencies
./venv/bin/pip install -r requirements-web.txt

# 3. optional: one-time download of the Gen III sprites (Nintendo's IP - personal
#    use only; without them the UI shows a generated placeholder)
./venv/bin/python tools/fetch_sprites.py
```

(If the vendored metadata should ever be regenerated - not needed to run anything:
`python3 tools/build_metadata.py`. It downloads a few files from the pinned
pret/pokefirered commit and rewrites `webapp/data/gen3.json`.)

## Configuration

Everything lives in `config.toml` (repository root). The shipped file documents
every option; the defaults:

```toml
[app]
host = "127.0.0.1"     # "0.0.0.0" to reach the UI from other LAN devices
port = 8000

[library]
path = "./pokemon_library"      # scanned recursively for *.pk3 / *.ek3
received_path = "./received"     # received Pokémon (never overwritten)
auto_add_received = false        # else use the "Add to library" button

[trade]
phy = "phy1"
keys = "~/.switch/prod.keys"
python = "./bin/python"          # interpreter with the requirements installed
idle_reset_seconds = 15          # auto-return FAILED/CANCELLED/COMPLETED to IDLE
# password / comm_id / ot / version / timeout / prefix / verbose / dry_run …
```

The browser can never influence any of this: it only sends indexed Pokémon ids
(SHA-256 of the file contents), never filesystem paths or shell arguments.
`prod.keys` contents are never shown in the UI or logs.

## Launch

```bash
# UI development / everything except a real trade (no Switch, no root):
./venv/bin/python -m webapp --dry-run

# Real trades (the existing LDN transport needs elevated network privileges).
# Simplest working method for development: run the whole app elevated.
sudo -E ./venv/bin/python -m webapp
```

Then open <http://127.0.0.1:8000> (or the configured host:port).

**Privilege isolation.** The web app itself does not need root - only the spawned
`frlgtrade.py` child does (raw packet injection on the Wi-Fi phy). Options:

1. *Development (simplest):* run the whole launcher with `sudo -E` (above).
2. *Narrow worker:* instead of running the app as root, grant the *interpreter
   only* the two capabilities the LDN stack needs and keep the app unprivileged:
   `sudo setcap cap_net_raw,cap_net_admin+eip ./venv/bin/python`.
   (Interface must still be unmanaged by NetworkManager - see above.)
3. *argv prefix:* `[trade].prefix = ["sudo", "-E"]` makes the app itself run
   unprivileged and only the child elevated; the browser can never change the
   prefix. Requires a passwordless sudo rule for `frlgtrade.py`.

Do not weaken the host globally (e.g. disabling firewalls or running the app as
root on a public interface) just to make the UI work.

## Workflow: opening the page to a completed Switch trade

1. Put your `.pk3`/`.ek3` files into `pokemon_library/` (any nesting).
2. Start the app (`sudo -E ./venv/bin/python -m webapp`) and open the page.
3. Browse/search the 386-species grid; species without files are grayed out with
   "No PK3 available". Click a species to see every library instance of it.
4. Click **Send to Switch** on the instance you want to trade away.
5. On the Switch: Direct Corner → trade room → **Leader**. Accept the join from
   the simulated trainer (default name "EMU"), walk to the left chair, select a
   Pokémon, and accept the trade confirmation (same steps as the CLI README).
6. Watch the state machine in the side panel: SCANNING → JOINING → CONNECTED →
   TRADING → COMPLETED, with the live `frlgtrade.py` log streamed underneath.
7. The received Pokémon lands in `received/` with a collision-safe name; its
   sprite and full details appear in the panel, the trade is added to the
   **History** view, and **Add to library** copies it into the library.

A **Cancel** button terminates the child gracefully (SIGINT → SIGTERM → SIGKILL
against its process group); only one trade runs at a time; a PID file reaps any
orphaned `frlgtrade.py` left behind if the web app is killed.

## Tests

```bash
./venv/bin/python -m pytest tests/        # 123 tests, no Switch required
```

Covers the extended PK3/EK3 decoder (80/100-byte, checksums, TID/SID, IVs/EVs,
friendship, ability, nature, shiny, Pokérus, met data, ball, origin game), the
internal-species→National-Dex mapping (internal ids are NOT dex numbers after
Celebi), item/move tables, library indexing (duplicates, corrupt files, path
traversal), the whole HTTP API, trade argv construction (validated against the
real `frlgtrade.py` parser), one-trade-at-a-time locking, and the dry-run
lifecycle. The existing CLI surface is pinned by compatibility tests.

## Web app limitations

- The live LDN path itself is unchanged and still needs Linux + root/capabilities
  + a compatible Wi-Fi card; `--dry-run` fakes the entire trade for UI work.
- Met locations: FRLG stores a map-section index; names come from the decomp's
  region-map data (Kanto/Sevii sections + the documented 0xFC..0xFE sentinels).
- A Pokémon whose personality equals its OT id (XOR key 0) makes `.pk3`/`.ek3`
  indistinguishable - the same documented limitation as `Mon.from_pk3`.
- Sprites are opt-in (see `tools/fetch_sprites.py`); the UI placeholder is used
  otherwise. No runtime downloads, no hotlinking.
- An abrupt `kill -9` of the web app cannot run cleanup code; the next start
  reaps the orphan via the PID file.

## Credits
- [kinnay](https://github.com/kinnay) - For the [LDN library](https://github.com/kinnay/LDN) this is built upon, and the excellent [NintendoClients Wiki](https://github.com/kinnay/NintendoClients/wiki)
- [pokefirered](https://github.com/pret/pokefirered) - A full decompilation of FireRed/LeafGreen, including the Switch port. It served as an important reference.
- [tornadus](https://github.com/tornadus/), [trowgundam](https://github.com/trowgundam), [MercuryEnigma](https://github.com/MercuryEnigma) - For the [frlg-ldn-trade](https://github.com/tornadus/frlg-ldn-trade) project.
- Deepseek!

## License
AGPLv3
