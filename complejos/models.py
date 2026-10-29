from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import models
from django.contrib.auth.models import User


class Salon(models.Model):
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True)
    capacidad = models.PositiveIntegerField(default=0)
    activo = models.BooleanField(default=True)
    admins = models.ManyToManyField(
        User,
        related_name='salones_administrados',
        blank=True,
        verbose_name='Administradores',
    )

    class Meta:
        verbose_name = 'Salón'
        verbose_name_plural = 'Salones'

    def __str__(self):
        return self.nombre


class ConfigCancha(models.Model):
    MODALIDAD_HORA = 'HORA'
    MODALIDAD_DIA = 'DIA'
    MODALIDAD_CHOICES = [
        (MODALIDAD_HORA, 'Por hora'),
        (MODALIDAD_DIA, 'Por día'),
    ]

    nombre = models.CharField(max_length=100, default="Cancha de Tenis")
    modalidad = models.CharField(
        max_length=4,
        choices=MODALIDAD_CHOICES,
        default=MODALIDAD_HORA,
        verbose_name="Modalidad de arriendo",
    )
    hora_inicio = models.TimeField(default="09:00")
    hora_fin = models.TimeField(default="23:00")
    pausa_inicio = models.TimeField(null=True, blank=True, verbose_name="Inicio pausa")
    pausa_fin = models.TimeField(null=True, blank=True, verbose_name="Fin pausa")
    # Días disponibles: 0=Lun, 1=Mar, 2=Mié, 3=Jue, 4=Vie, 5=Sáb, 6=Dom
    dias_semana = models.CharField(
        max_length=20,
        default="0,1,2,3,4,5,6",
        verbose_name="Días disponibles",
    )
    intervalo_minutos = models.PositiveIntegerField(default=60)
    precio = models.PositiveIntegerField(default=0, verbose_name="Precio por hora o por día (CLP)")
    precio_utm = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Precio en UTM",
        help_text="Si se define, el precio en pesos se calcula con el valor de la UTM vigente.",
    )
    activo = models.BooleanField(default=True, verbose_name="Cancha Activa")

    @property
    def precio_vigente(self):
        """Precio en CLP: precio_utm × UTM del mes si está definido; si no, el precio fijo."""
        if self.precio_utm:
            registro = ValorUTM.objects.first()
            if registro:
                return int((self.precio_utm * registro.valor).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        return self.precio

    def dias_semana_list(self):
        try:
            return [int(d) for d in self.dias_semana.split(',') if d.strip().isdigit()]
        except Exception:
            return list(range(7))

    class Meta:
        verbose_name = 'Configuración de Cancha'
        verbose_name_plural = 'Configuraciones de Canchas'

    def __str__(self):
        return f"Configuración: {self.nombre}"


class ValorUTM(models.Model):
    """Valor de la UTM en pesos, ingresado a mano por el administrador (anio/mes = mes al que corresponde)."""
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    anio = models.PositiveIntegerField()
    mes = models.PositiveIntegerField()
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Valor UTM'
        verbose_name_plural = 'Valor UTM'

    def __str__(self):
        return f"UTM {self.mes:02d}/{self.anio}: ${self.valor}"


class BloqueoHorario(models.Model):
    cancha = models.ForeignKey(ConfigCancha, on_delete=models.CASCADE, related_name='bloqueos')
    fecha = models.DateField()
    hora = models.TimeField()

    class Meta:
        verbose_name = 'Bloqueo de Horario'
        verbose_name_plural = 'Bloqueos de Horarios'
        unique_together = ('cancha', 'fecha', 'hora')

    def __str__(self):
        return f"Bloqueo: {self.cancha.nombre} - {self.fecha} {self.hora}"


class BloqueoPeriodo(models.Model):
    cancha = models.ForeignKey(ConfigCancha, on_delete=models.CASCADE, related_name='bloqueos_periodo')
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField()
    creado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Bloqueo de Período'
        verbose_name_plural = 'Bloqueos de Período'

    def __str__(self):
        return f"{self.cancha.nombre}: {self.fecha_inicio} → {self.fecha_fin}"


class DatosTransferencia(models.Model):
    banco = models.CharField(max_length=100, default="Banco de Prueba")
    titular = models.CharField(max_length=150, default="Complejos Deportivos Demo SpA")
    tipo_cuenta = models.CharField(max_length=50, default="Cuenta Corriente")
    numero_cuenta = models.CharField(max_length=50, default="1234567890")
    rut = models.CharField(max_length=20, default="12.345.678-9")
    email = models.EmailField(default="pagos-demo@complejosdeportivos.cl")

    class Meta:
        verbose_name = 'Datos de Transferencia'
        verbose_name_plural = 'Datos de Transferencia'

    def __str__(self):
        return f"Datos de transferencia ({self.banco})"


class ConfiguracionWebpay(models.Model):
    ENTORNO_CHOICES = [
        ('INTEGRACION', 'Integración (Pruebas)'),
        ('PRODUCCION', 'Producción'),
    ]
    entorno = models.CharField(max_length=20, choices=ENTORNO_CHOICES, default='INTEGRACION')
    commerce_code = models.CharField(max_length=100, default="597055555532", help_text="Código de comercio entregado por Transbank. Por defecto es el de pruebas.")
    api_key = models.CharField(max_length=255, default="579B532A7440BB0C9079DED94D31EA1615BACEB56610332264630D42D0A36B1C", help_text="Llave secreta entregada por Transbank. Por defecto es la de pruebas.")
    activo = models.BooleanField(default=True, help_text="Activar/Desactivar el pago con Webpay")

    # Llave de pruebas pública de Transbank: nunca debe usarse en producción
    API_KEY_PRUEBAS = "579B532A7440BB0C9079DED94D31EA1615BACEB56610332264630D42D0A36B1C"

    @property
    def credenciales_en_entorno(self):
        return bool(settings.WEBPAY_API_KEY)

    @property
    def commerce_code_efectivo(self):
        return settings.WEBPAY_COMMERCE_CODE or self.commerce_code

    @property
    def api_key_efectiva(self):
        return settings.WEBPAY_API_KEY or self.api_key

    class Meta:
        verbose_name = 'Configuración Webpay'
        verbose_name_plural = 'Configuraciones Webpay'

    def __str__(self):
        return f"Webpay ({self.get_entorno_display()})"
