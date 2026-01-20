import csv
import time
import zipfile
from datetime import date
from io import TextIOWrapper

from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand
from django.db import connection, transaction

from alerts.models import DATA_SRID, Dataset, Observation, Species

WGS84_SRID = 4326
BATCH_SIZE = 10000


class Command(BaseCommand):
    help = "Import observations from a GBIF DwC-A zip file"

    def add_arguments(self, parser):
        parser.add_argument("zip_file", type=str, help="Path to the GBIF DwC-A zip file")

    def handle(self, *args, **options):
        zip_path = options["zip_file"]
        self.skipped_count = 0
        self.imported_count = 0

        start_time = time.perf_counter()

        self.stdout.write(f"Opening {zip_path}...")

        with zipfile.ZipFile(zip_path, "r") as zf:
            with zf.open("occurrence.txt") as f:
                text_file = TextIOWrapper(f, encoding="utf-8")
                reader = csv.reader(text_file, delimiter="\t")

                # Parse header to build column index mapping
                header = next(reader)
                self.col = {name: idx for idx, name in enumerate(header)}

                self._import_observations(reader)

        elapsed = time.perf_counter() - start_time
        rate = self.imported_count / elapsed if elapsed > 0 else 0

        self.stdout.write(
            self.style.SUCCESS(
                f"Import complete: {self.imported_count} imported, {self.skipped_count} skipped "
                f"in {elapsed:.2f}s ({rate:.0f} rows/s)"
            )
        )

    def _get(self, row, column_name, default=""):
        """Get a column value by name, returning default if column doesn't exist."""
        idx = self.col.get(column_name)
        if idx is None or idx >= len(row):
            return default
        return row[idx]

    def _import_observations(self, reader):
        # Pre-load existing Species and Dataset for fast lookups
        self.stdout.write("Loading existing Species and Datasets...")
        species_cache = {s.gbif_taxon_key: s for s in Species.objects.all()}
        dataset_cache = {d.gbif_dataset_key: d for d in Dataset.objects.all()}

        # Truncate observations (fast)
        self.stdout.write("Truncating Observation table...")
        with connection.cursor() as cursor:
            cursor.execute(f"TRUNCATE TABLE {Observation._meta.db_table} RESTART IDENTITY")

        self.stdout.write("Processing rows...")
        observations_batch = []
        new_species = {}  # gbif_taxon_key -> Species (not yet saved)
        new_datasets = {}  # gbif_dataset_key -> Dataset (not yet saved)

        for row in reader:
            obs_data = self._parse_row(row, species_cache, dataset_cache, new_species, new_datasets)
            if obs_data is None:
                continue

            observations_batch.append(obs_data)

            if len(observations_batch) >= BATCH_SIZE:
                self._flush_batch(
                    observations_batch, species_cache, dataset_cache, new_species, new_datasets
                )
                observations_batch = []
                new_species = {}
                new_datasets = {}

        # Flush remaining
        if observations_batch:
            self._flush_batch(
                observations_batch, species_cache, dataset_cache, new_species, new_datasets
            )

    def _parse_row(self, row, species_cache, dataset_cache, new_species, new_datasets):
        """Parse a CSV row and return observation data dict, or None if invalid."""
        try:
            gbif_id = self._get(row, "gbifID").strip()

            # Required: speciesKey
            species_key_str = self._get(row, "speciesKey").strip()
            if not species_key_str:
                self.stderr.write(f"Skipping row: missing speciesKey (gbifID={gbif_id})")
                self.skipped_count += 1
                return None
            species_key = int(species_key_str)

            # Required: date (try eventDate first, fall back to year/month/day)
            obs_date = self._parse_date(row)
            if obs_date is None:
                self.stderr.write(f"Skipping row: missing date (gbifID={gbif_id})")
                self.skipped_count += 1
                return None

            # Required: datasetKey
            dataset_key = self._get(row, "datasetKey").strip()
            if not dataset_key:
                self.stderr.write(f"Skipping row: missing datasetKey (gbifID={gbif_id})")
                self.skipped_count += 1
                return None

            # Ensure Species exists (in cache or pending creation)
            if species_key not in species_cache and species_key not in new_species:
                new_species[species_key] = Species(
                    gbif_taxon_key=species_key,
                    scientific_name=self._get(row, "species").strip()[:100] or f"Species {species_key}",
                    vernacular_name=self._get(row, "vernacularName").strip()[:100],
                )

            # Ensure Dataset exists (in cache or pending creation)
            if dataset_key not in dataset_cache and dataset_key not in new_datasets:
                new_datasets[dataset_key] = Dataset(
                    gbif_dataset_key=dataset_key,
                    name=self._get(row, "datasetName").strip() or dataset_key,
                )

            # Parse optional fields
            location = self._parse_location(row)
            individual_count = self._parse_int(self._get(row, "individualCount"))
            coordinate_uncertainty = self._parse_float(self._get(row, "coordinateUncertaintyInMeters"))

            return {
                "gbif_id": gbif_id,
                "occurrence_id": self._get(row, "occurrenceID").strip(),
                "species_key": species_key,
                "dataset_key": dataset_key,
                "date": obs_date,
                "location": location,
                "individual_count": individual_count,
                "locality": self._get(row, "locality").strip(),
                "municipality": self._get(row, "municipality").strip(),
                "basis_of_record": self._get(row, "basisOfRecord").strip(),
                "recorded_by": self._get(row, "recordedBy").strip(),
                "coordinate_uncertainty_in_meters": coordinate_uncertainty,
                "references": self._get(row, "references").strip(),
            }

        except (IndexError, ValueError) as e:
            self.stderr.write(f"Skipping row: parse error ({e})")
            self.skipped_count += 1
            return None

    def _parse_date(self, row):
        """Parse date from eventDate or year/month/day fields."""
        event_date = self._get(row, "eventDate").strip()
        if event_date:
            # eventDate can be a range like "2009-07-07/2013-09-11", take the first date
            first_date = event_date.split("/")[0]
            try:
                parts = first_date.split("-")
                if len(parts) >= 3:
                    return date(int(parts[0]), int(parts[1]), int(parts[2]))
            except (ValueError, IndexError):
                pass

        # Fall back to year/month/day
        year = self._parse_int(self._get(row, "year"))
        month = self._parse_int(self._get(row, "month"))
        day = self._parse_int(self._get(row, "day"))

        if year and month and day:
            try:
                return date(year, month, day)
            except ValueError:
                pass

        return None

    def _parse_location(self, row):
        """Parse and transform coordinates from WGS84 to target SRID."""
        lat_str = self._get(row, "decimalLatitude").strip()
        lon_str = self._get(row, "decimalLongitude").strip()

        if not lat_str or not lon_str:
            return None

        try:
            lat = float(lat_str)
            lon = float(lon_str)
            point = Point(lon, lat, srid=WGS84_SRID)
            point.transform(DATA_SRID)
            return point
        except (ValueError, Exception):
            return None

    def _parse_int(self, value):
        """Parse integer, return None if empty or invalid."""
        value = value.strip()
        if not value:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    def _parse_float(self, value):
        """Parse float, return None if empty or invalid."""
        value = value.strip()
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def _flush_batch(self, observations_batch, species_cache, dataset_cache, new_species, new_datasets):
        """Create new Species/Dataset and bulk insert observations."""
        with transaction.atomic():
            # Create new datasets
            if new_datasets:
                Dataset.objects.bulk_create(new_datasets.values(), ignore_conflicts=True)
                # Refresh cache with newly created (and any that existed due to race)
                for ds in Dataset.objects.filter(gbif_dataset_key__in=new_datasets.keys()):
                    dataset_cache[ds.gbif_dataset_key] = ds

            # Create new species
            if new_species:
                Species.objects.bulk_create(new_species.values(), ignore_conflicts=True)
                # Refresh cache
                for sp in Species.objects.filter(gbif_taxon_key__in=new_species.keys()):
                    species_cache[sp.gbif_taxon_key] = sp

            # Build Observation objects
            observations = []
            for obs_data in observations_batch:
                species = species_cache.get(obs_data["species_key"])
                dataset = dataset_cache.get(obs_data["dataset_key"])

                if not species or not dataset:
                    self.stderr.write(
                        f"Skipping: missing species or dataset after creation (gbifID={obs_data['gbif_id']})"
                    )
                    self.skipped_count += 1
                    continue

                observations.append(
                    Observation(
                        gbif_id=obs_data["gbif_id"],
                        occurrence_id=obs_data["occurrence_id"],
                        source_dataset=dataset,
                        source_dataset_gbif_key=dataset.gbif_dataset_key,
                        species=species,
                        date=obs_data["date"],
                        location=obs_data["location"],
                        individual_count=obs_data["individual_count"],
                        locality=obs_data["locality"],
                        municipality=obs_data["municipality"],
                        basis_of_record=obs_data["basis_of_record"],
                        recorded_by=obs_data["recorded_by"],
                        coordinate_uncertainty_in_meters=obs_data["coordinate_uncertainty_in_meters"],
                        references=obs_data["references"],
                    )
                )

            if observations:
                Observation.objects.bulk_create(observations)
                self.imported_count += len(observations)

        self.stdout.write(f"  Processed {self.imported_count} observations...")
