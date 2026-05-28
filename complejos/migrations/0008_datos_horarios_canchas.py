from django.db import migrations
from datetime import time


HORARIOS = [
    {
        'nombre': 'Cancha de Tenis',
        'hora_inicio': time(8, 30),
        'hora_fin': time(18, 30),
        'pausa_inicio': time(13, 0),
        'pausa_fin': time(14, 0),
        'dias_semana': '0,1,2,3,4',   # Lun–Vie
        'intervalo_minutos': 90,
    },
    {
        'nombre': 'Gimnasio Municipal',
        'hora_inicio': time(8, 30),
        'hora_fin': time(22, 0),
        'pausa_inicio': time(13, 0),
        'pausa_fin': time(14, 0),
        'dias_semana': '0,1,2,3,4,5,6',  # Lun–Dom
        'intervalo_minutos': 60,
    },
    {
        'nombre': 'Cancha de Futbolito',
        'hora_inicio': time(8, 30),
        'hora_fin': time(22, 0),
        'pausa_inicio': time(13, 0),
        'pausa_fin': time(14, 0),
        'dias_semana': '0,1,2,3,4,5,6',  # Lun–Dom
        'intervalo_minutos': 60,
    },
    {
        'nombre': 'Estadio Municipal',
        'hora_inicio': time(8, 30),
        'hora_fin': time(22, 0),
        'pausa_inicio': time(13, 0),
        'pausa_fin': time(14, 0),
        'dias_semana': '0,1,2,3,4,5,6',  # Lun–Dom
        'intervalo_minutos': 120,
    },
]


def aplicar_horarios(apps, schema_editor):
    ConfigCancha = apps.get_model('complejos', 'ConfigCancha')
    for datos in HORARIOS:
        ConfigCancha.objects.filter(nombre=datos['nombre']).update(
            hora_inicio=datos['hora_inicio'],
            hora_fin=datos['hora_fin'],
            pausa_inicio=datos['pausa_inicio'],
            pausa_fin=datos['pausa_fin'],
            dias_semana=datos['dias_semana'],
            intervalo_minutos=datos['intervalo_minutos'],
        )


class Migration(migrations.Migration):

    dependencies = [
        ('complejos', '0007_configcancha_pausa_dias'),
    ]

    operations = [
        migrations.RunPython(aplicar_horarios, migrations.RunPython.noop),
    ]
