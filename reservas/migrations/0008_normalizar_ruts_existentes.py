from django.db import migrations


def normalizar_rut(rut):
    if not rut:
        return ''

    sin_puntos = str(rut).replace('.', '').strip().upper()
    limpio = ''.join(ch for ch in sin_puntos if ch.isdigit() or ch in {'K', '-'})

    if limpio.count('-') != 1:
        return ''

    cuerpo, dv = limpio.split('-', 1)
    if not cuerpo.isdigit() or len(dv) != 1:
        return ''

    return f'{cuerpo}-{dv.upper()}'


def forwards(apps, schema_editor):
    UserProfile = apps.get_model('reservas', 'UserProfile')

    usados = {}
    for perfil in UserProfile.objects.exclude(rut__isnull=True).exclude(rut='').order_by('id'):
        rut_normalizado = normalizar_rut(perfil.rut)
        if not rut_normalizado:
            continue

        if rut_normalizado in usados:
            # Si existiera un duplicado histórico tras normalizar, no forzamos el cambio
            # para no romper la migración por la restricción unique.
            continue

        perfil.rut = rut_normalizado
        perfil.save(update_fields=['rut'])
        usados[rut_normalizado] = perfil.id


class Migration(migrations.Migration):

    dependencies = [
        ('reservas', '0007_reserva_respaldo_solicitante_y_bloqueo_usuario'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
