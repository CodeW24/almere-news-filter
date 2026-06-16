"""Select top-N scored articles, mark them published, render to site/index.html.

Run: python -m pipeline.publish
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from pipeline.common import PROJECT_ROOT, open_db

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

TOP_N = 20
MAX_PER_SOURCE = 5

SITE_DIR = PROJECT_ROOT / "site"
TEMPLATE_DIR = PROJECT_ROOT / "pipeline" / "templates"
OUTPUT_HTML = SITE_DIR / "index.html"

# Map source name -> canonical domain, used for favicon lookup via Google's s2 service.
# Many sources are fetched via Google News redirects, so we can't derive this from the
# article URL — keep it explicit.
SOURCE_DOMAINS: dict[str, str] = {
    "BBC News": "bbc.co.uk",
    "The Guardian (International)": "theguardian.com",
    "Al Jazeera English": "aljazeera.com",
    "CNN Edition": "cnn.com",
    "NYT HomePage": "nytimes.com",
    "AP News": "apnews.com",
    "Reuters World": "reuters.com",
    "NOS": "nos.nl",
    "NU.nl": "nu.nl",
    "RTL Nieuws": "rtlnieuws.nl",
    "AD": "ad.nl",
    "De Volkskrant": "volkskrant.nl",
    "NRC": "nrc.nl",
    "Het Financieele Dagblad": "fd.nl",
    "Trouw": "trouw.nl",
    "Het Parool": "parool.nl",
    "Omroep Flevoland": "omroepflevoland.nl",
    "NH Nieuws": "nhnieuws.nl",
    "AT5": "at5.nl",
    "RTV Utrecht": "rtvutrecht.nl",
}


def logo_url(source_name: str) -> str | None:
    domain = SOURCE_DOMAINS.get(source_name)
    if not domain:
        return None
    return f"https://www.google.com/s2/favicons?domain={domain}&sz=64"


TZ_AMS = ZoneInfo("Europe/Amsterdam")
NL_MONTHS = [
    "jan", "feb", "mrt", "apr", "mei", "jun",
    "jul", "aug", "sep", "okt", "nov", "dec",
]


def score_class(score: int) -> str:
    if score >= 9:
        return "score-high"
    if score >= 7:
        return "score-mid"
    return "score-low"


def parse_dt(iso_str: str | None) -> datetime | None:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def relative_date_nl(iso_str: str | None, now: datetime) -> str:
    dt = parse_dt(iso_str)
    if dt is None:
        return "onbekend"
    diff = (now - dt).total_seconds()
    if diff < 90:
        return "zojuist"
    if diff < 3600:
        mins = int(diff // 60)
        return "1 minuut geleden" if mins == 1 else f"{mins} minuten geleden"
    if diff < 86400:
        hours = int(diff // 3600)
        return f"{hours} uur geleden"
    dt_local = dt.astimezone(TZ_AMS)
    now_local = now.astimezone(TZ_AMS)
    days_diff = (now_local.date() - dt_local.date()).days
    if days_diff == 1:
        return "gisteren"
    if days_diff < 7:
        return f"{days_diff} dagen geleden"
    if dt_local.year == now_local.year:
        return f"{dt_local.day} {NL_MONTHS[dt_local.month - 1]}"
    return f"{dt_local.day} {NL_MONTHS[dt_local.month - 1]} {dt_local.year}"


def format_updated_nl(now: datetime) -> str:
    local = now.astimezone(TZ_AMS)
    return (
        f"{local.day} {NL_MONTHS[local.month - 1]} {local.year}, "
        f"{local.strftime('%H:%M')} (Europe/Amsterdam)"
    )


def select_articles(conn, top_n: int, max_per_source: int):
    rows = conn.execute(
        """
        SELECT id, url, title, published_at,
               source_name, source_category,
               score, score_motivation
          FROM articles
         WHERE score IS NOT NULL
         ORDER BY score DESC, published_at DESC
        """
    ).fetchall()

    selected = []
    per_source = defaultdict(int)
    for row in rows:
        source_name = row[4]
        if per_source[source_name] >= max_per_source:
            continue
        selected.append(row)
        per_source[source_name] += 1
        if len(selected) >= top_n:
            break
    return selected


def main() -> int:
    conn = open_db()
    selected = select_articles(conn, TOP_N, MAX_PER_SOURCE)
    if not selected:
        print("Geen gescoorde artikelen — niets te publiceren.")
        conn.close()
        return 0

    print(f"Geselecteerd: {len(selected)} artikelen (TOP_N={TOP_N}, MAX_PER_SOURCE={MAX_PER_SOURCE})")

    cur = conn.cursor()
    cur.executemany(
        "UPDATE articles SET status = 'published' WHERE id = ?",
        [(row[0],) for row in selected],
    )
    conn.commit()

    now = datetime.now(timezone.utc)
    articles_ctx = []
    source_counter: Counter[str] = Counter()
    for row in selected:
        (_id, url, title, published_at, source_name,
         source_category, score, motivation) = row
        articles_ctx.append(
            {
                "url": url,
                "title": title,
                "source_name": source_name,
                "source_category": source_category,
                "score": int(score),
                "score_class": score_class(int(score)),
                "relative_date": relative_date_nl(published_at, now),
                "motivation": motivation,
                "logo_url": logo_url(source_name),
            }
        )
        source_counter[source_name] += 1

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(enabled_extensions=("html", "j2")),
    )
    template = env.get_template("index.html.j2")
    html = template.render(articles=articles_ctx, updated_at=format_updated_nl(now))

    OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    conn.close()

    print(f"Geschreven: {OUTPUT_HTML.relative_to(PROJECT_ROOT)}")
    print()
    print("=" * 72)
    print("PUBLICATIE-SAMENVATTING")
    print("=" * 72)
    print(f"  Geselecteerd : {len(selected)} artikelen")
    print(f"  Unieke bronnen: {len(source_counter)}")
    for src, count in source_counter.most_common():
        print(f"     {count}x  {src}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
