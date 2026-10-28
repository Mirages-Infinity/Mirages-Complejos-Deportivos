import secrets
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.validators import RegexValidator
from datetime import timedelta, date

_telefono_validator = RegexValidator(
    regex=r'^\+?[\d\s\-]{7,20}$',
    message='Ingresa un número de teléfono válido (solo dígitos, espacios, guiones o +).',
)


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='perfil')
    telefono = models.CharField(max_length=20, blank=True, validators=[_telefono_validator])
    rut = models.CharField(max_length=12, unique=True, blank=True, null=True)
    fecha_nacimiento = models.DateField(blank=True, null=True)
    foto = models.FileField(upload_to='perfiles/', blank=True, null=True)
    es_alcalde = models.BooleanField(default=False, verbose_name='Es alcalde')
    es_tesorero = models.BooleanField(default=False, verbose_name='Es tesorero')
    es_organizacion = models.BooleanField(default=False, verbose_name='Es organización')
    nombre_organizacion = models.CharField(max_length=200, blank=True, verbose_name='Nombre organización')



    def __str__(self):
        return f"{self.user.get_full_name()} ({self.user.email})"


class CambioEmailPendiente(models.Model):
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='cambio_email_pendiente'
    )
    nuevo_email = models.EmailField()
    token = models.CharField(max_length=64, unique=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    def esta_vigente(self):
        return timezone.now() - self.creado_en < timedelta(hours=24)

    @classmethod
    def crear_para(cls, user, nuevo_email):
        cls.objects.filter(user=user).delete()
        return cls.objects.create(
            user=user,
            nuevo_email=nuevo_email,
            token=secrets.token_urlsafe(48),
        )


class Notificacion(models.Model):
    TIPO_CHOICES = [
        ('RESERVA_PENDIENTE', 'Reserva pendiente'),
        ('RESERVA_APROBADA', 'Reserva aprobada'),
        ('RESERVA_RECHAZADA', 'Reserva rechazada'),
        ('RESERVA_CANCELADA', 'Reserva cancelada'),
        ('PERFIL_ACTUALIZADO', 'Perfil actualizado'),
        ('SEGURIDAD_CAMBIO_CLAVE', 'Cambio de contraseña'),
        ('SEGURIDAD_CAMBIO_EMAIL', 'Cambio de email'),
        ('CUPON_EMITIDO', 'Cupón emitido'),
        ('CUPON_USADO', 'Cupón usado'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notificaciones')
    tipo = models.CharField(max_length=30, choices=TIPO_CHOICES)
    titulo = models.CharField(max_length=120)
    mensaje = models.TextField()
    leida = models.BooleanField(default=False)
    reserva = models.ForeignKey('Reserva', on_delete=models.CASCADE, null=True, blank=True, related_name='notificaciones')
    creada_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creada_en']
        verbose_name = 'Notificación'
        verbose_name_plural = 'Notificaciones'

    def __str__(self):
        return f'{self.user.email} - {self.titulo}'


class Reserva(models.Model):
    ESTADO_RESERVA = [
        ('PENDIENTE', 'Pendiente'),
        ('PENDIENTE_ALCALDE', 'Pendiente firma alcalde'),
        ('APROBADA', 'Aprobada'),
        ('RECHAZADA', 'Rechazada'),
        ('CANCELADA', 'Cancelada'),
        ('ELIMINADA', 'Eliminada'),
    ]
    TIPO_RESERVA_CHOICES = [
        ('PERSONAL', 'Personal'),
        ('ORGANIZACION', 'Organización'),
    ]

    salon = models.ForeignKey(
        'complejos.Salon',
        on_delete=models.CASCADE,
        related_name='reservas',
        verbose_name='Salón',
        null=True,
        blank=True
    )
    cancha = models.ForeignKey(
        'complejos.ConfigCancha',
        on_delete=models.CASCADE,
        related_name='reservas',
        verbose_name='Cancha',
        null=True,
        blank=True
    )
    solicitante = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name='reservas',
        verbose_name='Solicitante',
        null=True,
        blank=True,
    )
    solicitante_nombre = models.CharField(max_length=150, blank=True, default='')
    solicitante_email = models.EmailField(blank=True, default='')
    solicitante_telefono = models.CharField(max_length=20, blank=True, default='')
    solicitante_rut = models.CharField(max_length=12, blank=True, default='')
    fecha = models.DateField()
    hora_inicio = models.TimeField()
    hora_fin = models.TimeField()
    motivo = models.TextField(verbose_name='Motivo del evento', blank=True, null=True)
    estado = models.CharField(max_length=20, choices=ESTADO_RESERVA, default='PENDIENTE')
    metodo_pago = models.CharField(
        max_length=20,
        choices=[('TARJETA', 'Tarjeta'), ('TRANSFERENCIA', 'Transferencia'), ('CUPON', 'Cupón')],
        default='TRANSFERENCIA'
    )
    comprobante_transferencia = models.FileField(
        upload_to='comprobantes_transferencia/',
        blank=True,
        null=True,
        verbose_name='Comprobante de transferencia',
    )

    # Tipo de reserva
    tipo_reserva = models.CharField(
        max_length=15, choices=TIPO_RESERVA_CHOICES, default='PERSONAL', verbose_name='Tipo de reserva'
    )

    # Campos para reservas de organizaciones
    nombre_organizacion = models.CharField(max_length=200, blank=True, verbose_name='Nombre organización')
    representante_nombre = models.CharField(max_length=150, blank=True, verbose_name='Nombre representante')
    representante_rut = models.CharField(max_length=12, blank=True, verbose_name='RUT representante')
    representante_telefono = models.CharField(max_length=20, blank=True, verbose_name='Teléfono representante')
    representante_email = models.EmailField(blank=True, verbose_name='Email representante')
    precio_original = models.DecimalField(max_digits=10, decimal_places=0, null=True, blank=True, verbose_name='Precio original')
    precio_descuento = models.DecimalField(max_digits=10, decimal_places=0, null=True, blank=True, verbose_name='Precio con descuento')

    # Firmas de la carta de solicitud
    firma_org_en = models.DateTimeField(null=True, blank=True, verbose_name='Carta firmada por org el')
    firma_alcalde_carta_en = models.DateTimeField(null=True, blank=True, verbose_name='Carta firmada por alcalde el')

    # Gestión admin
    gestionado_por = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reservas_gestionadas',
        verbose_name='Gestionado por',
    )
    motivo_rechazo = models.TextField(blank=True, verbose_name='Motivo de rechazo/cancelación')

    # Campos de auditoría para cambios solicitados
    fecha_anterior = models.DateField(null=True, blank=True, verbose_name="Fecha previa al cambio")
    hora_inicio_anterior = models.TimeField(null=True, blank=True, verbose_name="Inicio previo")
    hora_fin_anterior = models.TimeField(null=True, blank=True, verbose_name="Fin previo")

    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Reserva'
        verbose_name_plural = 'Reservas'
        ordering = ['fecha', 'hora_inicio']

    def __str__(self):
        recurso = self.salon if self.salon else self.cancha
        return f"{recurso} — {self.get_solicitante_display()} — {self.fecha}"

    def save(self, *args, **kwargs):
        if self.solicitante:
            nombre = self.solicitante.get_full_name().strip()
            self.solicitante_nombre = nombre or self.solicitante.username
            self.solicitante_email = self.solicitante.email or self.solicitante.username
        super().save(*args, **kwargs)

    def get_solicitante_display(self):
        if self.solicitante:
            nombre = self.solicitante.get_full_name().strip()
            return nombre or self.solicitante.email or self.solicitante.username
        return self.solicitante_nombre or self.solicitante_email or 'Usuario eliminado'


class Bitacora(models.Model):
    admin = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='acciones')
    accion = models.CharField(max_length=255)
    detalles = models.TextField(blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Bitácora'
        verbose_name_plural = 'Bitácoras'
        ordering = ['-creado_en']

    def __str__(self):
        admin_name = self.admin.username if self.admin else "Sistema"
        return f"{self.creado_en.strftime('%d/%m/%Y %H:%M')} - {admin_name}: {self.accion}"


class Cupon(models.Model):
    ESTADO_CHOICES = [
        ('ACTIVO', 'Activo'),
        ('USADO', 'Usado'),
        ('EXPIRADO', 'Expirado'),
    ]
    MOTIVO_CHOICES = [
        ('CANCELACION_USUARIO', 'Cancelación por usuario'),
        ('CANCELACION_SISTEMA', 'Cancelación por sistema'),
    ]

    codigo = models.CharField(max_length=12, unique=True, db_index=True)
    usuario = models.ForeignKey(User, on_delete=models.CASCADE, related_name='cupones')
    reserva_origen = models.ForeignKey(
        'Reserva', on_delete=models.SET_NULL, null=True, blank=True, related_name='cupon_generado'
    )
    reserva_uso = models.ForeignKey(
        'Reserva', on_delete=models.SET_NULL, null=True, blank=True, related_name='cupon_aplicado'
    )
    nombre_recurso = models.CharField(max_length=150, blank=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='ACTIVO')
    motivo = models.CharField(max_length=30, choices=MOTIVO_CHOICES)
    fecha_emision = models.DateTimeField(auto_now_add=True)
    fecha_expiracion = models.DateField()

    class Meta:
        verbose_name = 'Cupón'
        verbose_name_plural = 'Cupones'
        ordering = ['-fecha_emision']

    def __str__(self):
        return f'{self.codigo} — {self.usuario.email} ({self.estado})'

    def esta_vigente(self):
        return self.estado == 'ACTIVO' and date.today() <= self.fecha_expiracion

    @classmethod
    def generar_codigo(cls):
        """Genera un código único con formato XXXX-XXXX (letras y dígitos)."""
        alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # sin O/0/I/1 para evitar confusiones
        for _ in range(20):  # máximo 20 intentos antes de escalar entropía
            parte1 = ''.join(secrets.choice(alphabet) for _ in range(4))
            parte2 = ''.join(secrets.choice(alphabet) for _ in range(4))
            codigo = f'{parte1}-{parte2}'
            if not cls.objects.filter(codigo=codigo).exists():
                return codigo
        # Fallback con mayor entropía si los 20 intentos colisionan (prácticamente imposible)
        while True:
            parte1 = ''.join(secrets.choice(alphabet) for _ in range(5))
            parte2 = ''.join(secrets.choice(alphabet) for _ in range(5))
            codigo = f'{parte1}-{parte2}'
            if not cls.objects.filter(codigo=codigo).exists():
                return codigo


class CuponDescuento(models.Model):
    TIPO_ORGANIZACION_CHOICES = [
        ('UNIVERSIDAD', 'Universidad'),
        ('EMPRESA', 'Empresa'),
        ('ONG', 'ONG'),
        ('CLUB_DEPORTIVO', 'Club Deportivo'),
        ('GOBIERNO', 'Gobierno'),
        ('MUNICIPALIDAD', 'Municipalidad'),
        ('OTRO', 'Otro'),
    ]
    ESTADO_CHOICES = [
        ('ACTIVO', 'Activo'),
        ('EXPIRADO', 'Expirado'),
    ]

    codigo = models.CharField(max_length=15, unique=True, db_index=True)
    tipo_organizacion = models.CharField(max_length=30, choices=TIPO_ORGANIZACION_CHOICES)
    nombre_organizacion = models.CharField(max_length=200)
    autorizado_por = models.CharField(max_length=200)
    cantidad_descuento = models.PositiveIntegerField(help_text='Porcentaje de descuento (1–100)')
    fecha_vencimiento = models.DateField()
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='ACTIVO')
    creado_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name='cupones_descuento_creados'
    )
    fecha_emision = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Cupón de Descuento'
        verbose_name_plural = 'Cupones de Descuento'
        ordering = ['-fecha_emision']

    def __str__(self):
        return f'{self.codigo} — {self.nombre_organizacion} ({self.estado})'

    def esta_vigente(self):
        return self.estado == 'ACTIVO' and date.today() <= self.fecha_vencimiento

    @classmethod
    def generar_codigo(cls):
        alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
        for _ in range(20):
            parte1 = ''.join(secrets.choice(alphabet) for _ in range(4))
            parte2 = ''.join(secrets.choice(alphabet) for _ in range(4))
            codigo = f'D-{parte1}-{parte2}'
            if not cls.objects.filter(codigo=codigo).exists():
                return codigo
        while True:
            parte1 = ''.join(secrets.choice(alphabet) for _ in range(5))
            parte2 = ''.join(secrets.choice(alphabet) for _ in range(5))
            codigo = f'D-{parte1}-{parte2}'
            if not cls.objects.filter(codigo=codigo).exists():
                return codigo


class RegistroTesoreria(models.Model):
    ESTADO_CHOICES = [
        ('PENDIENTE', 'Pendiente'),
        ('VISTO', 'Visto'),
        ('INGRESADO', 'Ingresado'),
    ]

    reserva = models.OneToOneField(
        'Reserva', on_delete=models.CASCADE, related_name='registro_tesoreria'
    )
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='PENDIENTE')
    notas = models.TextField(blank=True, verbose_name='Notas')
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Registro de Tesorería'
        verbose_name_plural = 'Registros de Tesorería'
        ordering = ['-creado_en']

    def __str__(self):
        return f'Tesorería #{self.reserva_id} — {self.get_estado_display()}'


class TransaccionWebpay(models.Model):
    ESTADO_CHOICES = [
        ('INICIADA', 'Iniciada'),
        ('AUTORIZADA', 'Autorizada'),
        ('RECHAZADA', 'Rechazada'),
        ('ANULADA', 'Anulada'),
        ('FALLIDA', 'Fallida'),
    ]

    reserva = models.OneToOneField('Reserva', on_delete=models.CASCADE, related_name='pago_webpay')
    token_ws = models.CharField(max_length=255, unique=True, null=True, blank=True)
    buy_order = models.CharField(max_length=100, unique=True)
    session_id = models.CharField(max_length=100)
    monto = models.DecimalField(max_digits=10, decimal_places=0)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='INICIADA')
    authorization_code = models.CharField(max_length=50, blank=True, null=True)
    payment_type_code = models.CharField(max_length=10, blank=True, null=True)
    response_code = models.IntegerField(blank=True, null=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Transacción Webpay'
        verbose_name_plural = 'Transacciones Webpay'
        ordering = ['-creado_en']

    def __str__(self):
        return f"Webpay {self.buy_order} - {self.get_estado_display()}"
