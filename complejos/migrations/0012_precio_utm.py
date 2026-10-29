from django.db import migrations, models

UTM_PISCINAS = {'Piscina Adultos': 2, 'Piscina Niños': 1}


def fijar_utm(apps, schema_editor):
    ConfigCancha = apps.get_model('complejos', 'ConfigCancha')
    for nombre, utm in UTM_PISCINAS.items():
        ConfigCancha.objects.filter(nombre=nombre).update(precio_utm=utm)


class Migration(migrations.Migration):

    dependencies = [
        ('complejos', '0011_modalidad_piscinas'),
    ]

    operations = [
        migrations.AddField(
            model_name='configcancha',
            name='precio_utm',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=6, null=True,
                help_text='Si se define, el precio en pesos se calcula con el valor de la UTM vigente.',
                verbose_name='Precio en UTM',
            ),
        ),
        migrations.CreateModel(
            name='ValorUTM',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('valor', models.DecimalField(decimal_places=2, max_digits=10)),
                ('anio', models.PositiveIntegerField()),
                ('mes', models.PositiveIntegerField()),
                ('actualizado_en', models.DateTimeField(auto_now=True)),
            ],
            options={'verbose_name': 'Valor UTM', 'verbose_name_plural': 'Valor UTM'},
        ),
        migrations.RunPython(fijar_utm, migrations.RunPython.noop),
    ]
