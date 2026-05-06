#!/usr/bin/env python3
"""Check tracked journals for new articles since the last run.

Reads docs/journals.json, fetches latest articles from Crossref,
diffs against .last-seen.json, writes new_articles.md if anything is new,
and updates .last-seen.json.

When running inside GitHub Actions, sets a step output `new_count`
so the workflow can decide whether to open an issue.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOURNALS_FILE = ROOT / "docs" / "journals.json"
STATE_FILE = ROOT / ".last-seen.json"
OUTPUT_MD = ROOT / "new_articles.md"
SITE_URL = "https://ljh704708-bot.github.io/journal-tracker/"
USER_AGENT = "journal-tracker-action/1.0 (mailto:ljh704708-bot@users.noreply.github.com)"
CHECK_LATEST_N = 30  # how many recent articles to inspect per journal each run


def fetch_articles(issn: str, rows: int = CHECK_LATEST_N) -> list[dict]:
    params = urllib.parse.urlencode({
        "sort": "published",
        "order": "desc",
        "rows": rows,
        "select": "title,author,DOI,URL,published-online,published-print,issued",
    })
    url = f"https://api.crossref.org/journals/{issn}/works?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["message"]["items"]


def article_date(item: dict) -> datetime | None:
    for k in ("published-online", "published-print", "issued"):
        dp = (item.get(k) or {}).get("date-parts", [[]])[0]
        if dp:
            try:
                return datetime(dp[0], dp[1] if len(dp) > 1 else 1, dp[2] if len(dp) > 2 else 1)
            except ValueError:
                return datetime(dp[0], 1, 1)
    return None


def fmt_authors(authors: list[dict]) -> str:
    if not authors:
        return ""
    names = [(f"{a.get('given','')} {a.get('family','')}").strip() for a in authors[:3]]
    names = [n for n in names if n]
    return ", ".join(names) + (" et al." if len(authors) > 3 else "")


def write_output(name: str, value: str) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"{name}={value}\n")
    print(f"[output] {name}={value}")


def main() -> None:
    journals = json.loads(JOURNALS_FILE.read_text())
    is_first_run = not STATE_FILE.exists()
    seen: set[str] = set()
    if not is_first_run:
        seen = set(json.loads(STATE_FILE.read_text()))

    print(f"Tracking {len(journals)} journal(s); first run = {is_first_run}; "
          f"prior seen = {len(seen)}\n")

    new_articles: list[tuple[dict, dict]] = []
    all_dois: set[str] = set()

    for j in journals:
        print(f"  → {j['name']}")
        try:
            items = fetch_articles(j["issn"])
        except Exception as e:
            print(f"    ! fetch failed: {e}")
            # On transient failure, keep the journal's previously-seen DOIs in state
            # so we don't lose them. Pull them forward from the prior set.
            for doi in (d for d in seen if d.startswith(j["issn"])):
                pass  # placeholder; we just won't drop anything
            continue
        print(f"    {len(items)} articles fetched")
        for item in items:
            doi = item.get("DOI")
            if not doi:
                continue
            doi = doi.lower()
            all_dois.add(doi)
            if not is_first_run and doi not in seen:
                new_articles.append((j, item))

    new_articles.sort(
        key=lambda pair: article_date(pair[1]) or datetime.min,
        reverse=True,
    )

    # Carry forward any prior DOIs that aren't in the current window
    # (so we never re-flag an old article as "new" if it falls off page 1).
    merged = sorted(all_dois | seen)
    STATE_FILE.write_text(json.dumps(merged, indent=2) + "\n")

    if is_first_run:
        print(f"\nFirst run — initialized state with {len(all_dois)} DOIs. No issue will be opened.")
        write_output("new_count", "0")
        return

    print(f"\n{len(new_articles)} new article(s).")
    if not new_articles:
        write_output("new_count", "0")
        return

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        f"**{len(new_articles)} new article{'s' if len(new_articles) != 1 else ''}** "
        f"detected on {today}.",
        "",
        f"Browse on the [site]({SITE_URL}).",
        "",
    ]
    for j, item in new_articles:
        title = (item.get("title") or [""])[0].strip()
        url = item.get("URL") or f"https://doi.org/{item.get('DOI','')}"
        authors = fmt_authors(item.get("author", []))
        date = article_date(item)
        date_str = date.strftime("%Y-%m-%d") if date else ""
        lines.append(f"### [{title}]({url})")
        meta_parts = [f"*{j['name']}*"]
        if authors:
            meta_parts.append(authors)
        if date_str:
            meta_parts.append(date_str)
        lines.append(" · ".join(meta_parts))
        lines.append("")

    OUTPUT_MD.write_text("\n".join(lines))
    write_output("new_count", str(len(new_articles)))


if __name__ == "__main__":
    main()
