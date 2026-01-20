from datetime import date

import pytest
from alerts.models import Dataset, Observation, Species


@pytest.fixture
def species():
    return Species.objects.create(
        scientific_name="Vespa velutina",
        vernacular_name="Asian hornet",
        gbif_taxon_key=1311477,
    )


@pytest.mark.django_db
def test_observation_stable_id_is_computed(species):
    dataset = Dataset.objects.create(name="Test Dataset", gbif_dataset_key="abc-123")
    obs = Observation.objects.create(
        gbif_id="gbif-1",
        occurrence_id="occ-1",
        source_dataset=dataset,
        species=species,
        date=date(2024, 1, 15),
    )
    obs.refresh_from_db()
    assert obs.stable_id is not None
    # The "parent" denormalized field has also been set
    assert obs.source_dataset_gbif_key == "abc-123"


@pytest.mark.django_db
def test_stable_id_changes_when_occurrence_id_changes(species):
    dataset = Dataset.objects.create(name="Test Dataset", gbif_dataset_key="abc-123")
    obs = Observation.objects.create(
        gbif_id="gbif-1",
        occurrence_id="occ-1",
        source_dataset=dataset,
        species=species,
        date=date(2024, 1, 15),
    )
    obs.refresh_from_db()
    original_stable_id = obs.stable_id

    obs.occurrence_id = "occ-2"
    obs.save()
    obs.refresh_from_db()

    assert obs.stable_id != original_stable_id


@pytest.mark.django_db
def test_stable_id_changes_when_source_dataset_changes(species):
    dataset1 = Dataset.objects.create(name="Dataset 1", gbif_dataset_key="key-111")
    dataset2 = Dataset.objects.create(name="Dataset 2", gbif_dataset_key="key-222")
    obs = Observation.objects.create(
        gbif_id="gbif-1",
        occurrence_id="occ-1",
        source_dataset=dataset1,
        species=species,
        date=date(2024, 1, 15),
    )
    obs.refresh_from_db()
    original_stable_id = obs.stable_id

    obs.source_dataset = dataset2
    obs.save()
    obs.refresh_from_db()

    assert obs.stable_id != original_stable_id
    assert obs.source_dataset_gbif_key == "key-222"


@pytest.mark.django_db
def test_stable_id_unchanged_when_gbif_id_changes(species):
    dataset = Dataset.objects.create(name="Test Dataset", gbif_dataset_key="abc-123")
    obs = Observation.objects.create(
        gbif_id="gbif-1",
        occurrence_id="occ-1",
        source_dataset=dataset,
        species=species,
        date=date(2024, 1, 15),
    )
    obs.refresh_from_db()
    original_stable_id = obs.stable_id

    obs.gbif_id = "gbif-changed"
    obs.save()
    obs.refresh_from_db()

    assert obs.stable_id == original_stable_id
