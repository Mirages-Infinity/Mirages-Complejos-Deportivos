from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def poblar_respaldo_solicitante(apps, schema_editor):
    Reserva = apps.get_model('reservas', 'Reserva')

    for reserva in Reserva.objects.select_related('solicitante').all():
        solicitante = reserva.solicitante
        if not solicitante:
            continue

        nombre = (f'{solicitante.first_name} {solicitante.last_name}').strip() or solicitante.username
        correo = solicitante.email or solicitante.username

        Reserva.objects.filter(pk=reserva.pk).update(
            solicitante_nombre=nombre,
            solicitante_email=correo,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('reservas', '0006_alter_reserva_options_remove_reserva_requiere_audio_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='reserva',
            name='solicitante_email',
            field=models.EmailField(blank=True, default='', max_length=254),
        ),
        migrations.AddField(
            model_name='reserva',
            name='solicitante_nombre',
            field=models.CharField(blank=True, default='', max_length=150),
        ),
        migrations.AlterField(
            model_name='reserva',
            name='solicitante',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reservas', to=settings.AUTH_USER_MODEL, verbose_name='Solicitante'),
        ),
        migrations.RunPython(poblar_respaldo_solicitante, migrations.RunPython.noop),
    ]
