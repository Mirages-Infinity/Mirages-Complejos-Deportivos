from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('complejos', '0006_bloqueoperiodo'),
    ]

    operations = [
        migrations.AddField(
            model_name='configcancha',
            name='pausa_inicio',
            field=models.TimeField(blank=True, null=True, verbose_name='Inicio pausa'),
        ),
        migrations.AddField(
            model_name='configcancha',
            name='pausa_fin',
            field=models.TimeField(blank=True, null=True, verbose_name='Fin pausa'),
        ),
        migrations.AddField(
            model_name='configcancha',
            name='dias_semana',
            field=models.CharField(
                default='0,1,2,3,4,5,6',
                max_length=20,
                verbose_name='Días disponibles',
            ),
        ),
    ]
