from django.core.management.base import BaseCommand
from reservas.models import Reserva, RegistroTesoreria


class Command(BaseCommand):
    help = 'Crea RegistroTesoreria para todas las reservas aprobadas que no tienen uno aún.'

    def handle(self, *args, **kwargs):
        reservas = Reserva.objects.filter(
            estado='APROBADA'
        ).exclude(
            registro_tesoreria__isnull=False
        ).select_related('registro_tesoreria')

        creados = 0
        for reserva in reservas:
            RegistroTesoreria.objects.create(reserva=reserva)
            creados += 1

        self.stdout.write(self.style.SUCCESS(
            f'{creados} registros de tesorería creados.'
        ))
