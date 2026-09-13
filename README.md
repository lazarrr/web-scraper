# university-scraper

Scrapes the [IMI PMF Kragujevac](https://imi.pmf.kg.ac.rs/) website,
extracts clean text from HTML pages, PDFs, and DOCX files, chunks the
content, generates embeddings with
`intfloat/multilingual-e5-large`, and upserts everything into a
persistent ChromaDB vector store.

## What it does

1. **Crawls** the university domain (breadth-first, respects `robots.txt`).
2. **Extracts** clean text — strips navigation, headers, footers, and
   other boilerplate from HTML, PDF, and DOCX resources.
3. **Chunks** text with a recursive character splitter (configurable
   size and overlap).
4. **Embeds** each chunk using `multilingual-e5-large` with the
   required `passage: ` prefix.
5. **Upserts** into a ChromaDB collection named `university_knowledge`.

Already-ingested URLs are tracked in `data/crawl_history.json` and
skipped on subsequent runs (set `CRAWL_HISTORY_ENABLED=false` to
force a full re-crawl).

For every URL the scraper also asks a local **Ollama** model to write a
short context summary and 1-2 questions the page can answer.  These are
stored alongside `type`, `chunks`, and `ingested_at` in
`crawl_history.json`.  Entries created before this feature was enabled
are backfilled automatically on the next run.  Set
`CONTEXT_GENERATION_ENABLED=false` to turn this off.

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
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

## Configuration

All settings live in `config/settings.py` and can be overridden via
environment variables or a `.env` file.  Key settings:

| Variable | Default | Description |
|---|---|---|
| `VECTOR_DB_PATH` | `./data/vector_store` | ChromaDB persist directory (shared with chatbot) |
| `CRAWLER_MAX_DEPTH` | `1` | Max crawl depth (0 = homepage only) |
| `CRAWLER_DELAY` | `1.0` | Seconds between requests |
| `CHUNK_SIZE` | `1000` | Character chunk size |
| `CHUNK_OVERLAP` | `200` | Character overlap between chunks |
| `CRAWL_HISTORY_ENABLED` | `true` | Skip already-ingested URLs |
| `CONTEXT_GENERATION_ENABLED` | `true` | Generate context + questions per URL via Ollama |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server address |
| `OLLAMA_MODEL` | `qwen2.5:7b-instruct-q4_K_M` | Ollama model used for generation |

## Running the search server (API for the Flutter app)

The FastAPI server in `src/api/search_server.py` exposes the vector search
API that the Flutter app talks to.  It reads the same ChromaDB collection
that the scraper populates, so **run the scraper first** to fill the store.

```bash
cd university-scraper
python -m venv .venv && source .venv/bin/activate
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
