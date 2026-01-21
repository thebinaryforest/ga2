from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Alert, AlertObservation, CustomUser, Dataset, Observation, Species

admin.site.register(CustomUser, UserAdmin)
admin.site.register(Dataset)


@admin.register(Observation)
class ObservationAdmin(admin.ModelAdmin):
    list_display = ["pk", "gbif_id", "occurrence_id", "source_dataset", "stable_id"]
    readonly_fields = ["stable_id"]


@admin.register(Species)
class SpeciesAdmin(admin.ModelAdmin):
    pass


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ["name", "user", "email_frequency", "unseen_count", "created_at"]
    list_filter = ["user", "email_frequency"]
    filter_horizontal = ["species", "datasets"]


@admin.register(AlertObservation)
class AlertObservationAdmin(admin.ModelAdmin):
    list_display = ["alert", "stable_id", "observation_date", "first_seen_in_alert"]
    list_filter = ["alert"]