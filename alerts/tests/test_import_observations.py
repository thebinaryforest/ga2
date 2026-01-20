import csv
import io
import tempfile
import zipfile
from datetime import date

import pytest
from django.core.management import call_command

from alerts.models import Dataset, Observation, Species


def create_test_zip(rows, header=None):
    """Create a temporary DwC-A zip file with the given rows."""
    if header is None:
        header = [
            "gbifID", "references", "datasetName", "basisOfRecord", "occurrenceID",
            "recordedBy", "individualCount", "eventDate", "year", "month", "day",
            "municipality", "locality", "decimalLatitude", "decimalLongitude",
            "coordinateUncertaintyInMeters", "vernacularName", "datasetKey",
            "speciesKey", "species",
        ]

    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    with zipfile.ZipFile(tmp.name, "w") as zf:
        output = io.StringIO()
        writer = csv.writer(output, delimiter="\t")
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)
        zf.writestr("occurrence.txt", output.getvalue())

    return tmp.name


def make_row(
    gbif_id="123",
    occurrence_id="occ-1",
    dataset_key="ds-key-1",
    dataset_name="Test Dataset",
    species_key="12345",
    species_name="Vespa velutina",
    event_date="2024-01-15",
    year="",
    month="",
    day="",
    lat="51.0",
    lon="4.0",
    **kwargs,
):
    """Create a test row with sensible defaults."""
    return [
        gbif_id,  # gbifID
        kwargs.get("references", ""),  # references
        dataset_name,  # datasetName
        kwargs.get("basis_of_record", "HUMAN_OBSERVATION"),  # basisOfRecord
        occurrence_id,  # occurrenceID
        kwargs.get("recorded_by", ""),  # recordedBy
        kwargs.get("individual_count", ""),  # individualCount
        event_date,  # eventDate
        year,  # year
        month,  # month
        day,  # day
        kwargs.get("municipality", ""),  # municipality
        kwargs.get("locality", ""),  # locality
        lat,  # decimalLatitude
        lon,  # decimalLongitude
        kwargs.get("coordinate_uncertainty", ""),  # coordinateUncertaintyInMeters
        kwargs.get("vernacular_name", ""),  # vernacularName
        dataset_key,  # datasetKey
        species_key,  # speciesKey
        species_name,  # species
    ]


@pytest.fixture
def simple_zip():
    """A zip file with a single valid row."""
    rows = [make_row()]
    return create_test_zip(rows)


@pytest.mark.django_db
class TestImportObservations:

    def test_imports_single_observation(self, simple_zip):
        call_command("import_observations", simple_zip)

        assert Observation.objects.count() == 1
        assert Species.objects.count() == 1
        assert Dataset.objects.count() == 1

        obs = Observation.objects.first()
        assert obs.gbif_id == "123"
        assert obs.occurrence_id == "occ-1"
        assert obs.date == date(2024, 1, 15)
        assert obs.species.scientific_name == "Vespa velutina"
        assert obs.source_dataset.gbif_dataset_key == "ds-key-1"

    def test_imports_multiple_observations(self):
        rows = [
            make_row(gbif_id="1", occurrence_id="occ-1"),
            make_row(gbif_id="2", occurrence_id="occ-2"),
            make_row(gbif_id="3", occurrence_id="occ-3"),
        ]
        zip_path = create_test_zip(rows)

        call_command("import_observations", zip_path)

        assert Observation.objects.count() == 3
        assert Species.objects.count() == 1  # Same species for all
        assert Dataset.objects.count() == 1  # Same dataset for all

    def test_creates_multiple_species(self):
        rows = [
            make_row(gbif_id="1", species_key="100", species_name="Species A"),
            make_row(gbif_id="2", species_key="200", species_name="Species B"),
            make_row(gbif_id="3", species_key="300", species_name="Species C"),
        ]
        zip_path = create_test_zip(rows)

        call_command("import_observations", zip_path)

        assert Species.objects.count() == 3
        assert set(Species.objects.values_list("scientific_name", flat=True)) == {
            "Species A", "Species B", "Species C"
        }

    def test_creates_multiple_datasets(self):
        rows = [
            make_row(gbif_id="1", dataset_key="ds-1", dataset_name="Dataset 1"),
            make_row(gbif_id="2", dataset_key="ds-2", dataset_name="Dataset 2"),
        ]
        zip_path = create_test_zip(rows)

        call_command("import_observations", zip_path)

        assert Dataset.objects.count() == 2

    def test_reuses_existing_species(self):
        Species.objects.create(
            gbif_taxon_key=12345,
            scientific_name="Existing Species",
            vernacular_name="",
        )
        rows = [make_row(species_key="12345", species_name="Vespa velutina")]
        zip_path = create_test_zip(rows)

        call_command("import_observations", zip_path)

        assert Species.objects.count() == 1
        # Name should be unchanged (existing species reused)
        assert Species.objects.first().scientific_name == "Existing Species"

    def test_reuses_existing_dataset(self):
        Dataset.objects.create(
            gbif_dataset_key="ds-key-1",
            name="Existing Dataset",
        )
        rows = [make_row(dataset_key="ds-key-1", dataset_name="New Name")]
        zip_path = create_test_zip(rows)

        call_command("import_observations", zip_path)

        assert Dataset.objects.count() == 1
        assert Dataset.objects.first().name == "Existing Dataset"

    @pytest.mark.django_db(transaction=True)
    def test_truncates_observations_on_reimport(self):
        # First import
        rows = [make_row(gbif_id="1")]
        zip_path = create_test_zip(rows)
        call_command("import_observations", zip_path)
        assert Observation.objects.count() == 1

        # Second import with different data
        rows = [
            make_row(gbif_id="2", occurrence_id="occ-2"),
            make_row(gbif_id="3", occurrence_id="occ-3"),
        ]
        zip_path = create_test_zip(rows)
        call_command("import_observations", zip_path)

        # Old observation should be gone, only new ones remain
        assert Observation.objects.count() == 2
        assert set(Observation.objects.values_list("gbif_id", flat=True)) == {"2", "3"}

    def test_skips_row_missing_species_key(self):
        rows = [
            make_row(gbif_id="1", species_key=""),  # Missing
            make_row(gbif_id="2", species_key="12345"),  # Valid
        ]
        zip_path = create_test_zip(rows)

        call_command("import_observations", zip_path)

        assert Observation.objects.count() == 1
        assert Observation.objects.first().gbif_id == "2"

    def test_skips_row_missing_dataset_key(self):
        rows = [
            make_row(gbif_id="1", dataset_key=""),  # Missing
            make_row(gbif_id="2", dataset_key="ds-key-1"),  # Valid
        ]
        zip_path = create_test_zip(rows)

        call_command("import_observations", zip_path)

        assert Observation.objects.count() == 1
        assert Observation.objects.first().gbif_id == "2"

    def test_skips_row_missing_date(self):
        rows = [
            make_row(gbif_id="1", event_date="", year="", month="", day=""),  # Missing
            make_row(gbif_id="2", event_date="2024-01-15"),  # Valid
        ]
        zip_path = create_test_zip(rows)

        call_command("import_observations", zip_path)

        assert Observation.objects.count() == 1
        assert Observation.objects.first().gbif_id == "2"

    def test_parses_date_from_event_date(self):
        rows = [make_row(event_date="2023-06-15")]
        zip_path = create_test_zip(rows)

        call_command("import_observations", zip_path)

        assert Observation.objects.first().date == date(2023, 6, 15)

    def test_parses_date_range_takes_first(self):
        rows = [make_row(event_date="2023-06-15/2023-06-20")]
        zip_path = create_test_zip(rows)

        call_command("import_observations", zip_path)

        assert Observation.objects.first().date == date(2023, 6, 15)

    def test_parses_date_from_year_month_day_fallback(self):
        rows = [make_row(event_date="", year="2022", month="3", day="10")]
        zip_path = create_test_zip(rows)

        call_command("import_observations", zip_path)

        assert Observation.objects.first().date == date(2022, 3, 10)

    def test_transforms_coordinates_to_mercator(self):
        # WGS84 coordinates for Brussels
        rows = [make_row(lat="50.85", lon="4.35")]
        zip_path = create_test_zip(rows)

        call_command("import_observations", zip_path)

        obs = Observation.objects.first()
        assert obs.location is not None
        assert obs.location.srid == 3857  # Mercator
        # Rough check that transformation happened (Mercator coords are much larger)
        assert abs(obs.location.x) > 100000
        assert abs(obs.location.y) > 100000

    def test_handles_missing_coordinates(self):
        rows = [make_row(lat="", lon="")]
        zip_path = create_test_zip(rows)

        call_command("import_observations", zip_path)

        assert Observation.objects.first().location is None

    def test_imports_optional_fields(self):
        rows = [make_row(
            individual_count="5",
            municipality="Brussels",
            locality="Park",
            basis_of_record="HUMAN_OBSERVATION",
            recorded_by="John Doe",
            coordinate_uncertainty="100.5",
            references="http://example.com",
        )]
        zip_path = create_test_zip(rows)

        call_command("import_observations", zip_path)

        obs = Observation.objects.first()
        assert obs.individual_count == 5
        assert obs.municipality == "Brussels"
        assert obs.locality == "Park"
        assert obs.basis_of_record == "HUMAN_OBSERVATION"
        assert obs.recorded_by == "John Doe"
        assert obs.coordinate_uncertainty_in_meters == 100.5
        assert obs.references == "http://example.com"

    def test_stable_id_is_computed(self):
        rows = [make_row()]
        zip_path = create_test_zip(rows)

        call_command("import_observations", zip_path)

        obs = Observation.objects.first()
        assert obs.stable_id is not None
