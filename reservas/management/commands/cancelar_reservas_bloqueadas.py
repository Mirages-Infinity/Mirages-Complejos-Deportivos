"""
Management command: cancelar_reservas_bloqueadas

Cancela automáticamente las reservas que:
  - Están en estado PENDIENTE o APROBADA
  - Tienen su hora bloqueada por un BloqueoHorario
  - Su fecha+hora_inicio ya pasó (la reserva venció sin que el usuario la cambiara)

Envía notificación al usuario con el aviso del cupón.
Registra cada cancelación en la bitácora.

Uso manual:
    python manage.py cancelar_reservas_bloqueadas

Uso automático: se llama desde el hilo de fondo en reservas/apps.py cada 60 s.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import datetime

from reservas.models import Reserva, Notificacion, Bitacora
from reservas.views import emitir_cupon
from complejos.models import BloqueoHorario


def ejecutar_cancelacion():
    """
    Lógica central de cancelación. Retorna el número de reservas canceladas.
    Puede llamarse tanto desde el Command como desde el hilo de fondo.
    """
    ahora = timezone.localtime(timezone.now())
    canceladas = 0

    # Reservas activas cuya fecha+hora ya pasó y tienen un BloqueoHorario
    reservas = Reserva.objects.filter(
        estado__in=['PENDIENTE', 'APROBADA'],
        cancha__isnull=False,
    ).select_related('solicitante', 'cancha')

    for reserva in reservas:
        # Verificar que la hora ya pasó
        dt_reserva = timezone.make_aware(
            datetime.combine(reserva.fecha, reserva.hora_inicio)
        )
        if dt_reserva > ahora:
            continue

        # Verificar que el horario está bloqueado
        esta_bloqueada = BloqueoHorario.objects.filter(
            cancha=reserva.cancha,
            fecha=reserva.fecha,
            hora=reserva.hora_inicio,
        ).exists()

        if not esta_bloqueada:
            continue

        fue_aprobada = reserva.estado == 'APROBADA'

        # Cancelar la reserva
        reserva.estado = 'CANCELADA'
        reserva.motivo_rechazo = (
            'Cancelada automáticamente por la administración: '
            'el horario fue bloqueado y no fue cambiado a tiempo.'
        )
        reserva.save(update_fields=['estado', 'motivo_rechazo'])

        # Notificar al usuario y emitir cupón si la reserva era APROBADA (ya pagada)
        if reserva.solicitante:
            fecha_fmt = reserva.fecha.strftime('%d/%m/%Y')
            hora_fmt = reserva.hora_inicio.strftime('%H:%M')
            Notificacion.objects.create(
                user=reserva.solicitante,
                tipo='RESERVA_CANCELADA',
                titulo='Tu reserva fue cancelada',
                mensaje=(
                    f'Tu reserva en {reserva.cancha.nombre} el {fecha_fmt} a las {hora_fmt} '
                    f'fue cancelada automáticamente porque el horario estaba restringido '
                    f'por la administración y no fue cambiada a tiempo.'
                ),
            )
            if fue_aprobada:
                emitir_cupon(reserva.solicitante, reserva, 'CANCELACION_SISTEMA')

        # Registrar en bitácora (sin admin, es acción automática)
        Bitacora.objects.create(
            admin=None,
            accion='Cancelación automática',
            detalles=(
                f'{reserva.cancha.nombre} — '
                f'{reserva.fecha.strftime("%d/%m/%Y")} {reserva.hora_inicio.strftime("%H:%M")} — '
                f'Usuario: {reserva.get_solicitante_display()}'
            ),
        )

        canceladas += 1

    return canceladas


class Command(BaseCommand):
    help = 'Cancela reservas en horarios bloqueados cuya hora ya pasó'

    def handle(self, *args, **options):
        canceladas = ejecutar_cancelacion()
        if canceladas:
            self.stdout.write(
                self.style.SUCCESS(f'{canceladas} reserva(s) cancelada(s) automáticamente.')
            )
        else:
            self.stdout.write('Sin reservas que cancelar.')
