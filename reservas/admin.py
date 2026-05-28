from django.contrib import admin
from .models import UserProfile, Reserva


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'rut', 'telefono')
    search_fields = ('user__email', 'rut')


@admin.register(Reserva)
class ReservaAdmin(admin.ModelAdmin):
    list_display = ('salon', 'solicitante', 'fecha', 'hora_inicio', 'hora_fin', 'estado')
    list_filter = ('estado', 'salon', 'fecha')
    search_fields = ('solicitante__email', 'solicitante__first_name', 'motivo')
    readonly_fields = ('creado_en', 'actualizado_en')
