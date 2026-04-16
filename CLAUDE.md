# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

BigBill is a personal asset allocation tracker built with Django 6. It helps manage a five-layer investment portfolio (安全垫/债券核心/股票核心/另类对冲/卫星机会) with screenshot OCR recognition, rebalancing calculations, and performance tracking.

The app is in Chinese (zh-hans) and uses Asia/Shanghai timezone.

## Commands

```bash
# Install dependencies
uv sync

# Run dev server
uv run python manage.py runserver

# Database migrations
uv run python manage.py makemigrations
uv run python manage.py migrate

# Seed default 5-layer asset configuration
uv run python manage.py shell < assets/seed_data.py

# Run tests
uv run python manage.py test assets
```

## Architecture

Single Django app (`assets`) with server-rendered templates + JSON API endpoints.

### Key Models (`assets/models.py`)
- **AssetLayer** — 5 investment tiers with target allocation ratios
- **Holding** — individual positions linked to a layer; auto-calculates P&L on save
- **Snapshot** — point-in-time portfolio snapshots (JSONField for layer breakdowns)
- **Transaction** — buy/sell/transfer/withdraw records; `transfer`/`withdraw` types are used by Modified Dietz performance calculations
- **Upload** — screenshot upload with OCR processing status workflow (pending → processing → recognized → confirmed)
- **Setting** — key-value store for app config (LLM API keys, provider settings)

### Services (`assets/services/`)
- **ocr.py** — Screenshot recognition via Anthropic Claude API or OpenAI-compatible API (Ollama, vLLM, etc.). Images are compressed to max 800px before sending. Includes truncated JSON repair logic.
- **rebalance.py** — Rebalancing engine: deviation alerts (>3% warning, >5% critical), new fund allocation (fills largest gaps first), and drawdown response protocols.
- **performance.py** — Modified Dietz method for interval return calculation using snapshots and cash flow transactions.

### Views (`assets/views.py`)
Page views render Django templates; API endpoints (`api_*`) accept/return JSON. No DRF — plain `JsonResponse` with `json.loads(request.body)` pattern. `Decimal` values are serialized via a custom `DecimalEncoder`.

### URL structure
All routes are under the `assets` app namespace, mounted at root (`/`). Pages: `/`, `/holdings/`, `/upload/`, `/rebalance/`, `/history/`, `/checklist/`, `/settings/`. APIs: `/api/...`.

### Frontend
Server-rendered Django templates in `assets/templates/assets/` with a shared `base.html`. JavaScript is inline within templates. No frontend build step.

### LLM Provider Configuration
The app supports two OCR backends configured via the Setting model:
- `anthropic` — uses the `anthropic` Python SDK directly
- `openai_compatible` — uses `httpx` to call any OpenAI-compatible endpoint (Ollama, vLLM, LM Studio)

### Database
SQLite (`db.sqlite3`) at project root. No auth required — single-user personal tool.
