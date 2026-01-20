from django.contrib.auth.models import AbstractUser
from django.contrib.gis.db import models
from django.db.models import Value
from django.db.models.functions import Concat
from django.db.models.expressions import Func

DATA_SRID = 3857  # Let's keep everything in Google Mercator to avoid reprojections

class CustomUser(AbstractUser):
    pass

class Dataset(models.Model):
    name = models.TextField()
    gbif_dataset_key = models.CharField(max_length=255, unique=True)

    def __str__(self):
        return f"Dataset {self.name} ({self.gbif_dataset_key})"

    def save(self, *args, **kwargs):
        # Prevent changing gbif_dataset_key after creation (to avoid having to cascade updates to Observation.source_dataset_gbif_key)
        if self.pk is not None:
            old = Dataset.objects.filter(pk=self.pk).values_list("gbif_dataset_key", flat=True).first()
            if old != self.gbif_dataset_key:
                raise ValueError("gbif_dataset_key cannot be changed after creation")
        super().save(*args, **kwargs)


class Species(models.Model):
    scientific_name = models.CharField(max_length=100)
    vernacular_name = models.CharField(max_length=100, blank=True)
    gbif_taxon_key = models.IntegerField(unique=True)

class Observation(models.Model):
    # Pay attention to the fact that this model actually has 4(!) different "identifiers" which serve different
    # purposes. gbif_id, occurrence_id and stable_id are documented below, Django also adds the usual and implicit "pk"
    # field.

    # The GBIF-assigned identifier. We show it to the user (links to GBIF.org, ...) but don't rely on it as a stable
    # identifier anymore. See: https://github.com/riparias/gbif-alert/issues/35#issuecomment-944073702 and
    # https://github.com/gbif/pipelines/issues/604,
    gbif_id = models.CharField(max_length=100)

    # The raw occurrenceId GBIF field, as provided by GBIF data providers retrieved from the data download.
    # It is an important data source, we use it to compute stable_id
    occurrence_id = models.TextField()

    source_dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE)

    # Denormalized copy of source_dataset.gbif_dataset_key. This redundancy is intentional:
    # PostgreSQL generated columns cannot reference other tables, so we need this value locally
    # to compute stable_id at the database level. Automatically synced via save().
    source_dataset_gbif_key = models.CharField(max_length=255, editable=False)

    # A stable identifier for this observation, computed as MD5(source_dataset_gbif_key | occurrence_id).
    # Stored as UUID (128 bits = MD5 output size) for efficient storage and indexing at scale.
    # Computed automatically by PostgreSQL on insert/update via a generated column.
    stable_id = models.GeneratedField(
        expression=Func(
            Concat(
                "source_dataset_gbif_key",
                Value("|"),
                "occurrence_id",
                output_field=models.TextField(),
            ),
            function="md5",
            template="(%(function)s(%(expressions)s))::uuid",
        ),
        output_field=models.UUIDField(),
        db_persist=True,
    )

    species = models.ForeignKey(Species, on_delete=models.CASCADE)
    location = models.PointField(blank=True, null=True, srid=DATA_SRID)
    date = models.DateField()
    individual_count = models.IntegerField(blank=True, null=True)
    locality = models.TextField(blank=True)
    municipality = models.TextField(blank=True)
    basis_of_record = models.TextField(blank=True)
    recorded_by = models.TextField(blank=True)
    coordinate_uncertainty_in_meters = models.FloatField(blank=True, null=True)
    references = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["stable_id"]),
        ]

    def __str__(self):
        return f"Observation {self.gbif_id} ({self.stable_id})"

    def save(self, *args, **kwargs):
        # Ensure denormalized source_dataset_gbif_key is up to date.
        # Beware: this won't be called on bulk_create/bulk_update!
        self.source_dataset_gbif_key = self.source_dataset.gbif_dataset_key
        super().save(*args, **kwargs)

