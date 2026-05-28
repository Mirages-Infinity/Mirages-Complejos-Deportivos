import threading
import time
import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class ReservasConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'reservas'

    def ready(self):
        # Evitar arrancar el hilo dos veces en el dev server (RUN_MAIN lo marca)
        import os
        if os.environ.get('RUN_MAIN') == 'true' or not _es_servidor_desarrollo():
            _iniciar_hilo_cancelacion()


def _es_servidor_desarrollo():
    """Devuelve True si estamos corriendo bajo 'manage.py runserver'."""
    import sys
    return 'runserver' in sys.argv


def _iniciar_hilo_cancelacion():
    hilo = threading.Thread(target=_bucle_cancelacion, daemon=True, name='cancelacion-bloqueos')
    hilo.start()
    logger.info('Hilo de cancelación automática de reservas iniciado.')


def _bucle_cancelacion():
    """Espera a que Django esté listo y luego corre cada 60 s."""
    # Pequeña espera inicial para que ORM y conexión a BD estén disponibles
    time.sleep(10)

    while True:
        try:
            from reservas.management.commands.cancelar_reservas_bloqueadas import ejecutar_cancelacion
            canceladas = ejecutar_cancelacion()
            if canceladas:
                logger.info(f'Cancelación automática: {canceladas} reserva(s) cancelada(s).')
        except Exception:
            logger.exception('Error en el hilo de cancelación automática.')
        time.sleep(60)
