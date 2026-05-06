#!/usr/bin/env python3
"""Interactive helper to add a journal to journals.json.

Usage:
    python3 add_journal.py

Looks up a journal by ISSN or by name search via Crossref, confirms with you,
then appends to journals.json.  Run build.py afterwards to refresh the page.
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
CONFIG = ROOT / "journals.json"
USER_AGENT = "journal-tracker/1.0 (mailto:daniel@example.com)"


def http_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def search_by_name(query: str) -> list[dict]:
    params = urllib.parse.urlencode({"query": query, "rows": 5})
    url = f"https://api.crossref.org/journals?{params}"
    data = http_get_json(url)
    return data["message"]["items"]


def lookup_by_issn(issn: str) -> dict | None:
    url = f"https://api.crossref.org/journals/{issn}"
    try:
        return http_get_json(url)["message"]
    except urllib.error.HTTPError:
        return None


def pick_issn(item: dict) -> str | None:
    issns = item.get("ISSN", [])
    return issns[0] if issns else None


def guess_homepage(item: dict) -> str:
    # Crossref doesn't reliably give a journal homepage.
    # We'll just point at the Crossref search page; user can edit later.
    issn = pick_issn(item)
    if issn:
        return f"https://search.crossref.org/?q={urllib.parse.quote(item.get('title', ''))}&from_ui=yes"
    return ""


def confirm(prompt: str) -> bool:
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


def main() -> None:
    config = json.loads(CONFIG.read_text())
    print(f"Currently tracking {len(config)} journal(s):")
    for j in config:
        print(f"  - {j['name']} ({j['issn']})")
    print()

    raw = input("Enter ISSN (e.g. 0091-3367) or journal name to search: ").strip()
    if not raw:
        print("Cancelled.")
        return

    item = None
    if "-" in raw and len(raw) <= 9:  # looks like an ISSN
        item = lookup_by_issn(raw)
        if not item:
            print(f"No Crossref journal found for ISSN {raw}.")
            return
    else:
        results = search_by_name(raw)
        if not results:
            print(f"No journals found for '{raw}'.")
            return
        print(f"\nTop {len(results)} matches:")
        for i, r in enumerate(results, 1):
            issn = pick_issn(r) or "(no ISSN)"
            print(f"  {i}. {r.get('title', '?')}  [{issn}]")
        choice = input("\nPick a number (or blank to cancel): ").strip()
        if not choice.isdigit() or not (1 <= int(choice) <= len(results)):
            print("Cancelled.")
            return
        item = results[int(choice) - 1]

    name = item.get("title", "?")
    issn = pick_issn(item)
    if not issn:
        print(f"Picked journal '{name}' has no ISSN — can't track.")
        return

    if any(j["issn"] == issn for j in config):
        print(f"Already tracking '{name}' ({issn}).")
        return

    print(f"\nWill add: {name}  [{issn}]")
    homepage = input(f"Homepage URL (Enter to skip / set later): ").strip() or guess_homepage(item)
    if not confirm("Confirm add?"):
        print("Cancelled.")
        return

    config.append({"name": name, "issn": issn, "homepage": homepage})
    CONFIG.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    print(f"\nAdded. Now run:  python3 build.py")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(1)
