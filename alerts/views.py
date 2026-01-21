from django.shortcuts import render

from .models import Dataset, Observation, Species


def home(request):
    # Total observation count
    total_count = Observation.objects.count()

    # Pick a few species/datasets for filtered counts (first 3 of each)
    sample_species = list(Species.objects.all()[:3])
    sample_datasets = list(Dataset.objects.all()[:3])

    species_counts = [
        {
            'species': sp,
            'count': Observation.objects.filter(species=sp).count(),
        }
        for sp in sample_species
    ]

    dataset_counts = [
        {
            'dataset': ds,
            'count': Observation.objects.filter(source_dataset=ds).count(),
        }
        for ds in sample_datasets
    ]

    # Combined filter: species IN (...) AND dataset IN (...)
    combined_count = None
    if sample_species and sample_datasets:
        combined_count = Observation.objects.filter(
            species__in=sample_species,
            source_dataset__in=sample_datasets,
        ).count()

    # User's alerts
    user_alerts = []
    if request.user.is_authenticated:
        user_alerts = request.user.get_alerts()

    return render(request, 'home.html', {
        'total_count': total_count,
        'species_counts': species_counts,
        'dataset_counts': dataset_counts,
        'combined_count': combined_count,
        'sample_species': sample_species,
        'sample_datasets': sample_datasets,
        'user_alerts': user_alerts,
    })
