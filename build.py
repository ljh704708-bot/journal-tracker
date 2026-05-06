#!/usr/bin/env python3
"""Fetch latest articles for each journal in journals.json:
   - metadata via Crossref
   - abstracts via OpenAlex (where available)
   render docs/index.html + docs/feed.xml."""

from __future__ import annotations

import html
import json
import re
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

ROOT = Path(__file__).parent
ARTICLES_PER_JOURNAL = 15
USER_AGENT = "journal-tracker/1.0 (mailto:daniel@example.com)"
ABSTRACT_MAX_CHARS = 600
SITE_TITLE = "Journal Tracker"
SITE_URL = ""  # set this to your GitHub Pages URL once deployed; used in feed.xml


# ---------------- HTTP ----------------

def http_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


# ---------------- Crossref ----------------

def fetch_crossref(issn: str, rows: int = ARTICLES_PER_JOURNAL) -> list[dict]:
    params = urllib.parse.urlencode({
        "sort": "published",
        "order": "desc",
        "rows": rows,
        "select": "title,author,DOI,URL,published-online,published-print,issued,volume,issue,container-title",
    })
    url = f"https://api.crossref.org/journals/{issn}/works?{params}"
    return http_get_json(url)["message"]["items"]


# ---------------- OpenAlex (abstract enrichment) ----------------

def fetch_abstracts(dois: list[str]) -> dict[str, str]:
    """Batch-look up abstracts for a list of DOIs. Returns {doi_lower: abstract_text}."""
    if not dois:
        return {}
    # OpenAlex supports filter `doi:url1|url2|...` (must be full https://doi.org/ form, lowercase)
    full_dois = [f"https://doi.org/{d.lower()}" for d in dois]
    out: dict[str, str] = {}
    # OpenAlex caps filter size; chunk to be safe.
    for i in range(0, len(full_dois), 25):
        chunk = full_dois[i:i + 25]
        params = urllib.parse.urlencode({
            "filter": "doi:" + "|".join(chunk),
            "per-page": 25,
            "select": "doi,abstract_inverted_index",
        })
        url = f"https://api.openalex.org/works?{params}"
        try:
            data = http_get_json(url)
        except Exception as e:
            print(f"  ! openalex chunk failed: {e}")
            continue
        for w in data.get("results", []):
            doi = (w.get("doi") or "").replace("https://doi.org/", "").lower()
            idx = w.get("abstract_inverted_index")
            if doi and idx:
                out[doi] = decode_inverted_index(idx)
    return out


def decode_inverted_index(idx: dict[str, list[int]]) -> str:
    positions: list[tuple[int, str]] = []
    for word, pos_list in idx.items():
        for pos in pos_list:
            positions.append((pos, word))
    positions.sort()
    return " ".join(word for _, word in positions)


# ---------------- Helpers ----------------

JATS_TAG = re.compile(r"<[^>]+>")
WHITESPACE = re.compile(r"\s+")


def clean_text(s: str | None) -> str:
    if not s:
        return ""
    s = JATS_TAG.sub(" ", s)
    s = WHITESPACE.sub(" ", s).strip()
    # crossref sometimes prefixes "Abstract" — trim
    s = re.sub(r"^(Abstract|ABSTRACT)[:\s]+", "", s)
    return s


def truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    cut = s[:n]
    last_space = cut.rfind(" ")
    if last_space > n * 0.7:
        cut = cut[:last_space]
    return cut.rstrip(",.;: ") + "…"


def best_date(item: dict) -> tuple[int, int, int] | None:
    for key in ("published-online", "published-print", "issued"):
        parts = item.get(key, {}).get("date-parts", [[]])
        if parts and parts[0]:
            p = parts[0]
            return (p[0], p[1] if len(p) > 1 else 1, p[2] if len(p) > 2 else 1)
    return None


def fmt_date(d: tuple[int, int, int] | None) -> str:
    if not d:
        return "—"
    try:
        return datetime(*d).strftime("%Y-%m-%d")
    except ValueError:
        return f"{d[0]}-{d[1]:02d}"


def to_datetime(d: tuple[int, int, int] | None) -> datetime:
    if not d:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    try:
        return datetime(*d, tzinfo=timezone.utc)
    except ValueError:
        return datetime(d[0], d[1], 1, tzinfo=timezone.utc)


def fmt_authors(authors: list[dict]) -> str:
    if not authors:
        return ""
    names = []
    for a in authors[:3]:
        given = a.get("given", "").strip()
        family = a.get("family", "").strip()
        names.append(f"{given} {family}".strip() if given else family)
    suffix = " et al." if len(authors) > 3 else ""
    return ", ".join(n for n in names if n) + suffix


# ---------------- Rendering ----------------

def render_card(item: dict, journal_name: str) -> str:
    title = clean_text((item.get("title") or [""])[0])
    url = item.get("URL") or f"https://doi.org/{item.get('DOI', '')}"
    doi = item.get("DOI", "")
    authors = fmt_authors(item.get("author", []))
    date = fmt_date(best_date(item))
    vol = item.get("volume")
    iss = item.get("issue")
    if vol and iss:
        issue_html = f'<span class="badge issue">Vol {html.escape(vol)}, No {html.escape(iss)}</span>'
    elif vol:
        issue_html = f'<span class="badge issue">Vol {html.escape(vol)}</span>'
    else:
        issue_html = '<span class="badge online-first">Online first</span>'

    abstract = item.get("_abstract", "")
    abstract_html = ""
    if abstract:
        abstract_html = f'<p class="abstract">{html.escape(truncate(abstract, ABSTRACT_MAX_CHARS))}</p>'

    return f"""
        <article class="card" data-doi="{html.escape(doi)}">
          <a class="title" href="{html.escape(url)}" target="_blank" rel="noopener">{html.escape(title)}</a>
          <div class="meta">
            <span class="authors">{html.escape(authors)}</span>
            <span class="meta-right">
              <span class="date">{date}</span>
              {issue_html}
            </span>
          </div>
          {abstract_html}
        </article>
    """


def render_journal_section(journal: dict, items: list[dict]) -> str:
    name = html.escape(journal["name"])
    homepage = html.escape(journal["homepage"])
    cards = "\n".join(render_card(item, journal["name"]) for item in items)
    return f"""
      <section class="journal">
        <h2>
          <a href="{homepage}" target="_blank" rel="noopener">{name}</a>
          <span class="count">{len(items)}</span>
        </h2>
        <div class="cards">
          {cards}
        </div>
      </section>
    """


def render_page(sections_html: str, built_at: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{SITE_TITLE}</title>
  <link rel="stylesheet" href="style.css">
  <link rel="alternate" type="application/rss+xml" title="{SITE_TITLE} feed" href="feed.xml">
</head>
<body>
  <header>
    <div class="header-row">
      <div>
        <h1>{SITE_TITLE}</h1>
        <p class="subtitle">Latest from journals I follow · updated {built_at}</p>
      </div>
      <a class="rss-link" href="feed.xml" title="Subscribe via RSS">RSS</a>
    </div>
  </header>
  <main>
    {sections_html}
  </main>
  <footer>
    <p>Data via <a href="https://www.crossref.org/" target="_blank" rel="noopener">Crossref</a>
       and <a href="https://openalex.org/" target="_blank" rel="noopener">OpenAlex</a>.
       <span id="new-info"></span></p>
  </footer>
  <script src="app.js"></script>
</body>
</html>
"""


# ---------------- RSS feed ----------------

def render_rss(all_items: list[tuple[dict, dict]], built_at: datetime) -> str:
    """all_items: list of (journal, article_item) sorted by date desc."""
    rss_items = []
    for journal, item in all_items[:60]:
        title = clean_text((item.get("title") or [""])[0])
        link = item.get("URL") or f"https://doi.org/{item.get('DOI', '')}"
        guid = item.get("DOI", link)
        date = best_date(item)
        pubdate = format_datetime(to_datetime(date))
        authors = fmt_authors(item.get("author", []))
        abstract = item.get("_abstract", "")
        desc_parts = [f"<strong>{html.escape(journal['name'])}</strong>"]
        if authors:
            desc_parts.append(html.escape(authors))
        if abstract:
            desc_parts.append(html.escape(abstract))
        description = "<br><br>".join(desc_parts)
        rss_items.append(f"""
    <item>
      <title>{html.escape(title)}</title>
      <link>{html.escape(link)}</link>
      <guid isPermaLink="false">{html.escape(guid)}</guid>
      <pubDate>{pubdate}</pubDate>
      <category>{html.escape(journal["name"])}</category>
      <description>{html.escape(description, quote=True)}</description>
    </item>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>{SITE_TITLE}</title>
  <link>{SITE_URL or 'about:blank'}</link>
  <description>Latest articles from journals I follow.</description>
  <lastBuildDate>{format_datetime(built_at)}</lastBuildDate>
  {''.join(rss_items)}
</channel>
</rss>
"""


# ---------------- Main ----------------

def main() -> None:
    journals = json.loads((ROOT / "journals.json").read_text())
    print(f"Tracking {len(journals)} journal(s)\n")

    # Fetch metadata
    sections_html_parts = []
    all_items: list[tuple[dict, dict]] = []
    all_dois: list[str] = []
    journal_items: list[tuple[dict, list[dict]]] = []

    for j in journals:
        print(f"[crossref] {j['name']} ({j['issn']})")
        try:
            items = fetch_crossref(j["issn"])
        except Exception as e:
            print(f"  ! failed: {e}")
            items = []
        print(f"  {len(items)} articles")
        journal_items.append((j, items))
        for it in items:
            doi = it.get("DOI")
            if doi:
                all_dois.append(doi)

    # Enrich with abstracts
    print(f"\n[openalex] enriching {len(all_dois)} DOIs with abstracts...")
    abstracts = fetch_abstracts(all_dois)
    print(f"  got abstracts for {len(abstracts)} of {len(all_dois)} articles")

    # Attach abstracts back; prefer Crossref's abstract if it has one (Springer JBE), fall back to OpenAlex
    for _, items in journal_items:
        for it in items:
            doi_lower = (it.get("DOI") or "").lower()
            xref_abs = clean_text(it.get("abstract"))  # rarely present, but use if so
            oa_abs = abstracts.get(doi_lower, "")
            it["_abstract"] = xref_abs or oa_abs

    # Render
    for j, items in journal_items:
        sections_html_parts.append(render_journal_section(j, items))
        for it in items:
            all_items.append((j, it))

    sections_html = "\n".join(sections_html_parts)
    built_dt = datetime.now(timezone.utc)
    built_at_str = built_dt.strftime("%Y-%m-%d %H:%M UTC")

    # Sort all items by date desc for RSS
    all_items.sort(key=lambda pair: to_datetime(best_date(pair[1])), reverse=True)

    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "index.html").write_text(render_page(sections_html, built_at_str))
    (docs / "feed.xml").write_text(render_rss(all_items, built_dt))
    print(f"\nWrote {docs/'index.html'} and {docs/'feed.xml'}")


if __name__ == "__main__":
    main()
