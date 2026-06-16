# Almere nieuws

Statische pagina met de 20 meest relevante nieuwsartikelen over Almere, elke 12 uur ververst via GitHub Actions. Gepubliceerd op GitHub Pages.

## Hoe het werkt

De pipeline bestaat uit 5 stappen die door [`run.py`](run.py) achter elkaar worden uitgevoerd:

1. **verify_sources** ([`pipeline/verify_sources.py`](pipeline/verify_sources.py)) — controleert dat alle RSS-feeds in [`sources.yaml`](sources.yaml) bereikbaar en parsebaar zijn. Faalt niet hard; rapporteert alleen.
2. **ingest** ([`pipeline/ingest.py`](pipeline/ingest.py)) — haalt elke feed op, extraheert per artikel titel, samenvatting, URL en datum, en schrijft nieuwe items naar `data/articles.db` (SQLite, in de repo gecommit). Dedupliceert op `url`.
3. **filter** ([`pipeline/filter.py`](pipeline/filter.py)) — past de keyword-lijst uit [`keywords.yaml`](keywords.yaml) toe op titel + samenvatting (case-insensitive, met word boundaries). Artikelen zonder match krijgen `status='filtered_out'` en gaan niet naar de LLM.
4. **score** ([`pipeline/score.py`](pipeline/score.py)) — stuurt de gematchte artikelen in batches van 10 naar Gemini Flash-Lite met de prompt uit [`relevance_prompt.md`](relevance_prompt.md). Gebruikt structured output (JSON-schema) voor een gegarandeerd geldig `{score: 1-10, motivatie: string}` per artikel. Respecteert `retryDelay` uit 429-responses.
5. **publish** ([`pipeline/publish.py`](pipeline/publish.py)) — selecteert de top 20 (`score DESC, published_at DESC`) met een cap van max 5 artikelen per bron, en rendert `site/index.html` via een Jinja2-template met Aero/Liquid-Glass styling en favicons van elke bron.

## Lokale setup

```bash
python -m venv .venv
. .venv/Scripts/activate    # Windows; gebruik .venv/bin/activate op macOS/Linux
pip install -r requirements.txt
```

Eén stap los draaien (zonder de hele pipeline):

```bash
python -m pipeline.verify_sources
python -m pipeline.ingest
python -m pipeline.filter
python -m pipeline.score      # vereist $GEMINI_API_KEY
python -m pipeline.publish
```

Of alles in één keer:

```bash
python run.py
```

## Deployment op GitHub

De repo bevat twee workflows:

- [`.github/workflows/run.yml`](.github/workflows/run.yml) — draait de pipeline om 06:00 en 18:00 UTC (cron) en bij handmatige trigger. Commit `data/articles.db` en `site/index.html` terug naar `main`.
- [`.github/workflows/pages.yml`](.github/workflows/pages.yml) — deployt `site/` naar GitHub Pages bij elke push die `site/` raakt.

### Eenmalige configuratie in de GitHub UI

1. **Voeg de Gemini API key toe als repository secret.**
   - Ga naar **Settings → Secrets and variables → Actions → New repository secret**.
   - Naam: `GEMINI_API_KEY`
   - Value: je key uit [Google AI Studio](https://aistudio.google.com/app/apikey).

2. **Schakel GitHub Pages in via Actions.**
   - Ga naar **Settings → Pages**.
   - Bij **Source** kies **GitHub Actions** (niet "Deploy from a branch").
   - Niets verder aan te klikken — de `pages.yml`-workflow doet de rest bij de volgende push naar `site/`.

3. **Trigger de eerste run handmatig.**
   - Ga naar **Actions → Run pipeline → Run workflow** (knop rechtsboven) → **Run workflow**.
   - Na ~1-2 minuten zou er een commit "Automatische run: …" verschijnen, wat de Pages-workflow triggert.
   - URL van de site staat onder **Settings → Pages** zodra de eerste deploy klaar is (`https://<user>.github.io/<repo>/`).

Daarna draait alles vanzelf elke 12 uur.

## Configuratie

| File | Wat erin staat |
|------|----------------|
| [`sources.yaml`](sources.yaml) | RSS-bronnen, gegroepeerd in categorieën met `name`, `url`, `tier` |
| [`keywords.yaml`](keywords.yaml) | Pre-filter keywords, gegroepeerd in thema's |
| [`relevance_prompt.md`](relevance_prompt.md) | Systeem-prompt voor de LLM-scorer met scoreschaal 1–10 |
| [`pipeline/publish.py`](pipeline/publish.py) | `TOP_N` en `MAX_PER_SOURCE` als constanten bovenin |
| [`pipeline/templates/index.html.j2`](pipeline/templates/index.html.j2) | Jinja2-template + CSS voor de site |
