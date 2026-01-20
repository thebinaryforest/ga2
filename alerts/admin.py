from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import CustomUser, Dataset, Observation

admin.site.register(CustomUser, UserAdmin)
admin.site.register(Dataset)


@admin.register(Observation)
class ObservationAdmin(admin.ModelAdmin):
    list_display = ["pk", "gbif_id", "occurrence_id", "source_dataset", "stable_id"]
    readonly_fields = ["stable_id"]