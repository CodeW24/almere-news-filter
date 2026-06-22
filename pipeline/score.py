"""Score keyword-matched articles for Almere relevance via Gemini Flash-Lite.

Run: python -m pipeline.score
Requires GEMINI_API_KEY in the environment.
"""
from __future__ import annotations

import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

from pipeline.common import PROJECT_ROOT, open_db

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

MODEL = "gemini-2.5-flash-lite"
PROMPT_PATH = PROJECT_ROOT / "relevance_prompt.md"
BATCH_SIZE = 10
MAX_RETRIES = 3
FALLBACK_BACKOFF_SECONDS = (5, 15, 45)


class ScoredItem(BaseModel):
    index: int
    score: int
    motivatie: str


class ScoredBatch(BaseModel):
    scores: list[ScoredItem]


def load_system_prompt() -> str:
    if not PROMPT_PATH.exists():
        print(f"relevance_prompt.md not found at {PROMPT_PATH}", file=sys.stderr)
        sys.exit(1)
    base = PROMPT_PATH.read_text(encoding="utf-8")
    extra = (
        "\n\n## Batch-instructies\n"
        "Je krijgt meerdere artikelen tegelijk, genummerd. Geef per artikel een score en motivatie."
        " Antwoord uitsluitend in JSON: {\"scores\": [{\"index\": <n>, \"score\": <1-10>,"
        " \"motivatie\": \"<max 1 zin>\"}, ...]}."
        " Houd de volgorde aan en gebruik dezelfde indexen als in de invoer."
    )
    return base + extra


def build_batch_message(batch: list[dict]) -> str:
    parts = ["Hieronder volgen de te scoren artikelen:\n"]
    for i, art in enumerate(batch, start=1):
        parts.append(
            f"--- Artikel {i} ---\n"
            f"Bron: {art['source_name']}\n"
            f"Datum: {art['published_at'] or 'onbekend'}\n"
            f"Titel: {art['title']}\n"
            f"Samenvatting: {art['summary'] or '(geen samenvatting beschikbaar)'}\n"
        )
    return "\n".join(parts)


_RETRY_RE = re.compile(r"'retryDelay'\s*:\s*'([0-9.]+)(ms|s)'")


def extract_retry_delay(exc: Exception) -> float | None:
    """Parse 'retryDelay' from a 429 error message. Returns seconds, or None."""
    m = _RETRY_RE.search(str(exc))
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2)
    return value / 1000 if unit == "ms" else value


def is_rate_limit(exc: Exception) -> bool:
    if isinstance(exc, genai_errors.APIError):
        code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        if code == 429:
            return True
    msg = str(exc).lower()
    return "rate" in msg or "quota" in msg or "429" in msg or "resource_exhausted" in msg


def score_batch(
    client: genai.Client, system_prompt: str, batch: list[dict]
) -> ScoredBatch | None:
    """Score a batch. Returns None if the LLM result is unusable. Raises on persistent failure."""
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        response_mime_type="application/json",
        response_schema=ScoredBatch,
        temperature=0.0,
    )
    user_text = build_batch_message(batch)

    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=user_text,
                config=config,
            )
            parsed = response.parsed
            if isinstance(parsed, ScoredBatch):
                return parsed
            return ScoredBatch.model_validate_json(response.text)
        except Exception as e:  # noqa: BLE001
            last_exc = e
            if attempt < MAX_RETRIES and is_rate_limit(e):
                delay = extract_retry_delay(e)
                if delay is None:
                    delay = FALLBACK_BACKOFF_SECONDS[min(attempt, len(FALLBACK_BACKOFF_SECONDS) - 1)]
                # add 0.5s padding to be safe
                delay += 0.5
                print(f"    rate limited; sleeping {delay:.1f}s ...")
                time.sleep(delay)
                continue
            raise
    assert last_exc is not None
    raise last_exc


def apply_batch_result(
    conn, batch: list[dict], result: ScoredBatch
) -> tuple[int, str | None]:
    """Apply scores to DB. Returns (n_applied, error_reason or None)."""
    if len(result.scores) != len(batch):
        return 0, f"expected {len(batch)} items, got {len(result.scores)}"

    by_index = {item.index: item for item in result.scores}
    expected_indexes = set(range(1, len(batch) + 1))
    if set(by_index.keys()) != expected_indexes:
        return 0, f"indexes mismatch: got {sorted(by_index)}, expected {sorted(expected_indexes)}"

    scored_at = datetime.now(timezone.utc).isoformat()
    cur = conn.cursor()
    for i, article in enumerate(batch, start=1):
        item = by_index[i]
        cur.execute(
            """
            UPDATE articles
               SET score = ?, score_motivation = ?, scored_at = ?, status = 'scored'
             WHERE id = ?
            """,
            (item.score, item.motivatie, scored_at, article["id"]),
        )
    conn.commit()
    return len(batch), None


def fetch_to_score(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, title, summary, source_name, published_at
          FROM articles
         WHERE keyword_match = 1 AND score IS NULL
         ORDER BY published_at DESC
        """
    ).fetchall()
    return [
        {
            "id": r[0],
            "title": r[1],
            "summary": r[2],
            "source_name": r[3],
            "published_at": r[4],
        }
        for r in rows
    ]


def main() -> int:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY ontbreekt in de environment.", file=sys.stderr)
        print("       Zet de variabele met: $env:GEMINI_API_KEY = '...'", file=sys.stderr)
        return 1

    system_prompt = load_system_prompt()
    client = genai.Client(api_key=api_key)

    conn = open_db()
    queue = fetch_to_score(conn)
    total = len(queue)
    if not total:
        print("Niets te scoren — geen artikelen met keyword_match = TRUE en score IS NULL.")
        return 0

    batches = [queue[i : i + BATCH_SIZE] for i in range(0, total, BATCH_SIZE)]
    print(f"Scoren met {MODEL}: {total} artikelen in {len(batches)} batch(es) van max {BATCH_SIZE}")
    print()

    scored = 0
    failed_batches = 0
    rescued_via_fallback = 0
    skipped_articles = 0

    def fallback_individual(batch: list[dict], reason: str) -> int:
        """Score elke article los; isoleert een rotte article die de batch verpest."""
        print(f"    fallback: scoor {len(batch)} artikelen individueel ({reason}) ...")
        nonlocal skipped_articles
        applied = 0
        for art in batch:
            single = [art]
            try:
                single_result = score_batch(client, system_prompt, single)
            except Exception as e:  # noqa: BLE001
                skipped_articles += 1
                print(f"      [SKIP id={art['id']}] {type(e).__name__}: {str(e)[:140]}")
                continue
            n, err = apply_batch_result(conn, single, single_result)
            if err:
                skipped_articles += 1
                print(f"      [SKIP id={art['id']}] {err}")
                continue
            applied += n
        return applied

    for batch_idx, batch in enumerate(batches, start=1):
        print(f"  batch {batch_idx}/{len(batches)} ({len(batch)} artikelen) ...", flush=True)
        try:
            result = score_batch(client, system_prompt, batch)
        except Exception as e:  # noqa: BLE001
            failed_batches += 1
            n = fallback_individual(batch, f"{type(e).__name__}: {str(e)[:80]}")
            rescued_via_fallback += n
            scored += n
            continue
        n_applied, err = apply_batch_result(conn, batch, result)
        if err:
            failed_batches += 1
            n = fallback_individual(batch, err)
            rescued_via_fallback += n
            scored += n
            continue
        scored += n_applied
        avg = sum(s.score for s in result.scores) / len(result.scores)
        print(f"    OK: {n_applied} gescoord (batch-gemiddelde: {avg:.2f})")

    print()
    print("=" * 72)
    print("SCORING-SAMENVATTING")
    print("=" * 72)
    print(f"  Deze run gescoord  : {scored}")
    print(f"  Via batch-fallback : {rescued_via_fallback}")
    print(f"  Mislukte batches   : {failed_batches}  ({skipped_articles} artikelen overgeslagen, blijven op status='new')")

    distribution: Counter[int] = Counter()
    for (s,) in conn.execute("SELECT score FROM articles WHERE score IS NOT NULL"):
        distribution[int(s)] += 1
    grand_total = sum(distribution.values())
    print(f"  Totaal gescoord    : {grand_total}")
    print()
    print("Score-distributie (alle gescoorde artikelen):")
    for s in range(1, 11):
        bar = "#" * distribution.get(s, 0)
        print(f"  {s:>2}: {distribution.get(s, 0):>4}  {bar}")

    print()
    print("=" * 72)
    print("TOP 10 ARTIKELEN (alle gescoorde)")
    print("=" * 72)
    top = conn.execute(
        """
        SELECT score, source_name, title, score_motivation
          FROM articles
         WHERE score IS NOT NULL
         ORDER BY score DESC, published_at DESC
         LIMIT 10
        """
    ).fetchall()
    for score, src, title, motiv in top:
        print(f"  [{score:>2}] {src}  --  {title[:90]}")
        print(f"        -> {motiv[:120]}")

    conn.close()
    # Returncode is altijd 0: gefaalde batches blijven score IS NULL en worden
    # volgende run automatisch opnieuw opgepakt. Een transiente Gemini-storing
    # moet de hele workflow niet rood maken.
    return 0


if __name__ == "__main__":
    sys.exit(main())
