# university-scraper

Scrapes the [IMI PMF Kragujevac](https://imi.pmf.kg.ac.rs/) and
[Prirodno-matematički fakultet](https://www.pmf.kg.ac.rs/) websites
using **`data/urls.json` as the single source of truth** for what to
scrape.  It extracts clean text from HTML pages, PDFs, and DOCX files,
indexes every chunk in up to three surface forms (original script,
transliterated script, ASCII-folded), generates embeddings with
`intfloat/multilingual-e5-large`, and upserts everything into a
persistent ChromaDB vector store.

## What it does

1. **Loads the plan** from `data/urls.json`: 60+ HTML seeds, curated
   PDFs (konkurs, cenovnik, pravilnici, past entrance exams, prep
   lessons), the **156 course-syllabus PDFs** generated from slug lists,
   and the staff-profile / Moodle-catalogue templates.
2. **Crawls** each seed within the allowed hosts only, up to a per-seed
   depth limit.  Every discovered link is **normalised** (query-string
   stripping, tab-selector removal, canonical `index.php` form,
   percent-encoding) and checked against the **denylist** before it is
   queued — this is what keeps the frontier finite on a site where
   arbitrary query strings return HTTP 200 with identical content.
3. **Extracts** clean text — strips navigation, headers, footers, the
   repeating address block, and staff CV/publications sections; handles
   table-based pages (notice board, timetable) and empty-shell 404s.
4. **Chunks** with the configured strategy, then expands every chunk
   into **Cyrillic / Latin / ASCII** variants (`č→c, š→s, ž→z, ć→c,
   đ→dj`) because the sites mix scripts and students type without
   diacritics.
5. **Embeds** each variant using `multilingual-e5-large` with the
   required `passage: ` prefix.
6. **Upserts** into a ChromaDB collection named `university_knowledge`
   (content-hashed IDs make re-ingestion idempotent).
7. **Runs the article-ID walk** — a monotonic `/vesti/{ID}-x` walk that
   incrementally ingests news/notice items that would otherwise age out
   of the 30-item rolling window.

## How it connects to university-chatbot

The scraper writes to a ChromaDB **persist directory** (default:
`./data/vector_store`).  The `university-chatbot` project opens the
**same directory read-only** and queries it.

A file named `shared_config.json` is automatically written into the
persist directory on the first run.  It records the embedding model
name, collection name, E5 query/passage prefixes, and HNSW space so
both projects stay in sync.

> **Important:** Both projects must point to the **same absolute
> `VECTOR_DB_PATH`**.  The default is `./data/vector_store` relative
> to each project root.  If you move one project, set
> `VECTOR_DB_PATH` to the shared absolute path in the `.env` file or
> environment.

## Quick start

```bash
cd university-scraper
conda activate uni
pip install -r requirements.txt
python run.py
```

## The plan: data/urls.json

The file documents everything the crawl needs to know about the two
sites — and why:

- `seeds` — the HTML pages, with `priority` (1 = the working chatbot,
  2 = depth, 3 = optional), `refresh` cadence, `type` (plain `html`,
  `html_table` for the notice board, `html_app` for the timetable,
  `html_template` for staff profiles / Moodle catalogue), and
  `follow_pdfs`.
- `pdf_seeds` — curated PDFs grouped by topic.  `schedules_current` is
  intentionally not seeded: `/pub/{hash}_{timestamp}/` paths change on
  every re-upload and are re-resolved from the parent HTML pages.
- `course_syllabi` — 156 syllabus PDFs generated from slug lists
  (directory listings return 403).  Slugs contain real typos
  (`strukutre_podataka_i_algoritmi_2`) — do not "correct" them.
- `crawl_rules.url_normalization` — the canonicalisation rules
  (query-string stripping is the single most important one).
- `crawl_rules.denylist` — 27 rules that keep the crawler terminating
  (staff publications tabs, frozen microsites, Moodle internals,
  other institutes, the stale 2023/24 competition page...).
- `change_detection` — the article-ID walk spec.
- `known_conflicts` / `gaps` — documented for the QA layer.

## Configuration

Runtime settings live in `config/settings.py` and can be overridden
via environment variables or a `.env` file.  Key settings:

| Variable | Default | Description |
|---|---|---|
| `URLS_JSON_PATH` | `./data/urls.json` | The scrape plan (single source of truth) |
| `INGEST_PRIORITIES` | `[1, 2, 3]` | Only ingest seeds with these priorities |
| `VECTOR_DB_PATH` | `./data/vector_store` | ChromaDB persist directory (shared with chatbot) |
| `CRAWLER_DELAY` | `1.5` | Seconds between requests (floor: urls.json politeness) |
| `CHUNK_SIZE` | `1000` | Character chunk size |
| `CHUNK_OVERLAP` | `200` | Character overlap between chunks |
| `CRAWL_HISTORY_ENABLED` | `true` | Skip URLs still fresh per their refresh cadence |
| `FORCE_REFRESH` | `false` | Ignore refresh cadence, re-ingest everything |
| `TRANSLITERATE_VARIANTS` | `true` | Index Cyrillic + Latin + ASCII variants |
| `ID_WALK_ENABLED` | `true` | Incremental notice-board article-ID walk |
| `ID_WALK_MAX_MISSES` | `20` | Consecutive 404s before the walk declares the head reached |
| `OCR_ENABLED` | `false` | OCR scanned PDFs (needs `ocrmypdf`) |

## Running the search server (API for the Flutter app)

The FastAPI server in `src/api/search_server.py` exposes the vector search
API that the Flutter app talks to.  It reads the same ChromaDB collection
that the scraper populates, so **run the scraper first** to fill the store.

```bash
cd university-scraper
conda activate uni
pip install -r requirements.txt

# Option A — start directly via the module entry point:
python src/api/search_server.py

# Option B — start with uvicorn explicitly:
python -m uvicorn src.api.search_server:app --host 0.0.0.0 --port 8001
```

The server listens on `http://0.0.0.0:8001` and exposes:

- `POST /search` — hybrid (dense + BM25) search, body `{"query": "...", "k": 5}`
- `GET /health` — health check

## Running the Flutter app

```bash
cd flutter_ui
flutter pub get
flutter run
```

The app calls the search server at a configurable base URL (open the
settings dialog in-app to change it).  Pick the address that matches your
target:

| Target | Base URL |
|---|---|
| iOS simulator / web | `http://localhost:8001` |
| Android emulator | `http://10.0.2.2:8001` |
| Physical device | `http://<your-machine-ip>:8001` |

Make sure the search server is running before you send queries.
