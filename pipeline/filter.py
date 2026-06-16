"""Pre-filter articles via keyword matching before they go to the LLM scorer.

Run: python -m pipeline.filter
"""
from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from pipeline.common import KEYWORDS_PATH, open_db

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def load_keywords(path: Path = KEYWORDS_PATH) -> list[tuple[str, str]]:
    """Return [(category, keyword), ...]. Order preserved per category."""
    if not path.exists():
        print(f"keywords.yaml not found at {path}", file=sys.stderr)
        sys.exit(1)
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    out: list[tuple[str, str]] = []
    for category, kws in data.items():
        if not kws:
            continue
        for kw in kws:
            if isinstance(kw, str) and kw.strip():
                out.append((category, kw.strip()))
    return out


def compile_patterns(keywords: list[tuple[str, str]]) -> list[tuple[str, re.Pattern]]:
    """Compile each keyword as a case-insensitive word-bounded regex."""
    patterns = []
    for _category, kw in keywords:
        pat = re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE)
        patterns.append((kw, pat))
    return patterns


def match_keywords(text: str, patterns: list[tuple[str, re.Pattern]]) -> list[str]:
    """Return the keywords (original spelling) that hit, in input order, unique."""
    hits: list[str] = []
    for kw, pat in patterns:
        if pat.search(text):
            hits.append(kw)
    return hits


def main() -> int:
    keywords = load_keywords()
    if not keywords:
        print("No keywords defined in keywords.yaml.", file=sys.stderr)
        return 1
    patterns = compile_patterns(keywords)
    print(f"Loaded {len(keywords)} keywords from {KEYWORDS_PATH.name}")

    conn = open_db()
    rows = conn.execute(
        """
        SELECT id, title, summary, source_category
          FROM articles
         WHERE status = 'new' AND keyword_match IS NULL
        """
    ).fetchall()
    print(f"Filtering {len(rows)} articles ...")

    matched_count: dict[str, int] = defaultdict(int)
    filtered_count: dict[str, int] = defaultdict(int)
    total_per_category: dict[str, int] = defaultdict(int)
    keyword_hits = Counter()

    cur = conn.cursor()
    for article_id, title, summary, category in rows:
        text = f"{title or ''} {summary or ''}"
        hits = match_keywords(text, patterns)
        total_per_category[category] += 1
        if hits:
            keyword_hits.update(hits)
            matched_count[category] += 1
            cur.execute(
                """
                UPDATE articles
                   SET keyword_match = 1, matched_keywords = ?
                 WHERE id = ?
                """,
                (",".join(hits), article_id),
            )
        else:
            filtered_count[category] += 1
            cur.execute(
                """
                UPDATE articles
                   SET keyword_match = 0, status = 'filtered_out'
                 WHERE id = ?
                """,
                (article_id,),
            )
    conn.commit()
    conn.close()

    print()
    print("=" * 72)
    print("FILTER-SAMENVATTING (per categorie)")
    print("=" * 72)
    grand_total = grand_matched = grand_filtered = 0
    for category in sorted(total_per_category):
        total = total_per_category[category]
        m = matched_count.get(category, 0)
        f = filtered_count.get(category, 0)
        grand_total += total
        grand_matched += m
        grand_filtered += f
        print(f"  {category:<24} {total:>4} nieuw -> {m:>4} gematcht, {f:>4} filtered_out")
    print("-" * 72)
    print(
        f"  TOTAAL                  {grand_total:>4} nieuw -> "
        f"{grand_matched:>4} gematcht, {grand_filtered:>4} filtered_out"
    )

    print()
    print("=" * 72)
    print("TOP 10 KEYWORDS")
    print("=" * 72)
    if keyword_hits:
        for kw, count in keyword_hits.most_common(10):
            print(f"  {count:>5}  {kw}")
    else:
        print("  (geen matches)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
