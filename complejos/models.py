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
    nombre = models.CharField(max_length=100, default="Cancha de Tenis")
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
    activo = models.BooleanField(default=True, verbose_name="Cancha Activa")

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
