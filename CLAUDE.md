# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GA2 (GBIF Alert 2) is a Django application designed to handle biodiversity observation data from GBIF at EU scale (~100M records). It uses PostgreSQL with PostGIS for spatial data support.

## Commands

**Run all tests:**
```bash
uv run pytest
```

**Run a single test file:**
```bash
uv run pytest alerts/tests/models/test_observation.py
```

**Run a specific test:**
```bash
uv run pytest alerts/tests/models/test_observation.py::test_observation_stable_id_is_computed
```

**Run Django management commands:**
```bash
uv run python manage.py <command>
```

**Apply migrations:**
```bash
uv run python manage.py migrate
```

## Architecture

- **Django 6.0** with **PostgreSQL/PostGIS** backend
- **uv** for dependency management (not pip/poetry)
- Custom user model at `alerts.CustomUser` for future extensibility
- Tests use **pytest-django** with `@pytest.mark.django_db` decorator for database access

### Key Models (alerts/models.py)

**Observation** has four distinct identifiers:
- `pk`: Standard Django primary key
- `gbif_id`: GBIF-assigned ID (displayed to users, not relied upon as stable)
- `occurrence_id`: Raw occurrenceId from GBIF data providers
- `stable_id`: Computed UUID via PostgreSQL generated column as `MD5(source_dataset_gbif_key | occurrence_id)::uuid`

The `stable_id` provides a consistent identifier across GBIF data updates. See the linked GitHub issues in the model for context on why `gbif_id` is not stable.

**Dataset** has an immutable `gbif_dataset_key` after creation (enforced in save()) to prevent cascading update issues with the denormalized `Observation.source_dataset_gbif_key` field.

### Design Decisions

- Planned: Wagtail CMS integration
- Planned: Vue.js SPA frontend with Vite