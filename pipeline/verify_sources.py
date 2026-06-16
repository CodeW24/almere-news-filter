"""Verify that every feed in sources.yaml parses and contains items.

sources.yaml is grouped per category (internationaal, nl_landelijk, ...).
Each entry has name, url and tier. This script walks all categories,
fetches each feed, and prints a report grouped by category. Failures do
not stop the run — everything is reported in one overview.

Run: python -m pipeline.verify_sources
"""
from __future__ import annotations

import sys
import time
from datetime import datetime

import feedparser

from pipeline.common import fetch, load_sources

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def latest_entry_date(parsed) -> str | None:
    """Return ISO date string of the newest entry, or None."""
    newest: datetime | None = None
    for entry in parsed.entries:
        for key in ("published_parsed", "updated_parsed"):
            t = entry.get(key)
            if t:
                try:
                    dt = datetime(*t[:6])
                except (TypeError, ValueError):
                    continue
                if newest is None or dt > newest:
                    newest = dt
                break
    return newest.strftime("%Y-%m-%d %H:%M") if newest else None


def check_feed(source: dict) -> dict:
    name = source.get("name") or source.get("url", "<unnamed>")
    url = source.get("url")
    tier = source.get("tier")
    result = {
        "name": name,
        "url": url,
        "tier": tier,
        "ok": False,
        "items": 0,
        "latest": None,
        "error": None,
    }

    if not url:
        result["error"] = "missing url"
        return result

    body, err = fetch(url)
    if body is None:
        result["error"] = err
        return result

    parsed = feedparser.parse(body)
    if parsed.bozo and not parsed.entries:
        bozo_exc = getattr(parsed, "bozo_exception", None)
        result["error"] = f"parse error: {bozo_exc!r}" if bozo_exc else "parse error"
        return result

    result["ok"] = True
    result["items"] = len(parsed.entries)
    result["latest"] = latest_entry_date(parsed)
    return result


def fmt_tier(tier) -> str:
    return f"tier {tier}" if tier is not None else "tier -"


def main() -> int:
    grouped = load_sources()

    total = 0
    working = 0
    report: list[tuple[str, list[dict]]] = []

    for category, entries in grouped.items():
        results: list[dict] = []
        for src in entries:
            print(f"checking [{category}] {src.get('name', src.get('url', '?'))} ...",
                  flush=True)
            r = check_feed(src)
            results.append(r)
            total += 1
            if r["ok"]:
                working += 1
            time.sleep(0.2)
        report.append((category, results))

    print()
    print("=" * 72)
    print("VERIFICATIERAPPORT")
    print("=" * 72)

    for category, results in report:
        print()
        print(f"## {category}  ({len(results)} {'bron' if len(results) == 1 else 'bronnen'})")
        print("-" * 72)
        if not results:
            print("  (geen bronnen gedefinieerd)")
            continue
        for r in results:
            head = f"  [{'OK  ' if r['ok'] else 'FAIL'}] {r['name']}  ({fmt_tier(r['tier'])})"
            print(head)
            if r["ok"]:
                latest = r["latest"] or "onbekend"
                print(f"         items: {r['items']}, meest recent: {latest}")
            else:
                print(f"         reden: {r['error']}")
                print(f"         url:   {r['url']}")

    print()
    print("=" * 72)
    print(f"SAMENVATTING: {working} van {total} feeds werken.")
    print("=" * 72)
    print()
    print("Installeer dependencies met:")
    print("  pip install -r requirements.txt")
    print()
    print("Draai dit script opnieuw met:")
    print("  python -m pipeline.verify_sources")

    return 0


if __name__ == "__main__":
    sys.exit(main())
