#!/usr/bin/env python3
"""One-shot downloader for Gen III sprites (optional, off by default).

Downloads the Emerald front sprites (the Gen III pixel art) for all 386 species
from the PokeAPI sprites repository into webapp/static/sprites/ so the Pokédex
grid shows real sprites. Without them the UI falls back to a generated placeholder.

The sprites are Nintendo's intellectual property - only fetch them for personal,
non-commercial use of this tool. The application never downloads anything at
runtime and never hotlinks: this script is the ONLY network step, run once, and its
output is cached locally.

Usage: python3 tools/fetch_sprites.py [--out webapp/static/sprites]
"""

import argparse
import ssl
import sys
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/versions/generation-iii/emerald/{national}.png"


def fetch_one(national: int, out: Path, unverified: bool) -> bool:
    target = out / f"{national:03d}.png"
    if target.exists():
        return True
    url = BASE.format(national=national)
    request = urllib.request.Request(url, headers={"User-Agent": "frlg-web-sprites"})
    try:
        response = urllib.request.urlopen(request, timeout=30)  # noqa: S310
        data = response.read()
    except urllib.error.HTTPError:
        return False
    except urllib.error.URLError as exc:
        if not unverified or "CERTIFICATE_VERIFY_FAILED" not in str(exc.reason):
            return False
        context = ssl._create_unverified_context()  # noqa: SLF001
        response = urllib.request.urlopen(request, timeout=30, context=context)
        data = response.read()
    target.write_bytes(data)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="webapp/static/sprites")
    ap.add_argument("--no-unverified", action="store_true",
                    help="do not retry without certificate verification")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ok, missing = 0, 0
    for national in range(1, 387):
        if fetch_one(national, out, unverified=not args.no_unverified):
            ok += 1
        else:
            missing += 1
            print(f"  missing sprite for #{national:03d}", file=sys.stderr)
    print(f"sprites: {ok} written, {missing} missing -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
