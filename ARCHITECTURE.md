# Architecture

This document describes the design constraints, decisions, and architecture of GA2 (GBIF Alert 2).

## Overview

GA2 is a Django application for tracking biodiversity observations from GBIF (Global Biodiversity Information Facility). Users can create alerts to monitor specific species or datasets and receive notifications when new observations appear.

## Design Constraints

### Scale
- Target: ~100M observation records at EU scale
- Observations are refreshed nightly via full truncate/reload from GBIF
- Must support fast queries for map display (MVT tiles) and paginated lists

### Data Characteristics
- GBIF data is external and refreshed frequently
- GBIF-assigned IDs (`gbifID`) are not stable across data updates
- Observations need a stable identifier that persists across reimports

### User Requirements
- Users create alerts with filters (species, datasets, geographic areas)
- Track seen/unseen status per observation per alert
- Email notifications for new observations (configurable frequency)
- Auto-mark old observations as seen after configurable period

## Data Model

### Core Entities

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Species   │     │   Dataset   │     │ CustomUser  │
├─────────────┤     ├─────────────┤     ├─────────────┤
│ gbif_taxon_ │     │ gbif_dataset│     │ (extends    │
│ key (unique)│     │ _key (unique│     │  AbstractUser)
│ scientific_ │     │ name        │     └──────┬──────┘
│ name        │     └──────┬──────┘            │
└──────┬──────┘            │                   │
       │                   │                   │
       │    ┌──────────────┴───────────┐       │
       │    │        Observation       │       │
       │    ├──────────────────────────┤       │
       └────┤ species (FK)             │       │
            │ source_dataset (FK)      ├───────┘
            │ stable_id (generated)    │       │
            │ gbif_id                  │       │
            │ occurrence_id            │       │
            │ location (Point)         │  ┌────┴────────┐
            │ date                     │  │    Alert    │
            └──────────────────────────┘  ├─────────────┤
                           ▲              │ user (FK)   │
                           │              │ name        │
                  (join on stable_id)     │ species (M2M)
                           │              │ datasets(M2M)
            ┌──────────────┴───────────┐  │ email_freq  │
            │    AlertObservation      │  │ auto_mark_  │
            ├──────────────────────────┤  │ seen_after_ │
            │ alert (FK)               ├──┤ days        │
            │ stable_id (UUID)         │  │ unseen_count│
            │ observation_date         │  └─────────────┘
            │ first_seen_in_alert      │
            └──────────────────────────┘
```

### Key Design Decisions

#### 1. Observation Identifiers

The `Observation` model has four distinct identifiers:

| Field | Purpose | Stability |
|-------|---------|-----------|
| `pk` | Django primary key | Resets on each import |
| `gbif_id` | GBIF-assigned ID for display/links | **Not stable** across reimports |
| `occurrence_id` | Raw occurrenceId from data provider | Stable per dataset |
| `stable_id` | Computed UUID for tracking | **Stable** across reimports |

**Why `gbif_id` is not stable:** GBIF pipelines can reassign IDs when data is reprocessed. See [gbif/pipelines#604](https://github.com/gbif/pipelines/issues/604).

**How `stable_id` works:**
```sql
stable_id = MD5(source_dataset_gbif_key || '|' || occurrence_id)::uuid
```

This is implemented as a PostgreSQL generated column for automatic computation:

```python
stable_id = models.GeneratedField(
    expression=Func(
        Concat("source_dataset_gbif_key", Value("|"), "occurrence_id"),
        function="md5",
        template="(%(function)s(%(expressions)s))::uuid",
    ),
    output_field=models.UUIDField(),
    db_persist=True,
)
```

#### 2. Denormalized `source_dataset_gbif_key`

`Observation.source_dataset_gbif_key` duplicates `Dataset.gbif_dataset_key` because:
- PostgreSQL generated columns cannot reference other tables
- Needed locally to compute `stable_id`
- Synced automatically in `Observation.save()` (but not in `bulk_create`)

`Dataset.gbif_dataset_key` is immutable after creation to prevent cascading update issues.

#### 3. AlertObservation: Unseen-Only Tracking

`AlertObservation` tracks **only unseen observations**:
- Row exists → observation is unseen
- Row deleted → observation is seen (or no longer exists)

**Benefits:**
- Table stays small (users mark things as seen over time)
- "Mark all as seen" = simple `DELETE WHERE alert_id=X`
- Count unseen = `COUNT(*)` for alert

**Trade-off:**
- "Get seen observations" requires computing: (matching observations) - (unseen)
- Acceptable because users primarily care about unseen

#### 4. Alert Filters: AND Logic

When an alert has multiple filter types, they combine with AND:
```python
# Alert with species=[A, B] and datasets=[X, Y]
# Matches: species IN (A, B) AND dataset IN (X, Y)
```

Empty filter means "no restriction" (matches all):
```python
# Alert with species=[] and datasets=[X]
# Matches: any species AND dataset IN (X)
```

#### 5. Denormalized `observation_date` in AlertObservation

Copied from `Observation.date` to enable auto-mark-as-seen without joining 100M rows:
```python
# Auto-mark observations older than threshold
AlertObservation.objects.filter(
    alert=alert,
    observation_date__lt=cutoff_date
).delete()
```

## Database

### PostgreSQL with PostGIS

- **PostGIS** for spatial data (observation locations, future area filters)
- **SRID 3857** (Web Mercator) used throughout to avoid runtime reprojections
- Coordinates transformed from WGS84 (SRID 4326) during import

### Indexing Strategy

**Observation:**
- `stable_id` - for joining with AlertObservation
- (spatial index on `location` for MVT queries)

**AlertObservation:**
- `(alert, observation_date)` - for auto-mark-as-seen
- `(alert, first_seen_in_alert)` - for "new since last email"
- `stable_id` - for cleanup and joining with Observation

### Performance Considerations

**Import (100M records):**
- `TRUNCATE` instead of `DELETE` for fast clearing
- `bulk_create` with batches of 10,000
- Species/Dataset cached in memory for O(1) lookups

**Future optimizations if needed:**
- PostgreSQL `COPY` for faster bulk insert
- `UNLOGGED` table during import
- Drop/recreate indexes around import

## Management Commands

### `import_observations`

```bash
uv run python manage.py import_observations <zip_file>
```

Imports observations from a GBIF Darwin Core Archive (DwC-A) zip file.

**Workflow:**
1. Parse header row for column name → index mapping
2. Truncate `Observation` table (fast)
3. Process rows in batches of 10,000:
   - Create missing `Species` and `Dataset` records
   - Transform coordinates from WGS84 to Mercator
   - Bulk insert observations
4. Report timing and throughput

**Skipped rows:** Missing speciesKey, datasetKey, or date

### `sync_alerts`

```bash
uv run python manage.py sync_alerts [--send-emails]
```

Synchronizes all alerts with current observations after data import.

**Workflow:**
1. **Cleanup:** Delete `AlertObservation` rows where `stable_id` no longer exists in `Observation`
2. **For each alert:**
   - Query matching observations based on filters
   - Insert new `AlertObservation` rows for new stable_ids
   - Delete old observations (auto-mark-as-seen based on `auto_mark_seen_after_days`)
   - Update `unseen_count`
3. **If `--send-emails`:** Send notifications for alerts with new observations (respects `email_frequency`)

**Intended usage:** Run after `import_observations` in nightly cron job:
```bash
uv run python manage.py import_observations /path/to/data.zip
uv run python manage.py sync_alerts --send-emails
```

## Future Considerations

### Spatial Filtering (Planned)

Alerts will support geographic area filters:
```python
class Alert(models.Model):
    areas = models.ManyToManyField('Area', blank=True)

class Area(models.Model):
    name = models.CharField(max_length=255)
    geometry = models.MultiPolygonField(srid=3857)
```

Query modification:
```python
if alert.areas.exists():
    combined = alert.areas.aggregate(union=Union('geometry'))['union']
    qs = qs.filter(location__intersects=combined)
```

### Partitioning (If Needed)

If `AlertObservation` grows too large, consider hash partitioning by `alert_id`:
- Queries always scoped to single alert → hits single partition
- Even distribution across partitions

### Email Implementation

Current email sending is a placeholder. Implementation should include:
- Alert details (name, filters)
- Count of new observations
- Sample observations with species, location, date
- Link to alert on website
- Respect `email_frequency` setting
