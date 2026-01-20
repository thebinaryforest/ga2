import pytest
from alerts.models import Dataset


@pytest.mark.django_db
def test_dataset_gbif_key_immutable():
    """We cannot change the dataset key (to avoid creating inconsistencies in a denormalized field on the Observation model)"""
    dataset = Dataset.objects.create(name="Test Dataset", gbif_dataset_key="abc-123")
    dataset.gbif_dataset_key = "changed"
    with pytest.raises(ValueError, match="cannot be changed"):
        dataset.save()
