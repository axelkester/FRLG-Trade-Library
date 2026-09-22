"""frlg-ldn-trade web application: a visual Gen III Pokémon trade library.

The web application is a frontend/controller for the existing project: it parses
.pk3/.ek3 files with frlgsim, displays the complete National Pokédex with vendored
pret/pokefirered metadata, and sends a selected Pokémon to the Switch by invoking
the existing ``frlgtrade.py`` CLI. It never reimplements LDN.

Launch:  python -m webapp [--config config.toml] [--dry-run] [--host H] [--port P]
"""

__version__ = "0.1.1"
