from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reservas', '0008_normalizar_ruts_existentes'),
    ]

    operations = [
        migrations.AddField(
            model_name='reserva',
            name='comprobante_transferencia',
            field=models.FileField(blank=True, null=True, upload_to='comprobantes_transferencia/', verbose_name='Comprobante de transferencia'),
        ),
    ]
