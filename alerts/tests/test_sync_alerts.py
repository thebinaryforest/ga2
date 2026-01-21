from datetime import date, timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from alerts.models import Alert, AlertObservation, CustomUser, Dataset, Observation, Species


@pytest.fixture
def user(db):
    return CustomUser.objects.create_user(username="testuser", password="testpass")


@pytest.fixture
def species(db):
    return Species.objects.create(
        scientific_name="Vespa velutina",
        vernacular_name="Asian hornet",
        gbif_taxon_key=1311477,
    )


@pytest.fixture
def species2(db):
    return Species.objects.create(
        scientific_name="Vespa crabro",
        vernacular_name="European hornet",
        gbif_taxon_key=1311478,
    )


@pytest.fixture
def dataset(db):
    return Dataset.objects.create(name="Test Dataset", gbif_dataset_key="ds-key-1")


@pytest.fixture
def dataset2(db):
    return Dataset.objects.create(name="Test Dataset 2", gbif_dataset_key="ds-key-2")


def create_observation(species, dataset, gbif_id, occurrence_id, obs_date=None):
    obs = Observation.objects.create(
        gbif_id=gbif_id,
        occurrence_id=occurrence_id,
        source_dataset=dataset,
        species=species,
        date=obs_date or date.today(),  # Use today to avoid auto-mark-as-seen
    )
    obs.refresh_from_db()
    return obs


@pytest.mark.django_db
class TestSyncAlertsCommand:
    def test_adds_new_observations_to_alert(self, user, species, dataset):
        alert = Alert.objects.create(user=user, name="Test Alert")
        obs = create_observation(species, dataset, "1", "occ-1")

        call_command("sync_alerts")

        assert AlertObservation.objects.filter(alert=alert).count() == 1
        alert_obs = AlertObservation.objects.get(alert=alert)
        assert alert_obs.stable_id == obs.stable_id
        assert alert_obs.observation_date == obs.date

    def test_updates_unseen_count(self, user, species, dataset):
        alert = Alert.objects.create(user=user, name="Test Alert")
        create_observation(species, dataset, "1", "occ-1")
        create_observation(species, dataset, "2", "occ-2")
        create_observation(species, dataset, "3", "occ-3")

        call_command("sync_alerts")

        alert.refresh_from_db()
        assert alert.unseen_count == 3

    def test_does_not_duplicate_existing_observations(self, user, species, dataset):
        alert = Alert.objects.create(user=user, name="Test Alert")
        obs = create_observation(species, dataset, "1", "occ-1")

        # First sync
        call_command("sync_alerts")
        assert AlertObservation.objects.filter(alert=alert).count() == 1

        # Second sync (no new observations)
        call_command("sync_alerts")
        assert AlertObservation.objects.filter(alert=alert).count() == 1

    def test_respects_species_filter(self, user, species, species2, dataset):
        alert = Alert.objects.create(user=user, name="Test Alert")
        alert.species.add(species)

        create_observation(species, dataset, "1", "occ-1")
        create_observation(species2, dataset, "2", "occ-2")

        call_command("sync_alerts")

        assert AlertObservation.objects.filter(alert=alert).count() == 1
        alert.refresh_from_db()
        assert alert.unseen_count == 1

    def test_respects_dataset_filter(self, user, species, dataset, dataset2):
        alert = Alert.objects.create(user=user, name="Test Alert")
        alert.datasets.add(dataset)

        create_observation(species, dataset, "1", "occ-1")
        create_observation(species, dataset2, "2", "occ-2")

        call_command("sync_alerts")

        assert AlertObservation.objects.filter(alert=alert).count() == 1
        alert.refresh_from_db()
        assert alert.unseen_count == 1

    def test_and_logic_for_filters(self, user, species, species2, dataset, dataset2):
        alert = Alert.objects.create(user=user, name="Test Alert")
        alert.species.add(species)
        alert.datasets.add(dataset)

        # Matches both filters
        create_observation(species, dataset, "1", "occ-1")
        # Wrong species
        create_observation(species2, dataset, "2", "occ-2")
        # Wrong dataset
        create_observation(species, dataset2, "3", "occ-3")

        call_command("sync_alerts")

        assert AlertObservation.objects.filter(alert=alert).count() == 1
        alert.refresh_from_db()
        assert alert.unseen_count == 1

    def test_removes_stale_observations(self, user, species, dataset):
        alert = Alert.objects.create(user=user, name="Test Alert")
        obs = create_observation(species, dataset, "1", "occ-1")

        # First sync
        call_command("sync_alerts")
        assert AlertObservation.objects.filter(alert=alert).count() == 1

        # Delete the observation (simulates it being removed from GBIF)
        obs.delete()

        # Second sync should clean up
        call_command("sync_alerts")
        assert AlertObservation.objects.filter(alert=alert).count() == 0
        alert.refresh_from_db()
        assert alert.unseen_count == 0

    def test_auto_marks_old_observations_as_seen(self, user, species, dataset):
        alert = Alert.objects.create(
            user=user, name="Test Alert", auto_mark_seen_after_days=30
        )

        # Create an old observation (more than 30 days ago)
        old_date = date.today() - timedelta(days=60)
        old_obs = create_observation(
            species, dataset, "1", "occ-1", obs_date=old_date
        )
        # Create a recent observation
        new_obs = create_observation(species, dataset, "2", "occ-2")

        call_command("sync_alerts")

        # Only the recent one should remain
        assert AlertObservation.objects.filter(alert=alert).count() == 1
        assert AlertObservation.objects.filter(
            alert=alert, stable_id=new_obs.stable_id
        ).exists()
        alert.refresh_from_db()
        assert alert.unseen_count == 1

    def test_multiple_alerts_independent(self, user, species, species2, dataset):
        alert1 = Alert.objects.create(user=user, name="Alert 1")
        alert1.species.add(species)

        alert2 = Alert.objects.create(user=user, name="Alert 2")
        alert2.species.add(species2)

        create_observation(species, dataset, "1", "occ-1")
        create_observation(species2, dataset, "2", "occ-2")

        call_command("sync_alerts")

        assert AlertObservation.objects.filter(alert=alert1).count() == 1
        assert AlertObservation.objects.filter(alert=alert2).count() == 1
        alert1.refresh_from_db()
        alert2.refresh_from_db()
        assert alert1.unseen_count == 1
        assert alert2.unseen_count == 1

    def test_same_observation_in_multiple_alerts(self, user, species, dataset):
        alert1 = Alert.objects.create(user=user, name="Alert 1")
        alert2 = Alert.objects.create(user=user, name="Alert 2")

        create_observation(species, dataset, "1", "occ-1")

        call_command("sync_alerts")

        # Both alerts should have the observation
        assert AlertObservation.objects.filter(alert=alert1).count() == 1
        assert AlertObservation.objects.filter(alert=alert2).count() == 1

    def test_first_seen_in_alert_is_set(self, user, species, dataset):
        alert = Alert.objects.create(user=user, name="Test Alert")
        create_observation(species, dataset, "1", "occ-1")

        before = timezone.now()
        call_command("sync_alerts")
        after = timezone.now()

        alert_obs = AlertObservation.objects.get(alert=alert)
        assert before <= alert_obs.first_seen_in_alert <= after

    def test_preserves_first_seen_on_resync(self, user, species, dataset):
        alert = Alert.objects.create(user=user, name="Test Alert")
        create_observation(species, dataset, "1", "occ-1")

        call_command("sync_alerts")
        original_first_seen = AlertObservation.objects.get(alert=alert).first_seen_in_alert

        # Resync
        call_command("sync_alerts")

        # first_seen should be unchanged
        assert (
            AlertObservation.objects.get(alert=alert).first_seen_in_alert
            == original_first_seen
        )


@pytest.mark.django_db
class TestSyncAlertsEmailNotification:
    def test_send_emails_flag_updates_last_email_sent(self, user, species, dataset):
        alert = Alert.objects.create(
            user=user, name="Test Alert", email_frequency=Alert.EmailFrequency.DAILY
        )
        create_observation(species, dataset, "1", "occ-1")

        assert alert.last_email_sent_at is None

        call_command("sync_alerts", "--send-emails")

        alert.refresh_from_db()
        assert alert.last_email_sent_at is not None

    def test_no_email_if_frequency_is_never(self, user, species, dataset):
        alert = Alert.objects.create(
            user=user, name="Test Alert", email_frequency=Alert.EmailFrequency.NEVER
        )
        create_observation(species, dataset, "1", "occ-1")

        call_command("sync_alerts", "--send-emails")

        alert.refresh_from_db()
        assert alert.last_email_sent_at is None

    def test_no_email_if_no_new_observations(self, user, species, dataset):
        alert = Alert.objects.create(
            user=user, name="Test Alert", email_frequency=Alert.EmailFrequency.DAILY
        )

        call_command("sync_alerts", "--send-emails")

        alert.refresh_from_db()
        assert alert.last_email_sent_at is None

    def test_respects_email_frequency(self, user, species, dataset):
        alert = Alert.objects.create(
            user=user,
            name="Test Alert",
            email_frequency=Alert.EmailFrequency.WEEKLY,
            last_email_sent_at=timezone.now() - timedelta(days=3),  # Less than a week
        )
        create_observation(species, dataset, "1", "occ-1")

        original_last_email = alert.last_email_sent_at

        call_command("sync_alerts", "--send-emails")

        alert.refresh_from_db()
        # Should not have sent email (less than a week since last)
        assert alert.last_email_sent_at == original_last_email
