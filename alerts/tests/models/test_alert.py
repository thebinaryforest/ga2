from datetime import date, timedelta

import pytest
from django.utils import timezone

from alerts.models import Alert, AlertObservation, Dataset, Observation, Species


@pytest.fixture
def user(db):
    from alerts.models import CustomUser

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


@pytest.fixture
def alert(user):
    return Alert.objects.create(user=user, name="Test Alert")


@pytest.mark.django_db
class TestAlertModel:
    def test_create_alert(self, user):
        alert = Alert.objects.create(user=user, name="My Alert")

        assert alert.name == "My Alert"
        assert alert.user == user
        assert alert.email_frequency == Alert.EmailFrequency.DAILY
        assert alert.auto_mark_seen_after_days == 365
        assert alert.unseen_count == 0

    def test_alert_str(self, alert):
        assert "Test Alert" in str(alert)
        assert "testuser" in str(alert)

    def test_default_email_frequency_is_daily(self, user):
        alert = Alert.objects.create(user=user, name="Test")
        assert alert.email_frequency == Alert.EmailFrequency.DAILY

    def test_default_auto_mark_seen_is_365_days(self, user):
        alert = Alert.objects.create(user=user, name="Test")
        assert alert.auto_mark_seen_after_days == 365


@pytest.mark.django_db
class TestAlertFiltering:
    def test_get_matching_observations_no_filters_returns_all(self, alert, species, dataset):
        obs1 = Observation.objects.create(
            gbif_id="1",
            occurrence_id="occ-1",
            source_dataset=dataset,
            species=species,
            date=date(2024, 1, 15),
        )
        obs2 = Observation.objects.create(
            gbif_id="2",
            occurrence_id="occ-2",
            source_dataset=dataset,
            species=species,
            date=date(2024, 1, 16),
        )

        matching = alert.get_matching_observations()

        assert matching.count() == 2
        assert set(matching.values_list("gbif_id", flat=True)) == {"1", "2"}

    def test_get_matching_observations_filters_by_species(
        self, alert, species, species2, dataset
    ):
        obs1 = Observation.objects.create(
            gbif_id="1",
            occurrence_id="occ-1",
            source_dataset=dataset,
            species=species,
            date=date(2024, 1, 15),
        )
        obs2 = Observation.objects.create(
            gbif_id="2",
            occurrence_id="occ-2",
            source_dataset=dataset,
            species=species2,
            date=date(2024, 1, 16),
        )

        alert.species.add(species)

        matching = alert.get_matching_observations()

        assert matching.count() == 1
        assert matching.first().gbif_id == "1"

    def test_get_matching_observations_filters_by_dataset(
        self, alert, species, dataset, dataset2
    ):
        obs1 = Observation.objects.create(
            gbif_id="1",
            occurrence_id="occ-1",
            source_dataset=dataset,
            species=species,
            date=date(2024, 1, 15),
        )
        obs2 = Observation.objects.create(
            gbif_id="2",
            occurrence_id="occ-2",
            source_dataset=dataset2,
            species=species,
            date=date(2024, 1, 16),
        )

        alert.datasets.add(dataset)

        matching = alert.get_matching_observations()

        assert matching.count() == 1
        assert matching.first().gbif_id == "1"

    def test_get_matching_observations_and_logic(
        self, alert, species, species2, dataset, dataset2
    ):
        """Filters use AND logic: must match both species AND dataset filters."""
        # Matches both filters
        obs1 = Observation.objects.create(
            gbif_id="1",
            occurrence_id="occ-1",
            source_dataset=dataset,
            species=species,
            date=date(2024, 1, 15),
        )
        # Wrong species
        obs2 = Observation.objects.create(
            gbif_id="2",
            occurrence_id="occ-2",
            source_dataset=dataset,
            species=species2,
            date=date(2024, 1, 16),
        )
        # Wrong dataset
        obs3 = Observation.objects.create(
            gbif_id="3",
            occurrence_id="occ-3",
            source_dataset=dataset2,
            species=species,
            date=date(2024, 1, 17),
        )

        alert.species.add(species)
        alert.datasets.add(dataset)

        matching = alert.get_matching_observations()

        assert matching.count() == 1
        assert matching.first().gbif_id == "1"

    def test_get_matching_observations_multiple_species(
        self, alert, species, species2, dataset
    ):
        obs1 = Observation.objects.create(
            gbif_id="1",
            occurrence_id="occ-1",
            source_dataset=dataset,
            species=species,
            date=date(2024, 1, 15),
        )
        obs2 = Observation.objects.create(
            gbif_id="2",
            occurrence_id="occ-2",
            source_dataset=dataset,
            species=species2,
            date=date(2024, 1, 16),
        )

        alert.species.add(species, species2)

        matching = alert.get_matching_observations()

        assert matching.count() == 2


@pytest.mark.django_db
class TestAlertEmailLogic:
    def test_should_send_email_never_frequency(self, alert):
        alert.email_frequency = Alert.EmailFrequency.NEVER
        alert.save()

        assert alert.should_send_email() is False

    def test_should_send_email_first_time(self, alert):
        alert.last_email_sent_at = None

        assert alert.should_send_email() is True

    def test_should_send_email_daily_after_one_day(self, alert):
        alert.email_frequency = Alert.EmailFrequency.DAILY
        alert.last_email_sent_at = timezone.now() - timedelta(days=1, hours=1)
        alert.save()

        assert alert.should_send_email() is True

    def test_should_not_send_email_daily_before_one_day(self, alert):
        alert.email_frequency = Alert.EmailFrequency.DAILY
        alert.last_email_sent_at = timezone.now() - timedelta(hours=12)
        alert.save()

        assert alert.should_send_email() is False

    def test_should_send_email_weekly_after_seven_days(self, alert):
        alert.email_frequency = Alert.EmailFrequency.WEEKLY
        alert.last_email_sent_at = timezone.now() - timedelta(days=7, hours=1)
        alert.save()

        assert alert.should_send_email() is True

    def test_should_send_email_monthly_after_thirty_days(self, alert):
        alert.email_frequency = Alert.EmailFrequency.MONTHLY
        alert.last_email_sent_at = timezone.now() - timedelta(days=30, hours=1)
        alert.save()

        assert alert.should_send_email() is True

    def test_get_new_observations_since_last_email(self, alert, species, dataset):
        obs = Observation.objects.create(
            gbif_id="1",
            occurrence_id="occ-1",
            source_dataset=dataset,
            species=species,
            date=date(2024, 1, 15),
        )
        obs.refresh_from_db()

        # Simulate last email was sent before
        alert.last_email_sent_at = timezone.now() - timedelta(days=1)
        alert.save()

        # Add an unseen observation
        AlertObservation.objects.create(
            alert=alert,
            stable_id=obs.stable_id,
            observation_date=obs.date,
        )

        new_obs = alert.get_new_observations_since_last_email()

        assert new_obs.count() == 1


@pytest.mark.django_db
class TestAlertObservationModel:
    def test_create_alert_observation(self, alert, species, dataset):
        obs = Observation.objects.create(
            gbif_id="1",
            occurrence_id="occ-1",
            source_dataset=dataset,
            species=species,
            date=date(2024, 1, 15),
        )
        obs.refresh_from_db()

        alert_obs = AlertObservation.objects.create(
            alert=alert,
            stable_id=obs.stable_id,
            observation_date=obs.date,
        )

        assert alert_obs.alert == alert
        assert alert_obs.stable_id == obs.stable_id
        assert alert_obs.observation_date == date(2024, 1, 15)
        assert alert_obs.first_seen_in_alert is not None

    def test_unique_constraint_alert_stable_id(self, alert, species, dataset):
        obs = Observation.objects.create(
            gbif_id="1",
            occurrence_id="occ-1",
            source_dataset=dataset,
            species=species,
            date=date(2024, 1, 15),
        )
        obs.refresh_from_db()

        AlertObservation.objects.create(
            alert=alert,
            stable_id=obs.stable_id,
            observation_date=obs.date,
        )

        with pytest.raises(Exception):  # IntegrityError
            AlertObservation.objects.create(
                alert=alert,
                stable_id=obs.stable_id,
                observation_date=obs.date,
            )

    def test_cascade_delete_on_alert_delete(self, alert, species, dataset):
        obs = Observation.objects.create(
            gbif_id="1",
            occurrence_id="occ-1",
            source_dataset=dataset,
            species=species,
            date=date(2024, 1, 15),
        )
        obs.refresh_from_db()

        AlertObservation.objects.create(
            alert=alert,
            stable_id=obs.stable_id,
            observation_date=obs.date,
        )

        assert AlertObservation.objects.count() == 1

        alert.delete()

        assert AlertObservation.objects.count() == 0

    def test_same_stable_id_in_different_alerts(self, user, species, dataset):
        alert1 = Alert.objects.create(user=user, name="Alert 1")
        alert2 = Alert.objects.create(user=user, name="Alert 2")

        obs = Observation.objects.create(
            gbif_id="1",
            occurrence_id="occ-1",
            source_dataset=dataset,
            species=species,
            date=date(2024, 1, 15),
        )
        obs.refresh_from_db()

        # Same observation can be unseen in multiple alerts
        AlertObservation.objects.create(
            alert=alert1,
            stable_id=obs.stable_id,
            observation_date=obs.date,
        )
        AlertObservation.objects.create(
            alert=alert2,
            stable_id=obs.stable_id,
            observation_date=obs.date,
        )

        assert AlertObservation.objects.count() == 2
