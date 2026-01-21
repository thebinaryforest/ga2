import pytest

from alerts.models import Alert, CustomUser, Dataset, Species


@pytest.fixture
def user(db):
    return CustomUser.objects.create_user(username="testuser", password="testpass")


@pytest.fixture
def other_user(db):
    return CustomUser.objects.create_user(username="otheruser", password="testpass")


@pytest.fixture
def species(db):
    return Species.objects.create(
        scientific_name="Vespa velutina",
        vernacular_name="Asian hornet",
        gbif_taxon_key=1311477,
    )


@pytest.fixture
def dataset(db):
    return Dataset.objects.create(name="Test Dataset", gbif_dataset_key="ds-key-1")


@pytest.mark.django_db
class TestCustomUserGetAlerts:
    def test_returns_empty_queryset_when_no_alerts(self, user):
        alerts = user.get_alerts()

        assert alerts.count() == 0

    def test_returns_user_alerts(self, user):
        alert1 = Alert.objects.create(user=user, name="Alert 1")
        alert2 = Alert.objects.create(user=user, name="Alert 2")

        alerts = user.get_alerts()

        assert alerts.count() == 2
        assert set(alerts) == {alert1, alert2}

    def test_does_not_return_other_users_alerts(self, user, other_user):
        user_alert = Alert.objects.create(user=user, name="My Alert")
        other_alert = Alert.objects.create(user=other_user, name="Other Alert")

        alerts = user.get_alerts()

        assert alerts.count() == 1
        assert user_alert in alerts
        assert other_alert not in alerts

    def test_prefetches_species(self, user, species, django_assert_num_queries):
        alert = Alert.objects.create(user=user, name="Test Alert")
        alert.species.add(species)

        alerts = list(user.get_alerts())

        # Accessing prefetched species should not cause additional queries
        with django_assert_num_queries(0):
            assert list(alerts[0].species.all()) == [species]

    def test_prefetches_datasets(self, user, dataset, django_assert_num_queries):
        alert = Alert.objects.create(user=user, name="Test Alert")
        alert.datasets.add(dataset)

        alerts = list(user.get_alerts())

        # Accessing prefetched datasets should not cause additional queries
        with django_assert_num_queries(0):
            assert list(alerts[0].datasets.all()) == [dataset]

    def test_returns_queryset(self, user):
        """Verify it returns a queryset that can be further filtered."""
        Alert.objects.create(user=user, name="Alert 1", email_frequency="daily")
        Alert.objects.create(user=user, name="Alert 2", email_frequency="never")

        alerts = user.get_alerts().filter(email_frequency="daily")

        assert alerts.count() == 1
        assert alerts[0].name == "Alert 1"
