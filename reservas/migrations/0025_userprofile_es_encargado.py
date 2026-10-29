from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reservas', '0024_transaccionwebpay'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='es_encargado',
            field=models.BooleanField(default=False, verbose_name='Es encargado'),
        ),
    ]
