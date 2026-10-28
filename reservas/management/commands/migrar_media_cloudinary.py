import os

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand

from reservas.models import Reserva, UserProfile


class Command(BaseCommand):
    help = (
        'Sube a Cloudinary los archivos que quedaron en el disco local '
        '(media/) antes de activar el almacenamiento externo, y actualiza '
        'los registros en la base de datos para que apunten al archivo '
        'subido. No borra nada del disco local.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Solo muestra que haria, sin subir ni modificar nada.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        if 'cloudinary' not in settings.STORAGES.get('default', {}).get('BACKEND', ''):
            self.stderr.write(self.style.ERROR(
                'El almacenamiento por defecto no es Cloudinary ahora mismo '
                '(revisa las variables CLOUDINARY_* en tu .env). Abortando.'
            ))
            return

        total_subidos = 0
        total_saltados = 0
        total_fallidos = 0

        campos = [
            (UserProfile.objects.exclude(foto='').exclude(foto__isnull=True), 'foto'),
            (Reserva.objects.exclude(comprobante_transferencia='').exclude(comprobante_transferencia__isnull=True), 'comprobante_transferencia'),
        ]

        for queryset, campo in campos:
            for instancia in queryset:
                filefield = getattr(instancia, campo)
                nombre_actual = filefield.name
                ruta_local = os.path.join(settings.MEDIA_ROOT, nombre_actual)

                if not os.path.exists(ruta_local):
                    self.stdout.write(f'  (ya no esta en disco local, se omite) {nombre_actual}')
                    total_saltados += 1
                    continue

                self.stdout.write(f'Subiendo {instancia.__class__.__name__}#{instancia.pk} -> {nombre_actual}')

                if dry_run:
                    continue

                try:
                    with open(ruta_local, 'rb') as f:
                        nuevo_nombre = default_storage.save(nombre_actual, ContentFile(f.read()))
                    setattr(instancia, campo, nuevo_nombre)
                    instancia.save(update_fields=[campo])
                    total_subidos += 1
                except Exception as exc:
                    self.stderr.write(self.style.ERROR(f'  Fallo: {exc}'))
                    total_fallidos += 1

        self.stdout.write(self.style.SUCCESS(
            f'Listo. Subidos: {total_subidos}  Omitidos: {total_saltados}  Fallidos: {total_fallidos}'
        ))
        if dry_run:
            self.stdout.write('(era un --dry-run, no se subio ni modifico nada)')
