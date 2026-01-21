from django.contrib.auth.models import AbstractUser
from django.contrib.gis.db import models
from django.db.models import Value
from django.db.models.functions import Concat
from django.db.models.expressions import Func
from django.utils import timezone

DATA_SRID = 3857  # Let's keep everything in Google Mercator to avoid reprojections

class CustomUser(AbstractUser):
    def get_alerts(self):
        """Get all alerts for this user, with species and datasets prefetched."""
        return self.alert_set.prefetch_related('species', 'datasets')

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

    def __str__(self):
        return self.scientific_name

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


class Alert(models.Model):
    """
    User-defined alert that tracks observations matching certain filters.
    Observations matching the filters are tracked in AlertObservation (unseen only).
    """

    class EmailFrequency(models.TextChoices):
        NEVER = "never", "Never"
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"

    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)

    # Filters (empty M2M = no filter on that dimension = all)
    species = models.ManyToManyField(Species, blank=True)
    datasets = models.ManyToManyField(Dataset, blank=True)
    # Later: areas = models.ManyToManyField('Area', blank=True)

    # Settings
    email_frequency = models.CharField(
        max_length=10,
        choices=EmailFrequency.choices,
        default=EmailFrequency.DAILY,
    )
    auto_mark_seen_after_days = models.PositiveIntegerField(default=365)

    # Denormalized count (updated by sync_alerts and on manual status changes)
    unseen_count = models.PositiveIntegerField(default=0)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    last_email_sent_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Alert '{self.name}' ({self.user.username})"

    def get_matching_observations(self):
        """Get Observation queryset matching this alert's filters (AND logic)."""
        qs = Observation.objects.all()

        if self.species.exists():
            qs = qs.filter(species__in=self.species.all())

        if self.datasets.exists():
            qs = qs.filter(source_dataset__in=self.datasets.all())

        # Later: spatial filter
        # if self.areas.exists():
        #     qs = qs.filter(location__intersects=self.combined_area)

        return qs

    def get_matching_observation_count(self):
        """Count observations matching this alert's filters. Queries Observation table."""
        return self.get_matching_observations().count()

    def get_unseen_observation_count(self):
        """
        Return the unseen observation count.

        Returns the denormalized `unseen_count` field (no DB query).
        This is updated by sync_alerts after each import.
        """
        return self.unseen_count

    def get_seen_observation_count(self):
        """
         Count seen observations (total matching minus unseen).

        Note: This queries the Observation table for total count.
        """
        return self.get_matching_observation_count() - self.unseen_count

    def should_send_email(self):
        """Check if enough time has passed since last email based on frequency."""
        from datetime import timedelta

        if self.email_frequency == self.EmailFrequency.NEVER:
            return False

        if self.last_email_sent_at is None:
            return True

        delta = {
            "daily": timedelta(days=1),
            "weekly": timedelta(days=7),
            "monthly": timedelta(days=30),
        }[self.email_frequency]

        return timezone.now() - self.last_email_sent_at >= delta

    def get_new_observations_since_last_email(self):
        """Get AlertObservation entries added since last email (or alert creation)."""
        since = self.last_email_sent_at or self.created_at
        return self.alertobservation_set.filter(first_seen_in_alert__gt=since)


class AlertObservation(models.Model):
    """
    Tracks UNSEEN observations for an alert.

    - Created when a new observation matches the alert's filters
    - Deleted when user marks it as seen (manual or auto after X days)
    - Deleted when observation disappears from Observation table

    Note: stable_id is NOT a ForeignKey to Observation because Observation
    is truncated/reloaded nightly. We use stable_id (UUID) to match records.
    """

    alert = models.ForeignKey(Alert, on_delete=models.CASCADE)
    stable_id = models.UUIDField()

    # Denormalized from Observation for auto-mark-as-seen without expensive join
    observation_date = models.DateField()

    first_seen_in_alert = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["alert", "stable_id"], name="unique_alert_observation"
            )
        ]
        indexes = [
            models.Index(fields=["alert", "observation_date"]),
            models.Index(fields=["alert", "first_seen_in_alert"]),
            models.Index(fields=["stable_id"]),
        ]

    def __str__(self):
        return f"AlertObservation {self.stable_id} for {self.alert}"

