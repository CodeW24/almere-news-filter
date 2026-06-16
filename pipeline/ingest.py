"""Fetch all working feeds and persist new articles to SQLite.

Run: python -m pipeline.ingest
"""
from __future__ import annotations

import re
import sqlite3
import sys
from calendar import timegm
from datetime import datetime, timezone
from html import unescape

import feedparser

from pipeline.common import DB_PATH, fetch, load_sources, open_db

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    no_tags = _TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", unescape(no_tags)).strip()


def parse_published(entry) -> datetime:
    """Return a UTC datetime for the entry. Falls back to 'now' if missing."""
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            try:
                return datetime.fromtimestamp(timegm(t), tz=timezone.utc)
            except (TypeError, ValueError, OverflowError):
                continue
    return datetime.now(timezone.utc)


def entry_url(entry) -> str | None:
    """Extract a usable URL from a feed entry."""
    url = entry.get("link") or ""
    if not url and entry.get("links"):
        for link in entry["links"]:
            if isinstance(link, dict) and link.get("href"):
                url = link["href"]
                break
    return url.strip() or None


def entry_summary(entry) -> str:
    raw = entry.get("summary") or entry.get("description") or ""
    if not raw and entry.get("content"):
        content = entry["content"]
        if isinstance(content, list) and content:
            raw = content[0].get("value", "")
    return strip_html(raw)


def insert_articles(conn: sqlite3.Connection, rows: list[dict]) -> tuple[int, int]:
    """Insert rows. Returns (new_count, duplicate_count)."""
    new_count = 0
    dup_count = 0
    cur = conn.cursor()
    for row in rows:
        cur.execute(
            """
            INSERT OR IGNORE INTO articles (
                url, title, summary, published_at,
                source_name, source_category, source_tier
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["url"],
                row["title"],
                row["summary"],
                row["published_at"].isoformat(),
                row["source_name"],
                row["source_category"],
                row["source_tier"],
            ),
        )
        if cur.rowcount == 1:
            new_count += 1
        else:
            dup_count += 1
    conn.commit()
    return new_count, dup_count


def ingest_source(source: dict, category: str) -> tuple[list[dict], str | None]:
    """Fetch one source and return (rows, error)."""
    url = source.get("url")
    name = source.get("name") or url or "<unnamed>"
    tier = source.get("tier")
    if not url:
        return [], "missing url"

    body, err = fetch(url)
    if body is None:
        return [], err

    parsed = feedparser.parse(body)
    if parsed.bozo and not parsed.entries:
        bozo_exc = getattr(parsed, "bozo_exception", None)
        return [], f"parse error: {bozo_exc!r}" if bozo_exc else "parse error"

    rows: list[dict] = []
    for entry in parsed.entries:
        article_url = entry_url(entry)
        title = strip_html(entry.get("title"))
        if not article_url or not title:
            continue
        rows.append(
            {
                "url": article_url,
                "title": title,
                "summary": entry_summary(entry),
                "published_at": parse_published(entry),
                "source_name": name,
                "source_category": category,
                "source_tier": tier,
            }
        )
    return rows, None


def main() -> int:
    grouped = load_sources()
    conn = open_db(DB_PATH)

    print(f"writing to {DB_PATH}")
    print()

    per_category: dict[str, dict] = {}
    failures: list[tuple[str, str, str]] = []  # (category, name, error)

    for category, entries in grouped.items():
        cat_new = 0
        cat_dup = 0
        cat_sources_ok = 0
        cat_sources_total = len(entries)

        for src in entries:
            name = src.get("name") or src.get("url", "<unnamed>")
            print(f"  fetching [{category}] {name} ...", flush=True)
            rows, err = ingest_source(src, category)
            if err is not None:
                print(f"    FAIL: {err}")
                failures.append((category, name, err))
                continue
            new, dup = insert_articles(conn, rows)
            cat_new += new
            cat_dup += dup
            cat_sources_ok += 1
            print(f"    {len(rows)} entries -> {new} nieuw, {dup} duplicaat")

        per_category[category] = {
            "new": cat_new,
            "dup": cat_dup,
            "sources_ok": cat_sources_ok,
            "sources_total": cat_sources_total,
        }

    total_in_db = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    conn.close()

    print()
    print("=" * 72)
    print("INGESTIE-SAMENVATTING")
    print("=" * 72)
    grand_new = 0
    grand_dup = 0
    for category, stats in per_category.items():
        print(
            f"  {category:<24} {stats['sources_ok']}/{stats['sources_total']} bronnen, "
            f"{stats['new']:>4} nieuw, {stats['dup']:>5} duplicaat"
        )
        grand_new += stats["new"]
        grand_dup += stats["dup"]

    print("-" * 72)
    print(f"  TOTAAL                  {grand_new:>4} nieuw, {grand_dup:>5} duplicaat")
    print(f"  Artikelen in DB         {total_in_db}")

    if failures:
        print()
        print(f"Falende bronnen ({len(failures)}):")
        for category, name, err in failures:
            print(f"  [{category}] {name} -> {err}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
