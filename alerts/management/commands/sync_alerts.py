import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from alerts.models import Alert, AlertObservation, Observation


class Command(BaseCommand):
    help = "Sync alerts with current observations after data import"

    def add_arguments(self, parser):
        parser.add_argument(
            "--send-emails",
            action="store_true",
            help="Send notification emails for alerts with new observations",
        )

    def handle(self, *args, **options):
        start_time = time.perf_counter()
        self.send_emails = options["send_emails"]

        self.stdout.write("Starting alert sync...")

        # Step 1: Clean up AlertObservation for observations that no longer exist
        self._cleanup_stale_observations()

        # Step 2: Sync each alert
        alerts = Alert.objects.all()
        total_new = 0
        total_auto_marked = 0

        for alert in alerts:
            new_count, auto_marked_count = self._sync_alert(alert)
            total_new += new_count
            total_auto_marked += auto_marked_count

        elapsed = time.perf_counter() - start_time
        self.stdout.write(
            self.style.SUCCESS(
                f"Sync complete: {alerts.count()} alerts processed, "
                f"{total_new} new observations added, "
                f"{total_auto_marked} auto-marked as seen "
                f"in {elapsed:.2f}s"
            )
        )

    def _cleanup_stale_observations(self):
        """Remove AlertObservation rows for stable_ids no longer in Observation."""
        self.stdout.write("Cleaning up stale AlertObservation entries...")

        # Get all stable_ids currently in Observation
        current_stable_ids = set(
            Observation.objects.values_list("stable_id", flat=True)
        )

        # Delete AlertObservation entries that reference non-existent observations
        stale_count, _ = AlertObservation.objects.exclude(
            stable_id__in=current_stable_ids
        ).delete()

        if stale_count > 0:
            self.stdout.write(f"  Removed {stale_count} stale entries")

    def _sync_alert(self, alert):
        """
        Sync a single alert:
        1. Find new matching observations and add them as unseen
        2. Auto-mark old observations as seen
        3. Update unseen_count
        4. Optionally queue email notification

        Returns (new_count, auto_marked_count)
        """
        with transaction.atomic():
            # Get stable_ids matching this alert's filters
            matching_obs = alert.get_matching_observations()
            matching_data = {
                row["stable_id"]: row["date"]
                for row in matching_obs.values("stable_id", "date")
            }
            matching_stable_ids = set(matching_data.keys())

            # Get stable_ids already tracked for this alert
            existing_stable_ids = set(
                AlertObservation.objects.filter(alert=alert).values_list(
                    "stable_id", flat=True
                )
            )

            # Find new observations to add
            new_stable_ids = matching_stable_ids - existing_stable_ids

            # Bulk create AlertObservation for new observations
            if new_stable_ids:
                AlertObservation.objects.bulk_create(
                    [
                        AlertObservation(
                            alert=alert,
                            stable_id=stable_id,
                            observation_date=matching_data[stable_id],
                        )
                        for stable_id in new_stable_ids
                    ]
                )

            new_count = len(new_stable_ids)

            # Auto-mark old observations as seen (delete them)
            auto_marked_count = 0
            if alert.auto_mark_seen_after_days:
                cutoff_date = (
                    timezone.now() - timedelta(days=alert.auto_mark_seen_after_days)
                ).date()
                auto_marked_count, _ = AlertObservation.objects.filter(
                    alert=alert, observation_date__lt=cutoff_date
                ).delete()

            # Update unseen count
            alert.unseen_count = AlertObservation.objects.filter(alert=alert).count()
            alert.save(update_fields=["unseen_count"])

            # Handle email notification
            if self.send_emails and new_count > 0 and alert.should_send_email():
                self._send_email_notification(alert, new_count)

        if new_count > 0 or auto_marked_count > 0:
            self.stdout.write(
                f"  Alert '{alert.name}': +{new_count} new, "
                f"-{auto_marked_count} auto-marked, "
                f"{alert.unseen_count} unseen total"
            )

        return new_count, auto_marked_count

    def _send_email_notification(self, alert, new_count):
        """Send email notification for an alert (placeholder for now)."""
        # TODO: Implement actual email sending
        # This would include:
        # - Alert details (name, filters)
        # - Count of new observations
        # - Sample of new observations (join with Observation for details)
        # - Link to alert on website

        self.stdout.write(
            f"  [EMAIL] Would send email to {alert.user.email or alert.user.username}: "
            f"{new_count} new observations in '{alert.name}'"
        )

        alert.last_email_sent_at = timezone.now()
        alert.save(update_fields=["last_email_sent_at"])
