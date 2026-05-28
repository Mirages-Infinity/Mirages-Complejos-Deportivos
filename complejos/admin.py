from django.contrib import admin
from .models import Salon, ConfigCancha


@admin.register(Salon)
class SalonAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'capacidad', 'activo')
    list_filter = ('activo',)
    filter_horizontal = ('admins',)


@admin.register(ConfigCancha)
class ConfigCanchaAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'hora_inicio', 'hora_fin', 'intervalo_minutos')
