"""Shared utilities for the pipeline: source loading, feed fetching, DB access."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import httpx
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCES_PATH = PROJECT_ROOT / "sources.yaml"
KEYWORDS_PATH = PROJECT_ROOT / "keywords.yaml"
DB_PATH = PROJECT_ROOT / "data" / "articles.db"

USER_AGENT = "almere-nieuws/0.1 (+https://github.com/)"
TIMEOUT_SECONDS = 15

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    published_at TIMESTAMP,
    source_name TEXT NOT NULL,
    source_category TEXT,
    source_tier INTEGER,
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    keyword_match BOOLEAN,
    matched_keywords TEXT,
    score INTEGER,
    score_motivation TEXT,
    scored_at TIMESTAMP,
    status TEXT DEFAULT 'new'
);

CREATE INDEX IF NOT EXISTS idx_articles_published_at ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);
CREATE INDEX IF NOT EXISTS idx_articles_score ON articles(score);
"""

# Columns to add via ALTER TABLE for databases that pre-date them.
# Each entry: (column_name, full ALTER statement).
MIGRATIONS = [
    ("matched_keywords", "ALTER TABLE articles ADD COLUMN matched_keywords TEXT"),
]


def open_db(path: Path = DB_PATH) -> sqlite3.Connection:
    """Open the SQLite DB, creating the schema and applying migrations."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(articles)")}
    for col_name, ddl in MIGRATIONS:
        if col_name not in existing:
            conn.execute(ddl)
    conn.commit()
    return conn


def load_sources(path: Path = SOURCES_PATH) -> dict[str, list[dict]]:
    """Return {category: [source, ...]}."""
    if not path.exists():
        print(f"sources.yaml not found at {path}", file=sys.stderr)
        sys.exit(1)
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        print("sources.yaml must be a mapping of category -> [sources].", file=sys.stderr)
        sys.exit(1)

    grouped: dict[str, list[dict]] = {}
    for category, entries in data.items():
        if not entries:
            grouped[category] = []
            continue
        if not isinstance(entries, list):
            print(
                f"category '{category}' must be a list, got {type(entries).__name__}",
                file=sys.stderr,
            )
            sys.exit(1)
        grouped[category] = entries
    return grouped


def fetch(url: str) -> tuple[bytes | None, str | None]:
    """Fetch URL and return (body, error). Body is None when fetch failed."""
    try:
        with httpx.Client(
            timeout=TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            r = client.get(url)
            r.raise_for_status()
            return r.content, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
