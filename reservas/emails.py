"""
Funciones de correo electrónico para el sistema de reservas.

Correo recuperación — Link de reset de contraseña → usuario solicitante
Correo A — Nueva solicitud → admins del salón
Correo B — Comprobante de recepción → solicitante
Correo C — Resultado de gestión (aprobada/rechazada/cancelada) → solicitante
Correo D — Respaldo de gestión → admin que gestionó
Correo E — Solicitud de equipos → encargado de comunicaciones (fijo)
Correo F — Aviso de modificación/cancelación por usuario → admins del salón
"""

import logging
from django.core.mail import send_mail, EmailMessage

logger = logging.getLogger(__name__)

TESORERIA_EMAIL = 'jovenprofesional@empedrado.cl'  # TEMPORAL: prueba, revertir a tesoreria@empedrado.cl


def _enviar(subject, message, recipient_list):
    """Wrapper central para send_mail con manejo de errores."""
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=None,
            recipient_list=recipient_list,
        )
    except Exception:
        logger.exception('Error al enviar correo a %s — asunto: %s', recipient_list, subject)


# ─── Recuperación de contraseña ──────────────────────────────────────────────

def correo_recuperacion_clave(user, reset_url):
    nombre = user.first_name or user.email
    _enviar(
        subject='Recuperación de contraseña - Sistema de Reservas IME',
        message=(
            f'Hola {nombre},\n\n'
            f'Recibimos una solicitud para restablecer la contraseña de tu cuenta.\n\n'
            f'Haz clic en el siguiente enlace para elegir una nueva contraseña:\n\n'
            f'{reset_url}\n\n'
            f'Este enlace es de un solo uso y expira en 24 horas.\n\n'
            f'Si no solicitaste este cambio, puedes ignorar este correo con seguridad.\n\n'
            f'Sistema de Reservas IME'
        ),
        recipient_list=[user.email],
    )


# ─── Cambio de email ─────────────────────────────────────────────────────────

def correo_confirmar_cambio_email(user, nuevo_email, confirm_url):
    nombre = user.first_name or user.email
    _enviar(
        subject='Confirma tu nuevo correo - Sistema de Reservas IME',
        message=(
            f'Hola {nombre},\n\n'
            f'Recibimos una solicitud para cambiar el correo de tu cuenta.\n\n'
            f'Haz clic en el siguiente enlace para confirmar tu nuevo correo:\n\n'
            f'{confirm_url}\n\n'
            f'Este enlace es válido por 24 horas. Una vez confirmado, '
            f'tu sesión se cerrará automáticamente y deberás ingresar con el nuevo correo.\n\n'
            f'Si no solicitaste este cambio, ignora este correo.\n\n'
            f'Sistema de Reservas IME'
        ),
        recipient_list=[nuevo_email],
    )


# ─── Restablecimiento de contraseña por administrador ───────────────────────

def correo_clave_restablecida_por_admin(user):
    if not user.email:
        return
    nombre = user.first_name or user.email
    _enviar(
        subject='Tu contraseña fue restablecida - Sistema de Reservas IME',
        message=(
            f'Hola {nombre},\n\n'
            f'Un administrador restableció la contraseña de tu cuenta, '
            f'a solicitud de una situación de emergencia (por ejemplo, '
            f'pérdida de acceso a tu correo de recuperación).\n\n'
            f'Si tú no solicitaste este cambio, contacta de inmediato '
            f'a la administración del sistema.\n\n'
            f'Sistema de Reservas IME'
        ),
        recipient_list=[user.email],
    )


# ─── Correo A ────────────────────────────────────────────────────────────────

def correo_nueva_solicitud_a_admins(reserva):
    if reserva.salon:
        admins_emails = list(
            reserva.salon.admins.filter(email__isnull=False)
            .exclude(email='')
            .values_list('email', flat=True)
        )
    else:
        from django.contrib.auth.models import User
        admins_emails = list(
            User.objects.filter(is_staff=True, email__isnull=False)
            .exclude(email='')
            .values_list('email', flat=True)
        )

    if not admins_emails:
        return

    recurso_nombre = reserva.salon.nombre if reserva.salon else reserva.cancha.nombre
    solicitante = reserva.solicitante
    nombre_completo = solicitante.get_full_name() or solicitante.email

    _enviar(
        subject=f'Nueva Solicitud: {recurso_nombre}',
        message=(
            f'Se ha recibido una nueva solicitud de reserva.\n\n'
            f'Solicitante: {nombre_completo}\n'
            f'Correo: {solicitante.email}\n'
            f'Recinto: {recurso_nombre}\n'
            f'Fecha: {reserva.fecha.strftime("%d/%m/%Y")}\n'
            f'Horario: {reserva.hora_inicio.strftime("%H:%M")} – {reserva.hora_fin.strftime("%H:%M")}\n'
            f'Motivo: {reserva.motivo or "N/A"}\n\n'
            f'Ingrese al sistema de administración para aprobar o rechazar esta solicitud.'
        ),
        recipient_list=admins_emails,
    )


# ─── Correo B ────────────────────────────────────────────────────────────────

def correo_comprobante_a_solicitante(reserva):
    if not reserva.solicitante:
        return
    email = reserva.solicitante.email
    if not email:
        return

    recurso_nombre = reserva.salon.nombre if reserva.salon else reserva.cancha.nombre
    nombre = reserva.solicitante.get_full_name() or reserva.solicitante.email
    estado_texto = 'APROBADA' if reserva.estado == 'APROBADA' else 'PENDIENTE de aprobación'

    if reserva.metodo_pago == 'TRANSFERENCIA':
        notificacion_texto = 'Te notificaremos cuando tu transferencia sea validada y la solicitud gestionada.\n\n'
    else:
        notificacion_texto = 'Tu reserva ya está confirmada y lista para ser disfrutada.\n\n'

    _enviar(
        subject=f'Solicitud Recibida - {recurso_nombre}',
        message=(
            f'Hola {nombre},\n\n'
            f'Tu solicitud de reserva ha sido registrada con el siguiente detalle:\n\n'
            f'Recinto: {recurso_nombre}\n'
            f'Fecha: {reserva.fecha.strftime("%d/%m/%Y")}\n'
            f'Horario: {reserva.hora_inicio.strftime("%H:%M")} – {reserva.hora_fin.strftime("%H:%M")}\n'
            f'Método de Pago: {reserva.get_metodo_pago_display()}\n'
            f'Estado actual: {estado_texto}\n\n'
            f'{notificacion_texto}'
            f'Sistema de Reservas IME'
        ),
        recipient_list=[email],
    )


# ─── Comprobante de transferencia a tesorería ────────────────────────────────

def correo_comprobante_a_tesoreria(reserva):
    comprobante = reserva.comprobante_transferencia
    if not comprobante:
        return

    recurso_nombre = reserva.salon.nombre if reserva.salon else reserva.cancha.nombre
    solicitante_nombre = (
        reserva.solicitante.get_full_name() or reserva.solicitante.email
        if reserva.solicitante else (reserva.solicitante_nombre or 'Usuario eliminado')
    )

    try:
        email = EmailMessage(
            subject=f'Comprobante reserva {reserva.pk}',
            body=(
                f'Se adjunta el comprobante de transferencia de la siguiente reserva.\n\n'
                f'Solicitante: {solicitante_nombre}\n'
                f'Recinto: {recurso_nombre}\n'
                f'Fecha: {reserva.fecha.strftime("%d/%m/%Y")}\n'
                f'Horario: {reserva.hora_inicio.strftime("%H:%M")} – {reserva.hora_fin.strftime("%H:%M")}\n\n'
                f'Sistema de Reservas IME'
            ),
            from_email=None,
            to=[TESORERIA_EMAIL],
        )
        comprobante.open('rb')
        contenido = comprobante.read()
        comprobante.close()
        email.attach(comprobante.name.split('/')[-1], contenido)
        email.send()
    except Exception:
        logger.exception('Error al enviar comprobante de la reserva %s a tesorería', reserva.pk)


# ─── Correo C ────────────────────────────────────────────────────────────────

def correo_resultado_a_solicitante(reserva):
    if not reserva.solicitante:
        return
    email = reserva.solicitante.email
    if not email:
        return

    recurso_nombre = reserva.salon.nombre if reserva.salon else reserva.cancha.nombre
    nombre = reserva.solicitante.get_full_name() or reserva.solicitante.email
    admin_nombre = (
        reserva.gestionado_por.get_full_name() or reserva.gestionado_por.email
        if reserva.gestionado_por else 'el administrador'
    )

    motivo_texto = ''
    if reserva.estado in ('RECHAZADA', 'CANCELADA') and reserva.motivo_rechazo:
        motivo_texto = f'\nMotivo: {reserva.motivo_rechazo}'

    _enviar(
        subject=f'Tu reserva ha sido {reserva.estado}',
        message=(
            f'Hola {nombre},\n\n'
            f'Tu reserva en {recurso_nombre} ha sido {reserva.estado.lower()} '
            f'por {admin_nombre}.\n\n'
            f'Recinto: {recurso_nombre}\n'
            f'Fecha: {reserva.fecha.strftime("%d/%m/%Y")}\n'
            f'Horario: {reserva.hora_inicio.strftime("%H:%M")} – {reserva.hora_fin.strftime("%H:%M")}'
            f'{motivo_texto}\n\n'
            f'Sistema de Reservas IME'
        ),
        recipient_list=[email],
    )


# ─── Correo D ────────────────────────────────────────────────────────────────

def correo_respaldo_a_admin(reserva):
    if not reserva.gestionado_por or not reserva.gestionado_por.email:
        return

    recurso_nombre = reserva.salon.nombre if reserva.salon else reserva.cancha.nombre
    solicitante_nombre = (
        reserva.solicitante.get_full_name() or reserva.solicitante.email
        if reserva.solicitante else 'Usuario eliminado'
    )

    _enviar(
        subject=f'Respaldo Gestión - {recurso_nombre} ({reserva.estado})',
        message=(
            f'Este es un comprobante de la acción que realizaste.\n\n'
            f'Acción: {reserva.estado}\n'
            f'Recinto: {recurso_nombre}\n'
            f'Solicitante: {solicitante_nombre}\n'
            f'Fecha: {reserva.fecha.strftime("%d/%m/%Y")}\n'
            f'Horario: {reserva.hora_inicio.strftime("%H:%M")} – {reserva.hora_fin.strftime("%H:%M")}\n\n'
            f'Sistema de Reservas IME'
        ),
        recipient_list=[reserva.gestionado_por.email],
    )


# ─── Correo F ────────────────────────────────────────────────────────────────

def correo_modificacion_a_admins(reserva, accion='modificada'):
    if reserva.salon:
        admins_emails = list(
            reserva.salon.admins.filter(email__isnull=False)
            .exclude(email='')
            .values_list('email', flat=True)
        )
    else:
        from django.contrib.auth.models import User
        admins_emails = list(
            User.objects.filter(is_staff=True, email__isnull=False)
            .exclude(email='')
            .values_list('email', flat=True)
        )

    if not admins_emails:
        return

    recurso_nombre = reserva.salon.nombre if reserva.salon else reserva.cancha.nombre
    solicitante_nombre = (
        reserva.solicitante.get_full_name() or reserva.solicitante.email
        if reserva.solicitante else 'Usuario eliminado'
    )

    if accion == 'cancelada':
        asunto = f'Reserva Cancelada por Solicitante - {recurso_nombre}'
        accion_texto = 'canceló'
    else:
        asunto = f'Reserva Modificada - {recurso_nombre}'
        accion_texto = 'modificó'

    _enviar(
        subject=asunto,
        message=(
            f'El solicitante {accion_texto} su reserva.\n\n'
            f'Solicitante: {solicitante_nombre}\n'
            f'Recinto: {recurso_nombre}\n'
            f'Fecha: {reserva.fecha.strftime("%d/%m/%Y")}\n'
            f'Horario: {reserva.hora_inicio.strftime("%H:%M")} – {reserva.hora_fin.strftime("%H:%M")}\n\n'
            f'Sistema de Reservas IME'
        ),
        recipient_list=admins_emails,
    )
