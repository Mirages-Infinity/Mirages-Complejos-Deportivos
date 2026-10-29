from django.db import migrations, models
from datetime import time


PISCINAS = [
    'Piscina Adultos',
    'Piscina Niños',
]


def crear_piscinas(apps, schema_editor):
    ConfigCancha = apps.get_model('complejos', 'ConfigCancha')
    for nombre in PISCINAS:
        ConfigCancha.objects.update_or_create(
            nombre=nombre,
            defaults={
                'modalidad': 'DIA',
                'hora_inicio': time(10, 0),
                'hora_fin': time(19, 0),
                'dias_semana': '0,1,2,3,4,5,6',
                'intervalo_minutos': 60,
            },
        )


def borrar_piscinas(apps, schema_editor):
    ConfigCancha = apps.get_model('complejos', 'ConfigCancha')
    ConfigCancha.objects.filter(nombre__in=PISCINAS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('complejos', '0010_configuracionwebpay'),
    ]

    operations = [
        migrations.AddField(
            model_name='configcancha',
            name='modalidad',
            field=models.CharField(
                choices=[('HORA', 'Por hora'), ('DIA', 'Por día')],
                default='HORA',
                max_length=4,
                verbose_name='Modalidad de arriendo',
            ),
        ),
        migrations.AlterField(
            model_name='configcancha',
            name='precio',
            field=models.PositiveIntegerField(default=0, verbose_name='Precio por hora o por día (CLP)'),
        ),
        migrations.RunPython(crear_piscinas, borrar_piscinas),
    ]
