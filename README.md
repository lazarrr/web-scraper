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
