from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Q
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from datetime import date, datetime, time, timedelta
from functools import wraps
from django.http import JsonResponse, HttpResponseNotAllowed
from django.core.cache import cache
from django.core.paginator import Paginator
from django.conf import settings
from django.db import transaction, IntegrityError
import json
import re
import logging
from PIL import Image, UnidentifiedImageError
from .models import UserProfile, Reserva, CambioEmailPendiente, Notificacion, Bitacora, Cupon, RegistroTesoreria
from complejos.models import ConfigCancha, BloqueoHorario, BloqueoPeriodo, Salon, DatosTransferencia
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode, url_has_allowed_host_and_scheme
from django.utils.encoding import force_bytes, force_str
from django.utils import timezone
from django.urls import reverse
import json as json_lib
from .emails import (
    correo_recuperacion_clave,
    correo_confirmar_cambio_email,
    correo_nueva_solicitud_a_admins,
    correo_comprobante_a_solicitante,
    correo_resultado_a_solicitante,
    correo_respaldo_a_admin,
    correo_modificacion_a_admins,
)

logger = logging.getLogger(__name__)

# Estados que bloquean un slot horario (reserva activa u organización esperando firma)
ESTADOS_OCUPAN_SLOT = ['PENDIENTE', 'PENDIENTE_ALCALDE', 'APROBADA']

RUT_REGEX = re.compile(r'^\d{7,8}-[\dkK]$')


def normalizar_rut(rut):
    sin_puntos = rut.replace('.', '').strip().upper()
    limpio = ''.join(ch for ch in sin_puntos if ch.isdigit() or ch in {'K', '-'})

    if limpio.count('-') != 1:
        return ''

    cuerpo, dv = limpio.split('-', 1)
    if not cuerpo.isdigit() or len(dv) != 1:
        return ''

    return f'{cuerpo}-{dv.upper()}'


def rut_valido(rut):
    rut_normalizado = normalizar_rut(rut)
    if not RUT_REGEX.match(rut_normalizado):
        return False

    cuerpo, dv = rut_normalizado.split('-')

    suma = 0
    multiplicador = 2
    for digito in reversed(cuerpo):
        suma += int(digito) * multiplicador
        multiplicador = 2 if multiplicador == 7 else multiplicador + 1

    resto = 11 - (suma % 11)
    dv_esperado = '0' if resto == 11 else 'K' if resto == 10 else str(resto)
    return dv == dv_esperado


def staff_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not (request.user.is_staff or request.user.is_superuser):
            return redirect('inicio')
        return view_func(request, *args, **kwargs)
    return wrapper


def alcalde_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        perfil = getattr(request.user, 'perfil', None)
        if not (perfil and perfil.es_alcalde):
            return redirect('inicio')
        return view_func(request, *args, **kwargs)
    return wrapper


def tesorero_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        perfil = getattr(request.user, 'perfil', None)
        if not (perfil and perfil.es_tesorero):
            return redirect('inicio')
        return view_func(request, *args, **kwargs)
    return wrapper


def _rate_limit_exceeded(request, action, limit=5, period=300):
    """Devuelve True si la IP superó el límite de intentos para la acción dada.

    Usa REMOTE_ADDR directamente para evitar que clientes malintencionados
    falsifiquen X-Forwarded-For y evadan el límite. Si el sistema se despliega
    detrás de un proxy de confianza configurable, TRUSTED_PROXY_IPS puede
    habilitarse en settings.py.
    """
    trusted_proxies = getattr(settings, 'TRUSTED_PROXY_IPS', [])
    remote_addr = request.META.get('REMOTE_ADDR', 'unknown')

    if trusted_proxies and remote_addr in trusted_proxies:
        # Solo confiar en X-Forwarded-For si el proxy está en la lista blanca
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        ip = forwarded.split(',')[0].strip() if forwarded else remote_addr
    else:
        ip = remote_addr

    key = f'rl:{action}:{ip}'
    count = cache.get(key, 0)
    if count >= limit:
        return True
    cache.set(key, count + 1, period)
    return False


def inicio(request):
    if request.user.is_authenticated:
        perfil = getattr(request.user, 'perfil', None)
        if perfil and perfil.es_alcalde:
            return redirect('perfil_alcalde')
    return render(request, 'inicio.html')


def _reserva_generica(request, nombre_cancha, template_name, context_key):
    """Función auxiliar para manejar la lógica común de reserva de recintos."""
    config = ConfigCancha.objects.filter(nombre=nombre_cancha).first()
    
    # Manejar peticiones AJAX para obtener bloqueos y reservas de un día
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('ajax'):
        fecha_str = request.GET.get('fecha')
        if config and fecha_str:
            try:
                fecha = datetime.strptime(fecha_str, "%Y-%m-%d").date()
                bloqueos = BloqueoHorario.objects.filter(cancha=config, fecha=fecha)
                reservas = Reserva.objects.filter(
                    cancha=config,
                    fecha=fecha,
                    estado__in=ESTADOS_OCUPAN_SLOT,
                )
                bloqueado_periodo = BloqueoPeriodo.objects.filter(
                    cancha=config,
                    fecha_inicio__lte=fecha,
                    fecha_fin__gte=fecha,
                ).exists()

                horas_bloqueadas = [b.hora.strftime("%H:%M") for b in bloqueos]

                # Genera todos los slots cubiertos por cada reserva (org puede abarcar múltiples horas)
                horas_reservadas = set()
                _slot = timedelta(minutes=config.intervalo_minutos)
                for r in reservas:
                    cursor = datetime.combine(fecha, r.hora_inicio)
                    fin    = datetime.combine(fecha, r.hora_fin)
                    while cursor < fin:
                        horas_reservadas.add(cursor.strftime("%H:%M"))
                        cursor += _slot

                return JsonResponse({
                    'bloqueos': list(set(horas_bloqueadas)),
                    'reservadas': list(horas_reservadas),
                    'bloqueado_periodo': bloqueado_periodo,
                })
            except ValueError:
                pass
        return JsonResponse({'bloqueos': [], 'reservadas': [], 'bloqueado_periodo': False})

    user_profile = None
    tiene_cupon = False
    if request.user.is_authenticated:
        user_profile, _ = UserProfile.objects.get_or_create(user=request.user)
        if config:
            tiene_cupon = Cupon.objects.filter(
                usuario=request.user,
                estado='ACTIVO',
                nombre_recurso=config.nombre,
                fecha_expiracion__gte=date.today(),
            ).exists()

    # Datos para restricciones en el HTML
    hoy = date.today()
    hoy_val = hoy.strftime('%Y-%m-%d')

    # Calcular días bloqueados en períodos futuros para extender la ventana de reserva
    dias_bloqueados = 0
    if config:
        periodos = BloqueoPeriodo.objects.filter(cancha=config, fecha_fin__gte=hoy)
        for p in periodos:
            inicio = max(p.fecha_inicio, hoy)
            dias_bloqueados += (p.fecha_fin - inicio).days + 1

    limite_val = (hoy + timedelta(days=31 + dias_bloqueados)).strftime('%Y-%m-%d')

    # Obtener datos de transferencia
    datos_transferencia, _ = DatosTransferencia.objects.get_or_create(id=1)

    return render(request, template_name, {
        context_key: config,
        'user_profile': user_profile,
        'hoy': hoy_val,
        'limite_futuro': limite_val,
        'dias_bloqueados_futuros': dias_bloqueados,
        'datos_transferencia': datos_transferencia,
        'tiene_cupon': tiene_cupon,
    })

def reserva_tenis(request):
    return _reserva_generica(request, 'Cancha de Tenis', 'reservar_tenis.html', 'config_tenis')

def reserva_gimnasio(request):
    return _reserva_generica(request, 'Gimnasio Municipal', 'reservar_gimnasio.html', 'config_gimnasio')

def reserva_futbolito(request):
    return _reserva_generica(request, 'Cancha de Futbolito', 'reservar_futbolito.html', 'config_futbolito')

def reserva_estadio(request):
    return _reserva_generica(request, 'Estadio Municipal', 'reservar_estadio.html', 'config_estadio')

@login_required
def toggle_bloqueo(request):
    if not request.user.is_superuser:
        return JsonResponse({'error': 'No autorizado'}, status=403)
    
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            cancha_id = data.get('cancha_id')
            fecha_str = data.get('fecha')
            hora_str = data.get('hora')
            
            cancha = get_object_or_404(ConfigCancha, id=cancha_id)
            fecha = datetime.strptime(fecha_str, "%Y-%m-%d").date()
            # Asegurar formato HH:MM
            hora = datetime.strptime(hora_str, "%H:%M").time()

            bloqueo, created = BloqueoHorario.objects.get_or_create(
                cancha=cancha, fecha=fecha, hora=hora
            )

            if not created:
                bloqueo.delete()
                fecha_fmt = fecha.strftime("%d/%m/%Y")
                hora_fmt = hora.strftime("%H:%M")
                usuarios_todos = User.objects.filter(
                    is_active=True, is_staff=False,
                    perfil__es_tesorero=False, perfil__es_alcalde=False,
                )
                for u in usuarios_todos:
                    Notificacion.objects.create(
                        user=u,
                        tipo='RESERVA_APROBADA',
                        titulo='Horario disponible nuevamente',
                        mensaje=(
                            f"El horario de las {hora_fmt} del {fecha_fmt} en {cancha.nombre} "
                            f"ha sido habilitado nuevamente por la administración. "
                            f"Ya puedes reservar ese horario."
                        ),
                    )
                return JsonResponse({'status': 'success', 'action': 'unblocked'})

            fecha_fmt = fecha.strftime("%d/%m/%Y")
            hora_fmt = hora.strftime("%H:%M")

            # 1. Notificación general a usuarios no-staff (excluye tesorero y alcalde)
            usuarios_todos = User.objects.filter(
                is_active=True, is_staff=False,
                perfil__es_tesorero=False, perfil__es_alcalde=False,
            )
            for u in usuarios_todos:
                Notificacion.objects.create(
                    user=u,
                    tipo='RESERVA_CANCELADA',
                    titulo='Horario no disponible',
                    mensaje=(
                        f"El horario de las {hora_fmt} del {fecha_fmt} en {cancha.nombre} "
                        f"ha sido restringido por la administración. "
                        f"No podrás crear ni editar reservas para ese horario."
                    ),
                )

            # 2. Notificación adicional a usuarios con reservas activas en esa hora
            reservas_afectadas = Reserva.objects.filter(
                cancha=cancha,
                fecha=fecha,
                hora_inicio=hora,
                estado__in=ESTADOS_OCUPAN_SLOT,
            ).select_related('solicitante')

            for reserva in reservas_afectadas:
                if reserva.solicitante:
                    Notificacion.objects.create(
                        user=reserva.solicitante,
                        tipo='RESERVA_CANCELADA',
                        titulo='Tu reserva está en un horario restringido',
                        mensaje=(
                            f'Tu reserva en {cancha.nombre} el {fecha_fmt} a las {hora_fmt} '
                            f'está en un horario restringido por la administración. '
                            f'Debes cambiarla a un horario disponible. '
                            f'De no hacerlo, será cancelada automáticamente '
                            f'y recibirás un cupón para una futura reserva gratis.'
                        ),
                    )

            avisos = reservas_afectadas.count()
            return JsonResponse({
                'status': 'success',
                'action': 'blocked',
                'avisos': avisos,
            })
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    
    return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)


@login_required
def check_periodo_bloqueado(request):
    """Devuelve si una fecha está dentro de un período bloqueado para una cancha."""
    cancha_id = request.GET.get('cancha_id')
    fecha_str = request.GET.get('fecha')
    if not cancha_id or not fecha_str:
        return JsonResponse({'bloqueado': False})
    try:
        fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
        bloqueado = BloqueoPeriodo.objects.filter(
            cancha_id=cancha_id,
            fecha_inicio__lte=fecha,
            fecha_fin__gte=fecha,
        ).exists()
        return JsonResponse({'bloqueado': bloqueado})
    except (ValueError, TypeError):
        return JsonResponse({'bloqueado': False})


@login_required
def check_hora_reservada(request):
    """Devuelve si una hora ya está reservada en una cancha/fecha (excluyendo la reserva actual)."""
    cancha_id  = request.GET.get('cancha_id')
    fecha_str  = request.GET.get('fecha')
    hora_str   = request.GET.get('hora')
    reserva_id = request.GET.get('reserva_id')
    if not cancha_id or not fecha_str or not hora_str:
        return JsonResponse({'reservado': False})
    try:
        fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
        hora  = datetime.strptime(hora_str, '%H:%M').time()
        # Detecta solapamiento real: la hora cae dentro del rango de cualquier reserva existente
        qs = Reserva.objects.filter(
            cancha_id=cancha_id,
            fecha=fecha,
            hora_inicio__lte=hora,
            hora_fin__gt=hora,
            estado__in=ESTADOS_OCUPAN_SLOT,
        )
        if reserva_id:
            qs = qs.exclude(pk=reserva_id)
        return JsonResponse({'reservado': qs.exists()})
    except (ValueError, TypeError):
        return JsonResponse({'reservado': False})


@login_required
def check_bloqueo_horario(request):
    """Devuelve las horas bloqueadas manualmente para una cancha en una fecha."""
    cancha_id = request.GET.get('cancha_id')
    fecha_str = request.GET.get('fecha')
    if not cancha_id or not fecha_str:
        return JsonResponse({'horas_bloqueadas': []})
    try:
        fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
        horas = list(
            BloqueoHorario.objects.filter(cancha_id=cancha_id, fecha=fecha)
            .values_list('hora', flat=True)
        )
        # Serializar a strings "HH:MM"
        horas_str = [h.strftime('%H:%M') for h in horas]
        return JsonResponse({'horas_bloqueadas': horas_str})
    except (ValueError, TypeError):
        return JsonResponse({'horas_bloqueadas': []})


@login_required
def bloquear_periodo(request):
    if not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'No autorizado'}, status=403)

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            cancha_id = data.get('cancha_id')
            fecha_inicio_str = data.get('fecha_inicio')
            fecha_fin_str = data.get('fecha_fin')

            cancha = get_object_or_404(ConfigCancha, id=cancha_id)
            fecha_inicio = datetime.strptime(fecha_inicio_str, "%Y-%m-%d").date()
            fecha_fin = datetime.strptime(fecha_fin_str, "%Y-%m-%d").date()

            if fecha_fin < fecha_inicio:
                return JsonResponse({'status': 'error', 'message': 'La fecha de fin debe ser posterior a la de inicio.'}, status=400)

            # Verificar solapamiento con bloqueos existentes para la misma cancha
            solapamiento = BloqueoPeriodo.objects.filter(
                cancha=cancha,
                fecha_inicio__lte=fecha_fin,
                fecha_fin__gte=fecha_inicio,
            ).first()
            if solapamiento:
                fi = solapamiento.fecha_inicio.strftime("%d/%m/%Y")
                ff = solapamiento.fecha_fin.strftime("%d/%m/%Y")
                return JsonResponse({
                    'status': 'error',
                    'message': f'Ya existe un período bloqueado que se superpone con estas fechas ({fi} → {ff}).'
                }, status=400)

            BloqueoPeriodo.objects.create(
                cancha=cancha,
                fecha_inicio=fecha_inicio,
                fecha_fin=fecha_fin,
                creado_por=request.user,
            )

            registrar_accion(request.user, "Bloqueó período", f"{cancha.nombre}: {fecha_inicio.strftime('%d/%m/%Y')} → {fecha_fin.strftime('%d/%m/%Y')}")

            fecha_inicio_fmt = fecha_inicio.strftime("%d/%m/%Y")
            fecha_fin_fmt = fecha_fin.strftime("%d/%m/%Y")

            # 1. Notificación general a usuarios no-staff (excluye tesorero y alcalde)
            usuarios_todos = User.objects.filter(
                is_active=True, is_staff=False,
                perfil__es_tesorero=False, perfil__es_alcalde=False,
            )
            for u in usuarios_todos:
                Notificacion.objects.create(
                    user=u,
                    tipo='RESERVA_CANCELADA',
                    titulo='Período no disponible',
                    mensaje=(
                        f"{cancha.nombre} estará no disponible desde el {fecha_inicio_fmt} hasta el {fecha_fin_fmt}. "
                        f"Durante este período no podrás crear ni editar reservas para estas fechas."
                    ),
                )

            # 2. Notificación adicional y personalizada a usuarios con reservas activas en el rango,
            #    agrupando todas sus reservas afectadas en un solo mensaje.
            reservas_afectadas = (
                Reserva.objects
                .filter(
                    cancha=cancha,
                    fecha__gte=fecha_inicio,
                    fecha__lte=fecha_fin,
                    estado__in=ESTADOS_OCUPAN_SLOT,
                )
                .select_related('solicitante')
                .order_by('fecha', 'hora_inicio')
            )

            reservas_por_usuario = {}
            for reserva in reservas_afectadas:
                if reserva.solicitante_id is None:
                    continue
                reservas_por_usuario.setdefault(reserva.solicitante, []).append(reserva)

            for usuario, reservas in reservas_por_usuario.items():
                lineas = [
                    f"- {r.fecha.strftime('%d/%m/%Y')} a las {r.hora_inicio.strftime('%H:%M')}"
                    for r in reservas
                ]
                detalle = "\n".join(lineas)
                plural = "reserva" if len(reservas) == 1 else "reservas"
                Notificacion.objects.create(
                    user=usuario,
                    tipo='RESERVA_CANCELADA',
                    titulo='Tienes reservas en un período restringido',
                    mensaje=(
                        f"Tienes {len(reservas)} {plural} en {cancha.nombre} dentro del período bloqueado "
                        f"({fecha_inicio_fmt} – {fecha_fin_fmt}) que {'debe ser cambiada' if len(reservas) == 1 else 'deben ser cambiadas'} "
                        f"a un horario disponible. De no hacerlo, "
                        f"{'será cancelada' if len(reservas) == 1 else 'serán canceladas'} automáticamente "
                        f"y recibirás un cupón por cada una para una futura reserva gratis.\n\n"
                        f"{'Reserva afectada:' if len(reservas) == 1 else 'Reservas afectadas:'}\n{detalle}"
                    ),
                )

            avisos = len(reservas_por_usuario)
            return JsonResponse({
                'status': 'success',
                'message': 'Período bloqueado correctamente.',
                'avisos': avisos,
            })
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)

    return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)


@login_required
def toggle_cancha_status(request):
    if not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'No autorizado'}, status=403)
        
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            cancha_id = data.get('cancha_id')
            cancha = get_object_or_404(ConfigCancha, id=cancha_id)
            cancha.activo = not cancha.activo
            cancha.save()
            
            registrar_accion(request.user, f"{'Habilitó' if cancha.activo else 'Inhabilitó'} el complejo", f"ID: {cancha.id} - Nombre: {cancha.nombre}")
            
            return JsonResponse({
                'status': 'success',
                'activo': cancha.activo,
                'message': f'Complejo {"habilitado" if cancha.activo else "inhabilitado"} correctamente.'
            })
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)


INTERVALOS_VALIDOS = {30, 45, 60, 90, 120}
DIAS_FINDE = {5, 6}  # 5 = Sábado, 6 = Domingo


@login_required
def toggle_findes_semana_cancha(request):
    """Habilita o deshabilita Sábado y Domingo como días reservables."""
    if not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'No autorizado'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)

    try:
        data = json.loads(request.body)
        cancha = get_object_or_404(ConfigCancha, id=data.get('cancha_id'))
        dias_actuales = set(cancha.dias_semana_list())
        findes_activos = DIAS_FINDE.issubset(dias_actuales)

        if findes_activos:
            # Antes de quitar el finde verificar reservas activas sábado/domingo
            hoy = date.today()
            reservas_finde = (
                Reserva.objects
                .filter(cancha=cancha, estado__in=ESTADOS_OCUPAN_SLOT, fecha__gte=hoy)
                .order_by('-fecha')
            )
            reservas_finde = [r for r in reservas_finde if r.fecha.weekday() in DIAS_FINDE]
            if reservas_finde:
                ultima = reservas_finde[0].fecha
                disponible_desde = ultima + timedelta(days=1)
                total = len(reservas_finde)
                plural = 'reserva activa' if total == 1 else 'reservas activas'
                return JsonResponse({
                    'status': 'error',
                    'message': (
                        f'No puedes deshabilitar los fines de semana: existen {total} {plural} '
                        f'de sábado o domingo en {cancha.nombre}. La última es el '
                        f'{ultima.strftime("%d/%m/%Y")}. Podrás aplicar este cambio desde el '
                        f'{disponible_desde.strftime("%d/%m/%Y")}.'
                    ),
                }, status=400)
            nuevos = sorted(dias_actuales - DIAS_FINDE)
            accion = 'deshabilitó'
        else:
            nuevos = sorted(dias_actuales | DIAS_FINDE)
            accion = 'habilitó'

        cancha.dias_semana = ','.join(str(d) for d in nuevos)
        cancha.save(update_fields=['dias_semana'])

        registrar_accion(
            request.user,
            f"{accion.capitalize()} fines de semana",
            f"{cancha.nombre} (días activos: {cancha.dias_semana})",
        )

        return JsonResponse({
            'status': 'success',
            'findes_activos': not findes_activos,
            'message': f'Fines de semana {accion}s correctamente.',
        })
    except (ValueError, TypeError, json.JSONDecodeError) as e:
        return JsonResponse({'status': 'error', 'message': f'Datos inválidos: {e}'}, status=400)


@login_required
def actualizar_horario_cancha(request):
    """Actualiza hora_inicio, hora_fin e intervalo_minutos de una ConfigCancha."""
    if not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'No autorizado'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)

    try:
        data = json.loads(request.body)
        cancha_id = data.get('cancha_id')
        hora_inicio_str = (data.get('hora_inicio') or '').strip()
        hora_fin_str = (data.get('hora_fin') or '').strip()
        intervalo = data.get('intervalo_minutos')

        cancha = get_object_or_404(ConfigCancha, id=cancha_id)
        hora_inicio = datetime.strptime(hora_inicio_str, "%H:%M").time()
        hora_fin = datetime.strptime(hora_fin_str, "%H:%M").time()
        intervalo = int(intervalo)

        if hora_fin <= hora_inicio:
            return JsonResponse({'status': 'error', 'message': 'La hora de fin debe ser posterior a la de inicio.'}, status=400)
        if intervalo not in INTERVALOS_VALIDOS:
            opciones = ', '.join(str(i) for i in sorted(INTERVALOS_VALIDOS))
            return JsonResponse({'status': 'error', 'message': f'Intervalo inválido. Valores permitidos: {opciones} minutos.'}, status=400)

        hay_cambios = (
            cancha.hora_inicio != hora_inicio
            or cancha.hora_fin != hora_fin
            or cancha.intervalo_minutos != intervalo
        )
        if hay_cambios:
            hoy = date.today()
            ultima_reserva = (
                Reserva.objects
                .filter(cancha=cancha, estado__in=ESTADOS_OCUPAN_SLOT, fecha__gte=hoy)
                .order_by('-fecha')
                .first()
            )
            if ultima_reserva:
                total = Reserva.objects.filter(
                    cancha=cancha, estado__in=ESTADOS_OCUPAN_SLOT, fecha__gte=hoy
                ).count()
                disponible_desde = ultima_reserva.fecha + timedelta(days=1)
                plural = 'reserva activa' if total == 1 else 'reservas activas'
                return JsonResponse({
                    'status': 'error',
                    'message': (
                        f'No puedes modificar los horarios: existen {total} {plural} en {cancha.nombre} '
                        f'a partir de hoy. La última es el {ultima_reserva.fecha.strftime("%d/%m/%Y")}. '
                        f'Podrás aplicar este cambio desde el {disponible_desde.strftime("%d/%m/%Y")}.'
                    ),
                }, status=400)

        anterior = (
            cancha.hora_inicio.strftime('%H:%M'),
            cancha.hora_fin.strftime('%H:%M'),
            cancha.intervalo_minutos,
        )
        cancha.hora_inicio = hora_inicio
        cancha.hora_fin = hora_fin
        cancha.intervalo_minutos = intervalo
        cancha.save(update_fields=['hora_inicio', 'hora_fin', 'intervalo_minutos'])

        registrar_accion(
            request.user,
            "Actualizó horarios del complejo",
            f"{cancha.nombre}: {anterior[0]}-{anterior[1]} ({anterior[2]} min) → "
            f"{hora_inicio.strftime('%H:%M')}-{hora_fin.strftime('%H:%M')} ({intervalo} min)",
        )

        return JsonResponse({'status': 'success', 'message': 'Horarios actualizados correctamente.'})

    except (ValueError, TypeError, json.JSONDecodeError) as e:
        return JsonResponse({'status': 'error', 'message': f'Datos inválidos: {e}'}, status=400)


@login_required
def crear_reserva_admin(request):
    """Permite a un superuser crear una reserva personal manualmente (siempre APROBADA)."""
    if not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'No autorizado'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)

    try:
        data = json.loads(request.body)
        cancha = get_object_or_404(ConfigCancha, id=data.get('cancha_id'))
        fecha = datetime.strptime(data.get('fecha'), "%Y-%m-%d").date()
        hora_inicio = datetime.strptime(data.get('hora'), "%H:%M").time()
        nombre = (data.get('solicitante_nombre') or '').strip()
        rut = normalizar_rut((data.get('solicitante_rut') or '').strip())
        telefono = (data.get('solicitante_telefono') or '').strip()
        email = (data.get('solicitante_email') or '').strip()

        if not nombre:
            return JsonResponse({'status': 'error', 'message': 'Debes ingresar el nombre del solicitante.'}, status=400)

        if fecha.weekday() not in cancha.dias_semana_list():
            return JsonResponse({'status': 'error', 'message': 'Ese día no está habilitado para este recinto.'}, status=400)

        if BloqueoPeriodo.objects.filter(cancha=cancha, fecha_inicio__lte=fecha, fecha_fin__gte=fecha).exists():
            return JsonResponse({'status': 'error', 'message': 'Esa fecha está dentro de un período bloqueado.'}, status=400)

        hora_fin_dt = datetime.combine(fecha, hora_inicio) + timedelta(minutes=cancha.intervalo_minutos)
        hora_fin = hora_fin_dt.time()

        with transaction.atomic():
            if BloqueoHorario.objects.select_for_update().filter(cancha=cancha, fecha=fecha, hora=hora_inicio).exists():
                return JsonResponse({'status': 'error', 'message': 'Ese horario está bloqueado.'}, status=400)
            if Reserva.objects.select_for_update().filter(
                cancha=cancha, fecha=fecha,
                hora_inicio__lt=hora_fin, hora_fin__gt=hora_inicio,
                estado__in=ESTADOS_OCUPAN_SLOT,
            ).exists():
                return JsonResponse({'status': 'error', 'message': 'Ese horario ya está reservado.'}, status=400)

            reserva = Reserva.objects.create(
                cancha=cancha,
                solicitante=None,
                solicitante_nombre=nombre,
                solicitante_rut=rut if rut_valido(rut) else '',
                solicitante_email=email,
                solicitante_telefono=telefono,
                fecha=fecha,
                hora_inicio=hora_inicio,
                hora_fin=hora_fin,
                estado='APROBADA',
                metodo_pago='TARJETA',
                tipo_reserva='PERSONAL',
                gestionado_por=request.user,
            )
            RegistroTesoreria.objects.create(reserva=reserva)

        registrar_accion(
            request.user,
            "Creó reserva personal manualmente",
            f"{cancha.nombre} {fecha.strftime('%d/%m/%Y')} {hora_inicio.strftime('%H:%M')} para {nombre}",
        )

        return JsonResponse({
            'status': 'success',
            'reserva_id': reserva.id,
            'estado': 'APROBADA',
            'message': 'Reserva creada y aprobada.',
        })
    except (ValueError, TypeError, json.JSONDecodeError) as e:
        return JsonResponse({'status': 'error', 'message': f'Datos inválidos: {e}'}, status=400)


@login_required
def crear_reserva_organizacion(request):
    """Reserva de organización: accesible a admins (flujo completo) y a usuarios org (datos pre-cargados)."""
    _perfil = getattr(request.user, 'perfil', None)
    _es_admin = request.user.is_staff or request.user.is_superuser
    _es_org_user = bool(_perfil and _perfil.es_organizacion)
    if not (_es_admin or _es_org_user):
        return redirect('inicio')

    cancha_id = request.GET.get('cancha_id') or request.POST.get('cancha_id')
    fecha_str = request.GET.get('fecha') or request.POST.get('fecha')
    hora_str = request.GET.get('hora') or request.POST.get('hora')
    cancha = get_object_or_404(ConfigCancha, id=cancha_id) if cancha_id else None

    # Consumir mensajes de sesión anteriores sin mostrarlos
    list(messages.get_messages(request))

    # Datos pre-cargados para usuarios org (no admin)
    _prefill = {}
    if _es_org_user and not _es_admin:
        _prefill = {
            'nombre_organizacion': _perfil.nombre_organizacion,
            'representante_nombre': request.user.get_full_name(),
            'representante_rut': _perfil.rut or '',
            'representante_telefono': _perfil.telefono or '',
            'representante_email': request.user.email,
        }

    def _ctx_base(extra=None):
        """Contexto común para el paso 1 (formulario)."""
        precio_cancha = cancha.precio if cancha else 0
        orgs_existentes = User.objects.filter(
            is_staff=False, perfil__es_organizacion=True
        ).select_related('perfil').order_by('perfil__nombre_organizacion') if _es_admin else []
        ctx = {
            'cancha': cancha, 'fecha': fecha_str, 'hora': hora_str,
            'precio_cancha': precio_cancha, 'precio_descuento': precio_cancha // 2,
            'orgs_existentes': orgs_existentes, 'step': 'form',
            'es_org_user': _es_org_user and not _es_admin,
            'prefill': _prefill,
        }
        if extra:
            ctx.update(extra)
        return ctx

    def _calcular_hora_fin(fecha, hora_inicio, modo_fin_dia, hora_fin_manual_str):
        if modo_fin_dia:
            return cancha.hora_fin
        if hora_fin_manual_str:
            try:
                return datetime.strptime(hora_fin_manual_str, "%H:%M").time()
            except ValueError:
                pass
        return (datetime.combine(fecha, hora_inicio) + timedelta(minutes=cancha.intervalo_minutos)).time()

    if request.method != 'POST':
        return render(request, 'panel/crear_reserva_organizacion.html', _ctx_base({'form_data': _prefill}))

    step = request.POST.get('step', 'preview')

    # Leer datos del formulario
    nombre_org = request.POST.get('nombre_organizacion', '').strip()
    rep_nombre = request.POST.get('representante_nombre', '').strip()
    rep_rut = request.POST.get('representante_rut', '').strip()
    rep_telefono = request.POST.get('representante_telefono', '').strip()
    rep_email = request.POST.get('representante_email', '').strip()
    motivo = request.POST.get('motivo', '').strip()
    modo_fin_dia = request.POST.get('modo_fin_dia') == '1'
    hora_fin_manual_str = request.POST.get('hora_fin_manual', '').strip()

    # Validar datos
    errores = []
    if not nombre_org:
        errores.append('El nombre de la organización es requerido.')
    if not rep_nombre:
        errores.append('El nombre del representante legal es requerido.')
    rep_rut_norm = normalizar_rut(rep_rut) if rep_rut else ''
    if rep_rut and not rut_valido(rep_rut_norm):
        errores.append('El RUT del representante no es válido.')
    if not rep_email:
        errores.append('El correo del representante es requerido.')
    if cancha and not cancha.precio:
        errores.append('Esta cancha no tiene precio configurado. Ve a Panel → Precios para definirlo.')

    fecha = None
    hora_inicio = None
    if not fecha_str or not hora_str or not cancha:
        errores.append('Faltan datos del horario. Vuelve al calendario e intenta de nuevo.')
    else:
        try:
            fecha = datetime.strptime(fecha_str, "%Y-%m-%d").date()
            hora_inicio = datetime.strptime(hora_str, "%H:%M").time()
        except ValueError:
            errores.append('Fecha u hora inválida.')

    if errores:
        return render(request, 'panel/crear_reserva_organizacion.html', _ctx_base({
            'form_data': request.POST, 'errores': errores,
        }))

    hora_fin = _calcular_hora_fin(fecha, hora_inicio, modo_fin_dia, hora_fin_manual_str)
    precio_original = cancha.precio
    precio_descuento = precio_original // 2

    meses_es = {1:'enero',2:'febrero',3:'marzo',4:'abril',5:'mayo',6:'junio',
                7:'julio',8:'agosto',9:'septiembre',10:'octubre',11:'noviembre',12:'diciembre'}
    fecha_carta = f"{timezone.now().day} de {meses_es[timezone.now().month]} de {timezone.now().year}"
    fecha_res_fmt = f"{fecha.day} de {meses_es[fecha.month]} de {fecha.year}"

    # ── PASO 1 → VISTA PREVIA ──
    if step == 'preview':
        from urllib.parse import urlencode
        preview_params = urlencode({
            'cancha_id': cancha.pk,
            'fecha': fecha_str,
            'hora_inicio': hora_inicio.strftime('%H:%M'),
            'hora_fin': hora_fin.strftime('%H:%M'),
            'nombre_org': nombre_org,
            'rep_nombre': rep_nombre,
            'rep_rut': rep_rut_norm,
            'motivo': motivo,
            'modo_fin_dia': '1' if modo_fin_dia else '0',
            'nombre_recinto': cancha.nombre,
        })
        preview_pdf_url = f"/carta/preview-pdf/?{preview_params}"
        hora_inicio_fmt = hora_inicio.strftime('%H:%M')
        hora_fin_fmt = hora_fin.strftime('%H:%M')
        return render(request, 'panel/crear_reserva_organizacion.html', {
            'cancha': cancha, 'fecha': fecha_str, 'hora': hora_str,
            'precio_cancha': precio_original, 'precio_descuento': precio_descuento,
            'form_data': request.POST,
            'step': 'preview',
            'preview_pdf_url': preview_pdf_url,
            'preview': {
                'nombre_org': nombre_org,
                'rep_nombre': rep_nombre,
                'rep_rut': rep_rut_norm,
                'rep_telefono': rep_telefono,
                'rep_email': rep_email,
                'motivo': motivo,
                'modo_fin_dia': modo_fin_dia,
                'hora_inicio': hora_inicio_fmt,
                'hora_fin': hora_fin_fmt,
                'fecha_res': fecha_res_fmt,
                'nombre_recinto': cancha.nombre,
                'fecha_carta': fecha_carta,
                'precio_original': precio_original,
                'precio_descuento': precio_descuento,
            },
        })

    # ── PASO 2 → CONFIRMAR + CREAR + FIRMAR ──
    with transaction.atomic():
        if BloqueoHorario.objects.select_for_update().filter(cancha=cancha, fecha=fecha, hora=hora_inicio).exists():
            return render(request, 'panel/crear_reserva_organizacion.html', _ctx_base({
                'form_data': request.POST,
                'errores': ['Ese horario está bloqueado. Elige otro.'],
            }))
        if Reserva.objects.select_for_update().filter(
            cancha=cancha, fecha=fecha,
            hora_inicio__lt=hora_fin, hora_fin__gt=hora_inicio,
            estado__in=ESTADOS_OCUPAN_SLOT
        ).exists():
            return render(request, 'panel/crear_reserva_organizacion.html', _ctx_base({
                'form_data': request.POST,
                'errores': ['Ese horario (o parte del rango) ya está reservado.'],
            }))

        # Org user crea su propia reserva: se vincula a su cuenta y firma la carta después
        es_autogestion = _es_org_user and not _es_admin
        reserva = Reserva.objects.create(
            cancha=cancha,
            solicitante=request.user if es_autogestion else None,
            solicitante_nombre=rep_nombre,
            solicitante_email=rep_email,
            fecha=fecha,
            hora_inicio=hora_inicio,
            hora_fin=hora_fin,
            motivo=motivo,
            estado='PENDIENTE_ALCALDE',
            metodo_pago='TARJETA',
            tipo_reserva='ORGANIZACION',
            nombre_organizacion=nombre_org,
            representante_nombre=rep_nombre,
            representante_rut=rep_rut_norm,
            representante_telefono=rep_telefono,
            representante_email=rep_email,
            precio_original=precio_original,
            precio_descuento=precio_descuento,
            gestionado_por=None if es_autogestion else request.user,
            firma_org_en=None if es_autogestion else timezone.now(),
        )

    registrar_accion(
        request.user,
        "Creó reserva de organización",
        f"{cancha.nombre} {fecha.strftime('%d/%m/%Y')} {hora_inicio.strftime('%H:%M')}–{hora_fin.strftime('%H:%M')} "
        f"— Org: {nombre_org} — Pendiente firma alcalde",
    )

    _fecha_fmt = f"{fecha.strftime('%d/%m/%Y')} {hora_inicio.strftime('%H:%M')}–{hora_fin.strftime('%H:%M')}"
    notificar_admins(
        'RESERVA_PENDIENTE',
        f'Nueva solicitud: {nombre_org}',
        f'{nombre_org} solicitó {cancha.nombre} el {_fecha_fmt}. Pendiente de firma para continuar el flujo.',
        reserva=reserva,
    )
    if es_autogestion:
        crear_notificacion_reserva(
            request.user,
            'RESERVA_PENDIENTE',
            'Solicitud de reserva creada',
            f'Tu solicitud para {cancha.nombre} el {_fecha_fmt} fue creada. Firma la carta para que el alcalde pueda aprobarla.',
            reserva=reserva,
        )
    return redirect('carta_solicitud_recinto', reserva_id=reserva.pk)


@login_required
def finalizar_reserva_cancha(request):
    if request.method == 'POST':
        try:
            if request.content_type and 'application/json' in request.content_type:
                data = json.loads(request.body)
                cancha_id = data.get('cancha_id')
                fecha_str = data.get('fecha')
                hora_str = data.get('hora')
                metodo_pago = data.get('metodo_pago')  # 'TARJETA' o 'TRANSFERENCIA'
                comprobante = None
                admin_nombre = data.get('admin_solicitante_nombre')
                admin_email = data.get('admin_solicitante_email')
            else:
                cancha_id = request.POST.get('cancha_id')
                fecha_str = request.POST.get('fecha')
                hora_str = request.POST.get('hora')
                metodo_pago = request.POST.get('metodo_pago')
                comprobante = request.FILES.get('comprobante_transferencia')
                admin_nombre = request.POST.get('admin_solicitante_nombre')
                admin_email = request.POST.get('admin_solicitante_email')

            admin_override = bool(
                request.user.is_superuser and admin_nombre and admin_nombre.strip()
            )
            
            cancha = get_object_or_404(ConfigCancha, id=cancha_id)
            fecha = datetime.strptime(fecha_str, "%Y-%m-%d").date()
            hora_inicio = datetime.strptime(hora_str, "%H:%M").time()

            # Validar día de semana
            dias_disponibles = cancha.dias_semana_list()
            if fecha.weekday() not in dias_disponibles:
                nombres_dias = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']
                disponibles_str = ', '.join(nombres_dias[d] for d in sorted(dias_disponibles))
                return JsonResponse({
                    'status': 'error',
                    'message': f'Este recinto no está disponible ese día. Días habilitados: {disponibles_str}.',
                }, status=400)

            # Validar que la hora no caiga en la pausa
            if cancha.pausa_inicio and cancha.pausa_fin:
                if cancha.pausa_inicio <= hora_inicio < cancha.pausa_fin:
                    return JsonResponse({
                        'status': 'error',
                        'message': (
                            f'El recinto no está disponible entre las '
                            f'{cancha.pausa_inicio.strftime("%H:%M")} y las '
                            f'{cancha.pausa_fin.strftime("%H:%M")}.'
                        ),
                    }, status=400)

            # Validación de 4 horas de anticipación para el mismo día (admin queda exento)
            if not admin_override:
                ahora = timezone.now()
                inicio_solicitado = timezone.make_aware(datetime.combine(fecha, hora_inicio))
                if inicio_solicitado < ahora + timedelta(hours=4):
                    return JsonResponse({
                        'status': 'error',
                        'message': 'Las reservas para el mismo día deben realizarse con al menos 4 horas de anticipación.'
                    }, status=400)

            minutos = cancha.intervalo_minutos
            dt_inicio = datetime.combine(fecha, hora_inicio)
            dt_fin = dt_inicio + timedelta(minutes=minutos)
            hora_fin = dt_fin.time()

            metodo_pago = (metodo_pago or '').upper()

            if metodo_pago not in ('TARJETA', 'TRANSFERENCIA', 'CUPON'):
                return JsonResponse({'status': 'error', 'message': 'Método de pago inválido.'}, status=400)

            # Validar cupón antes de seguir
            cupon_obj = None
            if metodo_pago == 'CUPON' and admin_override:
                return JsonResponse({
                    'status': 'error',
                    'message': 'Como administrador no puedes usar cupones en reservas creadas a nombre de otra persona.',
                }, status=400)
            if metodo_pago == 'CUPON':
                cupon_codigo = request.POST.get('cupon_codigo', '').strip().upper()
                if not cupon_codigo:
                    return JsonResponse({'status': 'error', 'message': 'Debes ingresar el código del cupón.'}, status=400)
                try:
                    cupon_obj = Cupon.objects.get(codigo=cupon_codigo, usuario=request.user)
                except Cupon.DoesNotExist:
                    return JsonResponse({'status': 'error', 'message': 'Cupón no válido o no te pertenece.'}, status=400)
                if not cupon_obj.esta_vigente():
                    return JsonResponse({'status': 'error', 'message': 'El cupón está expirado o ya fue usado.'}, status=400)
                # Verificar que el cupón corresponde a esta cancha
                if cupon_obj.nombre_recurso and cupon_obj.nombre_recurso != cancha.nombre:
                    return JsonResponse({
                        'status': 'error',
                        'message': f'Este cupón solo es válido para {cupon_obj.nombre_recurso}.',
                    }, status=400)

            if metodo_pago == 'CUPON':
                estado = 'APROBADA'
            elif metodo_pago == 'TARJETA':
                estado = 'APROBADA'
            else:
                estado = 'PENDIENTE'

            if metodo_pago == 'TRANSFERENCIA' and not comprobante:
                return JsonResponse({'status': 'error', 'message': 'Debes adjuntar un comprobante de transferencia.'}, status=400)

            if metodo_pago == 'TRANSFERENCIA' and comprobante:
                if comprobante.size > 5 * 1024 * 1024:
                    return JsonResponse({
                        'status': 'error',
                        'message': 'El archivo no puede superar los 5 MB.'
                    }, status=400)

                content_type = (getattr(comprobante, 'content_type', '') or '').lower()
                if not content_type.startswith('image/'):
                    return JsonResponse({
                        'status': 'error',
                        'message': 'Solo se permiten imágenes como comprobante.'
                    }, status=400)

                try:
                    comprobante.seek(0)
                    imagen = Image.open(comprobante)
                    imagen.verify()
                    comprobante.seek(0)
                except (UnidentifiedImageError, OSError, ValueError):
                    return JsonResponse({
                        'status': 'error',
                        'message': 'El archivo subido no es una imagen válida.'
                    }, status=400)
            
            # Verificar disponibilidad y crear dentro de una transacción atómica
            # para evitar double-booking por concurrencia
            with transaction.atomic():
                existe_bloqueo = BloqueoHorario.objects.select_for_update().filter(
                    cancha=cancha, fecha=fecha, hora=hora_inicio
                ).exists()
                existe_reserva = Reserva.objects.select_for_update().filter(
                    cancha=cancha,
                    fecha=fecha,
                    hora_inicio__lt=hora_fin,
                    hora_fin__gt=hora_inicio,
                    estado__in=ESTADOS_OCUPAN_SLOT,
                ).exists()

                if existe_bloqueo or existe_reserva:
                    return JsonResponse({'status': 'error', 'message': 'El horario ya no está disponible.'}, status=400)

                # Detectar si el solicitante es perfil de organización
                perfil_solicitante = getattr(request.user, 'perfil', None) if not admin_override else None
                es_org_user = bool(perfil_solicitante and perfil_solicitante.es_organizacion)

                reserva = Reserva.objects.create(
                    cancha=cancha,
                    solicitante=None if admin_override else request.user,
                    solicitante_nombre=admin_nombre.strip() if admin_override else '',
                    solicitante_email=(admin_email or '').strip() if admin_override else '',
                    fecha=fecha,
                    hora_inicio=hora_inicio,
                    hora_fin=hora_fin,
                    estado=estado,
                    metodo_pago=metodo_pago,
                    comprobante_transferencia=comprobante if metodo_pago == 'TRANSFERENCIA' else None,
                    gestionado_por=request.user if admin_override else None,
                    tipo_reserva='ORGANIZACION' if es_org_user else 'PERSONAL',
                    nombre_organizacion=perfil_solicitante.nombre_organizacion if es_org_user else '',
                    representante_nombre=request.user.get_full_name() if es_org_user else '',
                    representante_rut=perfil_solicitante.rut or '' if es_org_user else '',
                    representante_telefono=perfil_solicitante.telefono or '' if es_org_user else '',
                    representante_email=request.user.email if es_org_user else '',
                    precio_original=cancha.precio if es_org_user else None,
                    precio_descuento=cancha.precio // 2 if es_org_user and cancha.precio else None,
                )

                # Marcar cupón como usado dentro de la misma transacción
                if cupon_obj:
                    cupon_obj.estado = 'USADO'
                    cupon_obj.reserva_uso = reserva
                    cupon_obj.save(update_fields=['estado', 'reserva_uso'])
                    Notificacion.objects.create(
                        user=request.user,
                        tipo='CUPON_USADO',
                        titulo='Cupón aplicado',
                        mensaje=f'Usaste el cupón {cupon_obj.codigo} para tu reserva en {cancha.nombre} el {fecha.strftime("%d/%m/%Y")}.',
                    )

                if reserva.estado == 'APROBADA':
                    RegistroTesoreria.objects.get_or_create(reserva=reserva)

            if not admin_override:
                if reserva.estado == 'PENDIENTE':
                    crear_notificacion_reserva(
                        request.user,
                        'RESERVA_PENDIENTE',
                        'Reserva enviada',
                        f'Tu reserva para {cancha.nombre} del {fecha.strftime("%d/%m/%Y")} quedó pendiente de aprobación.',
                        reserva,
                    )
                elif reserva.estado == 'APROBADA':
                    crear_notificacion_reserva(
                        request.user,
                        'RESERVA_APROBADA',
                        'Reserva realizada con éxito',
                        f'Tu reserva para {cancha.nombre} del {fecha.strftime("%d/%m/%Y")} ha sido confirmada.',
                        reserva,
                    )
            else:
                registrar_accion(
                    request.user,
                    "Creó reserva manualmente (flujo de pago)",
                    f"{cancha.nombre} {fecha.strftime('%d/%m/%Y')} {hora_inicio.strftime('%H:%M')} "
                    f"para {admin_nombre.strip()} — método: {metodo_pago}, estado: {estado}",
                )
            
            # Correo de confirmación (basado en la lógica existente)
            correo_comprobante_a_solicitante(reserva)

            # Notificación in-app a administradores
            admins = User.objects.filter(is_staff=True)
            txt_estado = "pendiente de aprobación" if reserva.estado == 'PENDIENTE' else "confirmada (pago tarjeta)"
            for admin in admins:
                Notificacion.objects.create(
                    user=admin,
                    tipo='RESERVA_PENDIENTE' if reserva.estado == 'PENDIENTE' else 'RESERVA_APROBADA',
                    titulo='Nueva reserva de cancha',
                    mensaje=f'{reserva.get_solicitante_display()} ha solicitado {cancha.nombre} para el {reserva.fecha.strftime("%d/%m/%Y")} ({reserva.hora_inicio.strftime("%H:%M")}). Estado: {txt_estado}.',
                    reserva=reserva,
                )
            
            return JsonResponse({
                'status': 'success', 
                'reserva_id': reserva.id,
                'mensaje': 'Reserva creada con éxito'
            })
            
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
        except Exception:
            logger.exception('Error inesperado en finalizar_reserva_cancha')
            return JsonResponse({'status': 'error', 'message': 'Error interno al procesar la reserva.'}, status=500)

    return JsonResponse({'status': 'error', 'message': 'Solo POST permitido'}, status=405)

# Alias para compatibilidad si fuera necesario (aunque se recomienda usar finalizar_reserva_cancha)
finalizar_reserva_tenis = finalizar_reserva_cancha
finalizar_reserva_gimnasio = finalizar_reserva_cancha
finalizar_reserva_futbolito = finalizar_reserva_cancha
finalizar_reserva_estadio = finalizar_reserva_cancha



def registro(request):
    if request.user.is_authenticated:
        return redirect('inicio')

    if request.method == 'POST':
        if _rate_limit_exceeded(request, 'registro', limit=5, period=3600):
            return render(request, 'registro.html', {
                'dias': range(1, 32),
                'anios': range(2010, 1919, -1),
                'errores': {'general': 'Demasiados registros desde esta dirección. Intenta más tarde.'},
            })

        nombre = request.POST.get('nombre', '').strip()
        apellido = request.POST.get('apellido', '').strip()
        email = request.POST.get('email', '').strip().lower()
        telefono = request.POST.get('telefono', '').strip()
        rut_ingresado = request.POST.get('rut', '').strip()
        rut = normalizar_rut(rut_ingresado)
        dia = request.POST.get('dia')
        mes = request.POST.get('mes')
        anio = request.POST.get('anio')
        clave = request.POST.get('contraseña', '')
        clave2 = request.POST.get('contraseña2', '')

        errores = {}

        if not rut_valido(rut):
            errores['rut'] = 'Ingresa un RUT válido.'
        if User.objects.filter(email=email).exists():
            errores['email'] = 'Ya existe una cuenta con ese correo.'
        if rut_valido(rut) and UserProfile.objects.filter(rut=rut).exists():
            errores['rut'] = 'Ya existe una cuenta con ese RUT.'
        if clave != clave2:
            errores['clave'] = 'Las contraseñas no coinciden.'
        if len(clave) < 8:
            errores['clave'] = 'La contraseña debe tener al menos 8 caracteres.'

        if not errores:
            user = User.objects.create_user(
                username=email,
                email=email,
                password=clave,
                first_name=nombre,
                last_name=apellido,
            )
            fecha_nac = None
            try:
                fecha_nac = date(int(anio), int(mes), int(dia))
            except (ValueError, TypeError):
                pass

            UserProfile.objects.create(
                user=user,
                telefono=telefono,
                rut=rut,
                fecha_nacimiento=fecha_nac,
            )
            login(request, user)
            
            # Notificación in-app a administradores
            admins = User.objects.filter(is_staff=True)
            for admin in admins:
                Notificacion.objects.create(
                    user=admin,
                    tipo='PERFIL_ACTUALIZADO', # Reutilizamos tipo o uno genérico
                    titulo='Nuevo usuario registrado',
                    mensaje=f'El usuario {nombre} {apellido} ({email}) se ha registrado en el sistema.',
                )

            messages.success(request, f'¡Bienvenido/a, {nombre}!')
            return redirect('inicio')

        return render(request, 'registro.html', {
            'dias': range(1, 32),
            'anios': range(2010, 1919, -1),
            'errores': errores,
            'datos': {
                **request.POST.dict(),
                'rut': rut or rut_ingresado.replace('.', '').upper(),
            },
        })

    return render(request, 'registro.html', {
        'dias': range(1, 32),
        'anios': range(2010, 1919, -1),
    })


def login_view(request):
    if request.user.is_authenticated:
        return redirect('inicio')

    next_url = request.GET.get('next', '') or request.POST.get('next', '')
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts=request.get_host(), require_https=request.is_secure()):
        next_url = ''

    if request.method == 'POST':
        if _rate_limit_exceeded(request, 'login', limit=10, period=300):
            return render(request, 'login.html', {
                'error': 'Demasiados intentos. Espera 5 minutos e intenta de nuevo.',
                'next': next_url,
            })

        email = request.POST.get('email', '').strip().lower()
        clave = request.POST.get('clave', '')

        user = authenticate(request, username=email, password=clave)
        if user is not None:
            login(request, user)
            if next_url:
                return redirect(next_url)
            if user.is_superuser or user.is_staff:
                return redirect('panel_inicio')
            perfil = getattr(user, 'perfil', None)
            if perfil and perfil.es_tesorero:
                return redirect('perfil_tesorero')
            if perfil and perfil.es_alcalde:
                return redirect('perfil_alcalde')
            return redirect('inicio')
        else:
            try:
                usuario_bloqueado = User.objects.get(email=email)
                if not usuario_bloqueado.is_active and usuario_bloqueado.check_password(clave):
                    return render(request, 'login.html', {
                        'error': 'Tu cuenta fue deshabilitada temporalmente.',
                        'next': next_url,
                    })
            except User.DoesNotExist:
                pass
            return render(request, 'login.html', {'error': 'Correo o contraseña incorrectos.', 'next': next_url})

    return render(request, 'login.html', {'next': next_url})


def logout_view(request):
    if request.method != 'POST':
        return redirect('inicio')
    logout(request)
    return redirect('inicio')


def recuperar_clave(request):
    if request.method == 'POST':
        if _rate_limit_exceeded(request, 'recuperar_clave', limit=5, period=900):
            return render(request, 'recuperar_clave.html', {'enviado': True})

        metodo = request.POST.get('metodo', '')
        user = None

        if metodo == 'email':
            email = request.POST.get('email', '').strip().lower()
            try:
                user = User.objects.get(email=email)
            except User.DoesNotExist:
                pass

        elif metodo == 'rut':
            rut = request.POST.get('rut', '').strip()
            try:
                user = UserProfile.objects.get(rut=rut).user
            except UserProfile.DoesNotExist:
                pass

        if user is not None:
            token = default_token_generator.make_token(user)
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            reset_url = request.build_absolute_uri(
                reverse('resetear_clave', kwargs={'uidb64': uid, 'token': token})
            )
            correo_recuperacion_clave(user, reset_url)

        return render(request, 'recuperar_clave.html', {'enviado': True})

    return render(request, 'recuperar_clave.html')


def resetear_clave(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is None or not default_token_generator.check_token(user, token):
        return render(request, 'resetear_clave.html', {'error_token': True})

    if request.method == 'POST':
        clave = request.POST.get('clave', '')
        clave2 = request.POST.get('clave2', '')

        if len(clave) < 8:
            return render(request, 'resetear_clave.html', {
                'error': 'La contraseña debe tener al menos 8 caracteres.',
                'uidb64': uidb64, 'token': token,
            })
        if clave != clave2:
            return render(request, 'resetear_clave.html', {
                'error': 'Las contraseñas no coinciden.',
                'uidb64': uidb64, 'token': token,
            })

        user.set_password(clave)
        user.save()
        return render(request, 'resetear_clave.html', {'exito': True})

    return render(request, 'resetear_clave.html', {'uidb64': uidb64, 'token': token})


# ─── Vistas de Reservas ──────────────────────────────────────────────────────

@login_required
def crear_reserva(request, salon_id):
    """
    Crea una nueva reserva para el salón indicado.
    """
    salon = get_object_or_404(Salon, pk=salon_id, activo=True)

    # Datos para restricciones en el HTML
    hoy_val = date.today().strftime('%Y-%m-%d')
    limite_val = (date.today() + timedelta(days=31)).strftime('%Y-%m-%d')
    contexto = {
        'salon': salon,
        'hoy': hoy_val,
        'limite_futuro': limite_val
    }

    if request.method == 'POST':
        fecha = request.POST.get('fecha', '')
        hora_inicio = request.POST.get('hora_inicio', '')
        hora_fin = request.POST.get('hora_fin', '')
        motivo = request.POST.get('motivo', '').strip()

        if not all([fecha, hora_inicio, hora_fin, motivo]):
            messages.error(request, 'Completa todos los campos obligatorios.')
            return render(request, 'reservas/crear_reserva.html', contexto)

        es_admin = request.user.is_staff or request.user.is_superuser

        # Validación 4 horas mismo día ANTES de crear la reserva
        if not es_admin:
            ahora = timezone.now()
            f_obj = datetime.strptime(fecha, "%Y-%m-%d").date()
            h_obj = datetime.strptime(hora_inicio, "%H:%M").time()
            inicio_sol = timezone.make_aware(datetime.combine(f_obj, h_obj))
            if inicio_sol < ahora + timedelta(hours=4):
                messages.error(request, 'Las reservas para el mismo día deben hacerse con al menos 4 horas de anticipación.')
                return render(request, 'reservas/crear_reserva.html', contexto)

        estado_inicial = 'APROBADA' if es_admin else 'PENDIENTE'

        reserva = Reserva.objects.create(
            salon=salon,
            solicitante=request.user,
            fecha=fecha,
            hora_inicio=hora_inicio,
            hora_fin=hora_fin,
            motivo=motivo,
            estado=estado_inicial,
            gestionado_por=request.user if es_admin else None,
        )

        if reserva.estado == 'PENDIENTE':
            crear_notificacion_reserva(
                request.user,
                'RESERVA_PENDIENTE',
                'Reserva enviada',
                f'Tu reserva para {salon.nombre} del {reserva.fecha.strftime("%d/%m/%Y")} quedó pendiente de aprobación.',
                reserva,
            )
        elif reserva.estado == 'APROBADA':
            crear_notificacion_reserva(
                request.user,
                'RESERVA_APROBADA',
                'Reserva realizada con éxito',
                f'Tu reserva para {salon.nombre} del {reserva.fecha.strftime("%d/%m/%Y")} ha sido confirmada.',
                reserva,
            )
            RegistroTesoreria.objects.get_or_create(reserva=reserva)

        # Correo B: comprobante al solicitante (siempre)
        correo_comprobante_a_solicitante(reserva)

        if reserva.estado == 'PENDIENTE':
            # Correo A: notificar a admins del salón
            correo_nueva_solicitud_a_admins(reserva)

        # Notificación in-app a administradores
        admins = User.objects.filter(is_staff=True)
        txt_estado = "pendiente de aprobación" if reserva.estado == 'PENDIENTE' else "confirmada (admin/staff)"
        for admin in admins:
            Notificacion.objects.create(
                user=admin,
                tipo='RESERVA_PENDIENTE' if reserva.estado == 'PENDIENTE' else 'RESERVA_APROBADA',
                titulo='Nueva reserva de salón',
                mensaje=f'{reserva.get_solicitante_display()} ha reservado el salón {salon.nombre} para el {reserva.fecha.strftime("%d/%m/%Y")}. Estado: {txt_estado}.',
                reserva=reserva,
            )

        messages.success(request, f'Reserva creada con éxito.')
        return redirect('mis_reservas')

    return render(request, 'reservas/crear_reserva.html', contexto)


@login_required
def mis_reservas(request):
    """Listado del historial de reservas del usuario con filtros y orden."""
    estado_filtro = request.GET.get('estado', '')
    qs = Reserva.objects.filter(solicitante=request.user).exclude(estado='ELIMINADA').select_related('salon', 'cancha').order_by('fecha', 'hora_inicio')

    if estado_filtro:
        reservas_lista = list(qs.filter(estado=estado_filtro))
    else:
        reservas_lista = list(qs.all())

    # Conteos para filtros
    conteos = {
        'PENDIENTE': qs.filter(estado='PENDIENTE').count(),
        'APROBADA': qs.filter(estado='APROBADA').count(),
        'RECHAZADA': qs.filter(estado='RECHAZADA').count(),
        'CANCELADA': qs.filter(estado='CANCELADA').count(),
    }

    from django.core.paginator import Paginator
    paginator = Paginator(reservas_lista, 5)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Inyectar si es editable / cancelable solo a los de la página actual
    ahora = timezone.now()
    limite_edicion  = ahora + timedelta(hours=4)   # mínimo absoluto para editar
    limite_cancelar = ahora + timedelta(hours=12)  # mínimo para cancelar

    for r in page_obj:
        dt_naive = datetime.combine(r.fecha, r.hora_inicio)
        inicio_reserva = timezone.make_aware(dt_naive)
        if r.tipo_reserva == 'ORGANIZACION':
            r.es_editable = (r.firma_alcalde_carta_en is None and r.estado not in ('RECHAZADA', 'CANCELADA'))
        else:
            r.es_editable = (r.estado == 'APROBADA' and inicio_reserva > limite_edicion)
        
        if request.user.is_staff or request.user.is_superuser:
            r.es_cancelable = (r.estado in ('PENDIENTE', 'APROBADA'))
        else:
            r.es_cancelable = (r.estado in ('PENDIENTE', 'APROBADA') and inicio_reserva > limite_cancelar)

    return render(request, 'reservas/mis_reservas.html', {
        'reservas': page_obj,
        'estado_filtro': estado_filtro,
        'conteos': conteos,
        'total': qs.count()
    })


@login_required
def editar_reserva_organizacion(request, reserva_id):
    """Edición de reserva de organización: resetea firmas y redirige a carta."""
    reserva = get_object_or_404(Reserva, pk=reserva_id, tipo_reserva='ORGANIZACION')

    _perfil   = getattr(request.user, 'perfil', None)
    _es_admin = request.user.is_staff or request.user.is_superuser
    _es_prop  = reserva.solicitante == request.user
    _es_org   = bool(_perfil and _perfil.es_organizacion and _es_prop)

    if not (_es_admin or _es_prop):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    if reserva.firma_alcalde_carta_en:
        messages.error(request, 'La reserva ya fue firmada por el alcalde y no puede editarse.')
        return redirect('mis_reservas')

    cancha = reserva.cancha

    if request.method == 'POST':
        nombre_org     = request.POST.get('nombre_organizacion', '').strip()
        rep_nombre     = request.POST.get('representante_nombre', '').strip()
        rep_rut        = request.POST.get('representante_rut', '').strip()
        rep_telefono   = request.POST.get('representante_telefono', '').strip()
        rep_email      = request.POST.get('representante_email', '').strip()
        motivo         = request.POST.get('motivo', '').strip()
        modo_fin_dia   = request.POST.get('modo_fin_dia') == '1'
        hora_fin_manual= request.POST.get('hora_fin_manual', '').strip()

        errores = []
        if not nombre_org:
            errores.append('El nombre de la organización es requerido.')
        if not rep_nombre:
            errores.append('El nombre del representante es requerido.')
        if not rep_email:
            errores.append('El correo del representante es requerido.')

        rep_rut_norm = normalizar_rut(rep_rut) if rep_rut else ''
        if rep_rut and not rut_valido(rep_rut_norm):
            errores.append('El RUT del representante no es válido.')

        if errores:
            return render(request, 'panel/crear_reserva_organizacion.html', {
                'cancha': cancha, 'fecha': reserva.fecha.strftime('%Y-%m-%d'),
                'hora': reserva.hora_inicio.strftime('%H:%M'),
                'precio_cancha': cancha.precio if cancha else 0,
                'precio_descuento': (cancha.precio // 2) if cancha else 0,
                'orgs_existentes': [], 'step': 'form',
                'es_org_user': _es_org and not _es_admin,
                'editando': True, 'reserva_pk': reserva.pk,
                'prefill': request.POST, 'form_data': request.POST,
                'errores': errores,
            })

        # Calcular nueva hora_fin
        if modo_fin_dia and cancha:
            hora_fin = cancha.hora_fin
        elif hora_fin_manual:
            try:
                hora_fin = datetime.strptime(hora_fin_manual, '%H:%M').time()
            except ValueError:
                hora_fin = reserva.hora_fin
        else:
            hora_fin = reserva.hora_fin

        # Actualizar reserva y borrar firmas
        reserva.nombre_organizacion      = nombre_org
        reserva.representante_nombre     = rep_nombre
        reserva.representante_rut        = rep_rut_norm
        reserva.representante_telefono   = rep_telefono
        reserva.representante_email      = rep_email
        reserva.motivo                   = motivo
        reserva.hora_fin                 = hora_fin
        reserva.firma_org_en             = None
        reserva.firma_alcalde_carta_en   = None
        reserva.firmas_carta             = None
        reserva.save()

        registrar_accion(request.user, 'Editó reserva de organización',
                         f'Reserva #{reserva.pk} — firmas reiniciadas')
        _fecha_fmt = f'{reserva.fecha.strftime("%d/%m/%Y")} {reserva.hora_inicio.strftime("%H:%M")}–{reserva.hora_fin.strftime("%H:%M")}'
        notificar_admins(
            'RESERVA_PENDIENTE',
            f'Solicitud editada: {reserva.nombre_organizacion}',
            f'{reserva.nombre_organizacion} editó su solicitud para '
            f'{reserva.cancha.nombre if reserva.cancha else "el recinto"} el {_fecha_fmt}. '
            f'Las firmas fueron reiniciadas.',
            reserva=reserva,
        )
        notificar_alcaldes(
            'RESERVA_PENDIENTE',
            f'Solicitud editada: {reserva.nombre_organizacion}',
            f'{reserva.nombre_organizacion} modificó su solicitud para '
            f'{reserva.cancha.nombre if reserva.cancha else "el recinto"} el {_fecha_fmt}. '
            f'Deberá firmarse nuevamente.',
            reserva=reserva,
        )
        return redirect('carta_solicitud_recinto', reserva_id=reserva.pk)

    # GET — form pre-llenado con datos actuales
    prefill = {
        'nombre_organizacion':   reserva.nombre_organizacion,
        'representante_nombre':  reserva.representante_nombre,
        'representante_rut':     reserva.representante_rut,
        'representante_telefono':reserva.representante_telefono,
        'representante_email':   reserva.representante_email,
        'motivo':                reserva.motivo,
    }
    return render(request, 'panel/crear_reserva_organizacion.html', {
        'cancha': cancha, 'fecha': reserva.fecha.strftime('%Y-%m-%d'),
        'hora': reserva.hora_inicio.strftime('%H:%M'),
        'precio_cancha': cancha.precio if cancha else 0,
        'precio_descuento': (cancha.precio // 2) if cancha else 0,
        'orgs_existentes': [], 'step': 'form',
        'es_org_user': _es_org and not _es_admin,
        'editando': True, 'reserva_pk': reserva.pk,
        'prefill': prefill, 'form_data': prefill,
    })


@login_required
def editar_mi_reserva(request, reserva_id):
    """
    El solicitante edita su reserva APROBADA (según reglas de 12 horas).
    """
    reserva = get_object_or_404(Reserva, pk=reserva_id, solicitante=request.user)
    
    # Restricción Dinámica: 12h para adelantar, 4h para posponer
    ahora = timezone.now()
    inicio_original = timezone.make_aware(datetime.combine(reserva.fecha, reserva.hora_inicio))
    
    if reserva.estado != 'APROBADA':
        messages.error(request, 'Solo puedes editar reservas que ya estén aprobadas.')
        return redirect('mis_reservas')
        
    if inicio_original <= ahora + timedelta(hours=4):
        messages.error(request, 'No puedes editar la reserva con menos de 4 horas de anticipación.')
        return redirect('mis_reservas')

    if request.method == 'POST':
        fecha_str = request.POST.get('fecha', '')
        h_ini_str = request.POST.get('hora_inicio', '')
        h_fin_str = request.POST.get('hora_fin', '')
        
        try:
            f = datetime.strptime(fecha_str, "%Y-%m-%d").date()
            h_ini = datetime.strptime(h_ini_str, "%H:%M").time()
            h_fin = datetime.strptime(h_fin_str, "%H:%M").time()
            
            # Regla Dinámica de Anticipación
            inicio_nuevo = timezone.make_aware(datetime.combine(f, h_ini))
            
            if inicio_nuevo < inicio_original:
                # Caso: Adelantar la reserva
                if ahora + timedelta(hours=12) > inicio_original:
                    raise Exception('Para adelantar una reserva debes hacerlo con al menos 12 horas de anticipación a la hora original.')
            else:
                # Caso: Posponer la reserva
                if ahora + timedelta(hours=4) > inicio_original:
                    raise Exception('Para modificar una reserva debes hacerlo con al menos 4 horas de anticipación a la hora original.')

            # NUEVO: Validar que el nuevo horario solicitado cumpla el mínimo de 4h desde AHORA
            if inicio_nuevo < ahora + timedelta(hours=4):
                raise Exception('El nuevo horario seleccionado debe tener al menos 4 horas de anticipación desde el momento actual.')

            # Validar que el nuevo horario no sea idéntico al actual
            if f == reserva.fecha and h_ini == reserva.hora_inicio and h_fin == reserva.hora_fin:
                raise Exception('El horario seleccionado es exactamente el mismo que ya tienes. Por favor elige una fecha u hora diferente.')

            # Validaciones sistema
            dias_bloqueados = 0
            if reserva.cancha:
                for p in BloqueoPeriodo.objects.filter(cancha=reserva.cancha, fecha_fin__gte=date.today()):
                    inicio_p = max(p.fecha_inicio, date.today())
                    dias_bloqueados += (p.fecha_fin - inicio_p).days + 1
            limite_futuro = date.today() + timedelta(days=31 + dias_bloqueados)
            if f > limite_futuro:
                raise Exception('Solo se permiten reservas hasta 31 días a futuro.')
            
            if reserva.cancha:
                hora_min = reserva.cancha.hora_inicio
                hora_max = reserva.cancha.hora_fin
            else:
                hora_min, hora_max = time(9, 0), time(23, 0)
            if h_ini < hora_min or h_ini > hora_max:
                raise Exception(f'Horario permitido: {hora_min.strftime("%H:%M")} a {hora_max.strftime("%H:%M")}.')

            dt_ini = datetime.combine(f, h_ini)
            dt_fin = datetime.combine(f, h_fin)
            if dt_fin <= dt_ini:   # paso por medianoche (23:00 → 00:00)
                dt_fin += timedelta(days=1)
            diff = dt_fin - dt_ini
            if diff != timedelta(hours=1):
                raise Exception('La edición debe mantener bloques de 1 hora.')

            # Validar que la nueva fecha no esté en un período bloqueado
            if reserva.cancha_id:
                if BloqueoPeriodo.objects.filter(cancha_id=reserva.cancha_id, fecha_inicio__lte=f, fecha_fin__gte=f).exists():
                    raise Exception('La fecha seleccionada está dentro de un período bloqueado por el administrador. Por favor elige otra fecha disponible.')

            # Validar que la hora no esté bloqueada manualmente
            if reserva.cancha_id:
                if BloqueoHorario.objects.filter(cancha_id=reserva.cancha_id, fecha=f, hora=h_ini).exists():
                    raise Exception('El horario seleccionado está bloqueado por el administrador. Por favor elige otro horario disponible.')

            # Disponibilidad: verificar que no choque con otra reserva del mismo recurso
            choca_query = Reserva.objects.filter(fecha=f, estado__in=ESTADOS_OCUPAN_SLOT).exclude(pk=reserva.pk)
            if reserva.salon:
                choca_query = choca_query.filter(salon=reserva.salon)
            else:
                choca_query = choca_query.filter(cancha=reserva.cancha)
                
            if choca_query.filter(hora_inicio__lt=h_fin, hora_fin__gt=h_ini).exists():
                raise Exception('El horario seleccionado ya está ocupado por otra reserva. Por favor elige otro horario disponible.')

            # Soportar auditoría: Guardar valores previos antes del cambio
            reserva.fecha_anterior = reserva.fecha
            reserva.hora_inicio_anterior = reserva.hora_inicio
            reserva.hora_fin_anterior = reserva.hora_fin

            reserva.fecha = f
            reserva.hora_inicio = h_ini
            reserva.hora_fin = h_fin
            
            # Actualizar otros campos (Motivo)
            reserva.motivo = request.POST.get('motivo', '').strip()

            reserva.estado = 'PENDIENTE' # Volver a pendiente para re-validación
            reserva.save()

            crear_notificacion_reserva(
                request.user,
                'RESERVA_PENDIENTE',
                'Cambios en reserva enviados',
                f'Has modificado tu reserva para {(reserva.salon.nombre if reserva.salon else reserva.cancha.nombre)}. Ha vuelto a estado pendiente para su validación.',
                reserva,
            )

            # Notificación in-app a administradores
            admins = User.objects.filter(is_staff=True)
            recurso = reserva.salon.nombre if reserva.salon else reserva.cancha.nombre
            for admin in admins:
                Notificacion.objects.create(
                    user=admin,
                    tipo='RESERVA_PENDIENTE',
                    titulo='Reserva modificada',
                    mensaje=f'{reserva.get_solicitante_display()} ha modificado su reserva de {recurso} al {reserva.fecha.strftime("%d/%m/%Y")}. Requiere revisión.',
                    reserva=reserva,
                )

            messages.success(request, 'Cambios guardados. Su reserva volverá a ser evaluada.')
            return redirect('mis_reservas')
        except Exception as e:
            messages.error(request, f'Error: {str(e)}')
            
    # Datos para restricciones en el HTML
    hoy = date.today()
    hoy_val = hoy.strftime('%Y-%m-%d')
    dias_bloqueados = 0
    periodos_bloqueados = []
    if reserva.cancha_id:
        for p in BloqueoPeriodo.objects.filter(cancha_id=reserva.cancha_id, fecha_fin__gte=hoy):
            inicio_p = max(p.fecha_inicio, hoy)
            dias_bloqueados += (p.fecha_fin - inicio_p).days + 1
            periodos_bloqueados.append({
                'inicio': p.fecha_inicio.strftime('%Y-%m-%d'),
                'fin': p.fecha_fin.strftime('%Y-%m-%d'),
            })
    limite_val = (hoy + timedelta(days=31 + dias_bloqueados)).strftime('%Y-%m-%d')

    return render(request, 'reservas/editar_reserva.html', {
        'reserva': reserva,
        'hoy': hoy_val,
        'limite_futuro': limite_val,
        'cancha_id': reserva.cancha_id or '',
        'reserva_id': reserva.pk,
    })


@login_required
def cancelar_mi_reserva(request, reserva_id):
    """
    El solicitante cancela su propia reserva (mínimo 12 h de anticipación).
    No requiere aprobación. Notifica a los admins por correo y por notificación in-app.
    """
    reserva = get_object_or_404(Reserva, pk=reserva_id, solicitante=request.user)

    if reserva.estado in ('CANCELADA', 'RECHAZADA', 'ELIMINADA'):
        messages.error(request, 'Esta reserva no puede cancelarse.')
        return redirect('mis_reservas')

    # Validar regla de 12 horas (excepto para administradores)
    if not (request.user.is_staff or request.user.is_superuser):
        ahora = timezone.now()
        inicio_reserva = timezone.make_aware(datetime.combine(reserva.fecha, reserva.hora_inicio))
        if inicio_reserva <= ahora + timedelta(hours=12):
            messages.error(request, 'Solo puedes cancelar con al menos 12 horas de anticipación.')
            return redirect('mis_reservas')

    if request.method == 'POST':
        fue_pagada = reserva.estado == 'APROBADA'
        reserva.estado = 'CANCELADA'
        reserva.save()

        recurso = reserva.salon.nombre if reserva.salon else reserva.cancha.nombre

        # Notificación in-app a todos los administradores
        admins = User.objects.filter(is_staff=True)
        solicitante_nombre = reserva.get_solicitante_display()
        for admin in admins:
            Notificacion.objects.create(
                user=admin,
                tipo='RESERVA_CANCELADA',
                titulo='Reserva cancelada por el usuario',
                mensaje=f'{solicitante_nombre} canceló su reserva de {recurso} '
                        f'del {reserva.fecha.strftime("%d/%m/%Y")} '
                        f'({reserva.hora_inicio.strftime("%H:%M")} – {reserva.hora_fin.strftime("%H:%M")}).',
                reserva=reserva,
            )

        # Notificación al usuario que cancela
        crear_notificacion_reserva(
            request.user,
            'RESERVA_CANCELADA',
            'Reserva cancelada correctamente',
            f'Has cancelado tu reserva para {recurso} del {reserva.fecha.strftime("%d/%m/%Y")}.',
            reserva,
        )

        # Emitir cupón si la reserva ya estaba aprobada (= pagada)
        if fue_pagada:
            emitir_cupon(request.user, reserva, 'CANCELACION_USUARIO')

        # Correo a admins (no bloqueante)
        try:
            correo_modificacion_a_admins(reserva, accion='cancelada')
        except Exception:
            pass

        if fue_pagada:
            messages.success(request, 'Tu reserva fue cancelada. Se emitió un cupón de compensación en Mis cupones.')
        else:
            messages.success(request, 'Tu reserva fue cancelada correctamente.')
        return redirect('mis_reservas')

    return redirect('mis_reservas')


@login_required
def gestionar_reserva(request, reserva_id):
    """
    Admin aprueba, rechaza o cancela una reserva.

    Usa @login_required (no @staff_required) intencionalmente: los admins
    de salón asignados en Salon.admins también deben poder gestionar reservas
    aunque no sean staff globales. La autorización se verifica dentro de la vista.

    Flujo de correos al gestionar:
    - Correo C: resultado al solicitante
    - Correo D: respaldo al admin que gestionó
    - Correo E: al encargado de equipos (solo si aprueba + tiene insumos)
    """
    reserva = get_object_or_404(Reserva, pk=reserva_id)

    # Verificación de permisos: Staff, Superuser o Admin del Salón específico
    if not (request.user.is_staff or request.user.is_superuser):
        if reserva.salon:
            if not reserva.salon.admins.filter(pk=request.user.pk).exists():
                messages.error(request, 'No tienes permisos para gestionar esta reserva.')
                return redirect('inicio')
        else:
            # Es una cancha y el usuario no es staff/superuser
            messages.error(request, 'No tienes permisos para gestionar esta reserva.')
            return redirect('inicio')

    if reserva.estado not in ('PENDIENTE',):
        messages.error(request, f'Esta reserva ya fue gestionada ({reserva.get_estado_display()}).')
        return redirect('inicio')

    if request.method == 'POST':
        accion = request.POST.get('accion', '')
        motivo_rechazo = request.POST.get('motivo_rechazo', '').strip()
        estado_anterior = reserva.estado

        if accion not in ('aprobar', 'rechazar', 'cancelar'):
            messages.error(request, 'Acción no válida.')
            return render(request, 'reservas/gestionar_reserva.html', {'reserva': reserva})

        mapa_estados = {
            'aprobar': 'APROBADA',
            'rechazar': 'RECHAZADA',
            'cancelar': 'CANCELADA',
        }
        reserva.estado = mapa_estados[accion]
        reserva.gestionado_por = request.user
        reserva.motivo_rechazo = motivo_rechazo
        
        reserva.save()

        # Registrar en bitácora
        recurso = reserva.salon.nombre if reserva.salon else reserva.cancha.nombre
        usuario_str = reserva.solicitante.username if reserva.solicitante else 'Usuario eliminado'
        registrar_accion(
            request.user,
            f"Gestionó reserva {reserva.id}",
            f"Acción: {accion.upper()} | Recinto: {recurso} | Usuario: {usuario_str}"
        )

        if reserva.estado == 'APROBADA' and estado_anterior == 'PENDIENTE':
            RegistroTesoreria.objects.get_or_create(reserva=reserva)

        if (
            reserva.estado == 'APROBADA'
            and estado_anterior == 'PENDIENTE'
            and reserva.gestionado_por
        ):
            # Diferenciar entre reserva nueva o cambio de horario
            es_cambio = bool(reserva.fecha_anterior)
            titulo = 'Cambio de horario aprobado' if es_cambio else 'Reserva aprobada'
            if es_cambio:
                msg = f'Tu cambio de horario para la reserva de {(reserva.salon.nombre if reserva.salon else reserva.cancha.nombre)} del {reserva.fecha.strftime("%d/%m/%Y")} fue aprobado.'
            else:
                msg = f'Tu reserva para {(reserva.salon.nombre if reserva.salon else reserva.cancha.nombre)} del {reserva.fecha.strftime("%d/%m/%Y")} fue aprobada por administración.'

            crear_notificacion_reserva(
                reserva.solicitante,
                'RESERVA_APROBADA',
                titulo,
                msg,
                reserva,
            )
        elif reserva.estado == 'RECHAZADA':
            crear_notificacion_reserva(
                reserva.solicitante,
                'RESERVA_RECHAZADA',
                'Reserva rechazada',
                f'Lamentablemente, tu reserva para {(reserva.salon.nombre if reserva.salon else reserva.cancha.nombre)} del {reserva.fecha.strftime("%d/%m/%Y")} ha sido rechazada.',
                reserva,
            )
        elif reserva.estado == 'CANCELADA':
            crear_notificacion_reserva(
                reserva.solicitante,
                'RESERVA_CANCELADA',
                'Reserva cancelada por administración',
                f'Tu reserva para {(reserva.salon.nombre if reserva.salon else reserva.cancha.nombre)} del {reserva.fecha.strftime("%d/%m/%Y")} ha sido cancelada por el administrador.',
                reserva,
            )

        # Correo C: resultado al solicitante
        correo_resultado_a_solicitante(reserva)

        # Correo D: respaldo al admin que gestionó
        correo_respaldo_a_admin(reserva)

        messages.success(request, f'Reserva {reserva.get_estado_display().lower()} correctamente.')
        if request.user.is_staff or request.user.is_superuser:
            return redirect('panel_reservas')
        return redirect('inicio')

    return render(request, 'reservas/gestionar_reserva.html', {'reserva': reserva})


# ─── Vistas de Perfil ────────────────────────────────────────────────────────

@login_required
def perfil(request):
    perfil_obj, _ = UserProfile.objects.get_or_create(user=request.user)

    if request.method == 'POST' and request.FILES.get('foto'):
        foto = request.FILES['foto']

        # Validar tamaño (máx 5 MB)
        if foto.size > 5 * 1024 * 1024:
            messages.error(request, 'La imagen no puede superar los 5 MB.')
            return redirect('perfil')

        # Validar content-type declarado
        content_type = (getattr(foto, 'content_type', '') or '').lower()
        if not content_type.startswith('image/'):
            messages.error(request, 'Solo se permiten imágenes como foto de perfil.')
            return redirect('perfil')

        # Validar magic bytes con PIL (evita archivos disfrazados de imagen)
        try:
            foto.seek(0)
            img = Image.open(foto)
            img.verify()
            foto.seek(0)
        except (UnidentifiedImageError, OSError, ValueError):
            messages.error(request, 'El archivo subido no es una imagen válida.')
            return redirect('perfil')

        perfil_obj.foto = foto
        perfil_obj.save()
        messages.success(request, 'Foto actualizada.')
        return redirect('perfil')

    return render(request, 'perfil/perfil.html', {'perfil_obj': perfil_obj})


@login_required
def mis_datos(request):
    perfil_obj, _ = UserProfile.objects.get_or_create(user=request.user)

    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        apellido = request.POST.get('apellido', '').strip()
        telefono = request.POST.get('telefono', '').strip()

        if not nombre:
            messages.error(request, 'El nombre es obligatorio.')
            return render(request, 'perfil/mis_datos.html', {'perfil_obj': perfil_obj})

        request.user.first_name = nombre
        request.user.last_name = apellido
        request.user.save()
        perfil_obj.telefono = telefono
        perfil_obj.save()

        messages.success(request, 'Datos actualizados correctamente.')
        
        # Notificar al usuario
        crear_notificacion_reserva(
            request.user, 
            'PERFIL_ACTUALIZADO', 
            'Datos personales actualizados', 
            'Has actualizado correctamente tu información de perfil (nombre, teléfono, etc.).'
        )
        
        # Avisar a los administradores en su panel de avisos
        notificar_admins(
            'PERFIL_ACTUALIZADO',
            'Usuario actualizó sus datos',
            f"El usuario {request.user.email} actualizó sus datos personales (Nombre: {nombre} {apellido})."
        )

        return redirect('mis_datos')

    return render(request, 'perfil/mis_datos.html', {'perfil_obj': perfil_obj})


@login_required
def cambiar_email(request):
    if request.method == 'POST':
        nuevo_email = request.POST.get('nuevo_email', '').strip().lower()
        nuevo_email2 = request.POST.get('nuevo_email2', '').strip().lower()
        clave = request.POST.get('clave', '')

        if nuevo_email != nuevo_email2:
            messages.error(request, 'Los correos no coinciden.')
            return render(request, 'perfil/cambiar_email.html')

        if not nuevo_email:
            messages.error(request, 'Ingresa un correo válido.')
            return render(request, 'perfil/cambiar_email.html')

        if User.objects.filter(email=nuevo_email).exclude(pk=request.user.pk).exists():
            messages.error(request, 'Ese correo ya está en uso por otra cuenta.')
            return render(request, 'perfil/cambiar_email.html')

        if not request.user.check_password(clave):
            messages.error(request, 'La contraseña ingresada es incorrecta.')
            return render(request, 'perfil/cambiar_email.html')

        cambio = CambioEmailPendiente.crear_para(request.user, nuevo_email)
        confirm_url = request.build_absolute_uri(
            reverse('confirmar_cambio_email', kwargs={'token': cambio.token})
        )
        correo_confirmar_cambio_email(request.user, nuevo_email, confirm_url)

        return render(request, 'perfil/cambiar_email.html', {'enviado': True, 'nuevo_email': nuevo_email})

    return render(request, 'perfil/cambiar_email.html')


def confirmar_cambio_email(request, token):
    try:
        cambio = CambioEmailPendiente.objects.select_related('user').get(token=token)
    except CambioEmailPendiente.DoesNotExist:
        return render(request, 'perfil/confirmar_email_resultado.html', {'error': 'El enlace no es válido.'})

    if not cambio.esta_vigente():
        cambio.delete()
        return render(request, 'perfil/confirmar_email_resultado.html', {'error': 'El enlace expiró (24 h). Solicita uno nuevo.'})

    user = cambio.user
    user.email = cambio.nuevo_email
    user.username = cambio.nuevo_email
    user.save()
    cambio.delete()

    # Notificar al usuario (aunque se le desloguee, lo verá al volver)
    crear_notificacion_reserva(
        user, 
        'SEGURIDAD_CAMBIO_EMAIL', 
        'Correo electrónico actualizado', 
        f'Has confirmado exitosamente el cambio de tu correo a: {user.email}.'
    )
    
    # Avisar a los administradores en su panel de avisos
    notificar_admins(
        'SEGURIDAD_CAMBIO_EMAIL',
        'Cambio de correo electrónico',
        f"El usuario {user.username} ha confirmado el cambio de su dirección de correo electrónico."
    )

    logout(request)
    return render(request, 'perfil/confirmar_email_resultado.html', {'exito': True})


@login_required
def cambiar_contrasena(request):
    if request.method == 'POST':
        clave_actual = request.POST.get('clave_actual', '')
        nueva_clave = request.POST.get('nueva_clave', '')
        nueva_clave2 = request.POST.get('nueva_clave2', '')

        if not request.user.check_password(clave_actual):
            messages.error(request, 'La contraseña actual es incorrecta.')
            return render(request, 'perfil/cambiar_contrasena.html')

        if len(nueva_clave) < 8:
            messages.error(request, 'La nueva contraseña debe tener al menos 8 caracteres.')
            return render(request, 'perfil/cambiar_contrasena.html')

        if nueva_clave != nueva_clave2:
            messages.error(request, 'Las contraseñas nuevas no coinciden.')
            return render(request, 'perfil/cambiar_contrasena.html')

        request.user.set_password(nueva_clave)
        request.user.save()
        update_session_auth_hash(request, request.user)
        
        # Notificar al usuario
        crear_notificacion_reserva(
            request.user, 
            'SEGURIDAD_CAMBIO_CLAVE', 
            'Contraseña cambiada', 
            'Has actualizado la contraseña de tu cuenta exitosamente.'
        )
        
        # Avisar a los administradores en su panel de avisos
        notificar_admins(
            'SEGURIDAD_CAMBIO_CLAVE',
            'Cambio de contraseña de usuario',
            f"El usuario {request.user.email} ha modificado su contraseña de acceso."
        )

        messages.success(request, 'Contraseña actualizada correctamente.')
        return redirect('perfil')

    return render(request, 'perfil/cambiar_contrasena.html')


@login_required
def mis_tarjetas(request):
    return render(request, 'perfil/mis_tarjetas.html')


@login_required
def notificaciones(request):
    notificaciones_nuevas = list(
        Notificacion.objects.filter(user=request.user, leida=False)
        .select_related('reserva')
    )
    historial_list = Notificacion.objects.filter(user=request.user).select_related('reserva').order_by('-creada_en')
    from django.core.paginator import Paginator
    paginator = Paginator(historial_list, 10)
    page_number = request.GET.get('page')
    historial = paginator.get_page(page_number)

    if notificaciones_nuevas:
        Notificacion.objects.filter(
            pk__in=[n.pk for n in notificaciones_nuevas]
        ).update(leida=True)

    return render(request, 'perfil/notificaciones.html', {
        'notificaciones_nuevas': notificaciones_nuevas,
        'historial_notificaciones': historial,
    })


@login_required
def mis_cupones(request):
    hoy = timezone.now().date()
    Cupon.objects.filter(usuario=request.user, estado='ACTIVO', fecha_expiracion__lt=hoy).update(estado='EXPIRADO')
    filtro = request.GET.get('filtro', 'todos')
    qs = Cupon.objects.filter(usuario=request.user).order_by('-fecha_emision')
    if filtro == 'activo':
        qs = qs.filter(estado='ACTIVO')
    elif filtro == 'usado':
        qs = qs.filter(estado='USADO')
    elif filtro == 'expirado':
        qs = qs.filter(estado='EXPIRADO')
    return render(request, 'perfil/mis_cupones.html', {'cupones': qs, 'filtro': filtro})


@login_required
def validar_cupon(request):
    codigo = request.GET.get('codigo', '').strip().upper()
    cancha_nombre = request.GET.get('cancha', '').strip()
    if not codigo:
        return JsonResponse({'valido': False, 'mensaje': 'Código vacío.'})
    try:
        cupon = Cupon.objects.get(codigo=codigo, usuario=request.user)
    except Cupon.DoesNotExist:
        return JsonResponse({'valido': False, 'mensaje': 'Cupón no encontrado o no te pertenece.'})
    if not cupon.esta_vigente():
        return JsonResponse({'valido': False, 'mensaje': f'Cupón {cupon.get_estado_display().lower()}.'})
    if cancha_nombre and cupon.nombre_recurso and cupon.nombre_recurso != cancha_nombre:
        return JsonResponse({
            'valido': False,
            'mensaje': f'Este cupón solo es válido para {cupon.nombre_recurso}.',
        })
    return JsonResponse({
        'valido': True,
        'mensaje': f'Cupón válido — expira el {cupon.fecha_expiracion.strftime("%d/%m/%Y")}.',
        'recurso': cupon.nombre_recurso,
    })


@staff_required
def panel_cupones(request):
    hoy = timezone.now().date()
    Cupon.objects.filter(estado='ACTIVO', fecha_expiracion__lt=hoy).update(estado='EXPIRADO')
    filtro = request.GET.get('filtro', 'todos')
    qs = Cupon.objects.select_related('usuario', 'reserva_origen', 'reserva_uso').order_by('-fecha_emision')
    if filtro == 'activo':
        qs = qs.filter(estado='ACTIVO')
    elif filtro == 'usado':
        qs = qs.filter(estado='USADO')
    elif filtro == 'expirado':
        qs = qs.filter(estado='EXPIRADO')
    paginator = Paginator(qs, 25)
    page = request.GET.get('page', 1)
    cupones = paginator.get_page(page)
    conteos = {
        'todos': Cupon.objects.count(),
        'activo': Cupon.objects.filter(estado='ACTIVO').count(),
        'usado': Cupon.objects.filter(estado='USADO').count(),
        'expirado': Cupon.objects.filter(estado='EXPIRADO').count(),
    }
    return render(request, 'panel/cupones.html', {'cupones': cupones, 'filtro': filtro, 'conteos': conteos})


@staff_required
def panel_crear_cupon_reserva(request):
    usuarios = User.objects.filter(is_staff=False, is_active=True).order_by('first_name', 'last_name', 'email')
    canchas = ConfigCancha.objects.filter(activa=True).order_by('nombre')

    if request.method == 'POST':
        usuario_id = request.POST.get('usuario_id', '').strip()
        nombre_recurso = request.POST.get('nombre_recurso', '').strip()
        fecha_expiracion_str = request.POST.get('fecha_expiracion', '').strip()

        errores = []
        usuario = None
        if not usuario_id:
            errores.append('Debes seleccionar un usuario.')
        else:
            try:
                usuario = User.objects.get(pk=int(usuario_id), is_staff=False, is_active=True)
            except (User.DoesNotExist, ValueError):
                errores.append('Usuario inválido.')

        fecha_expiracion = None
        if not fecha_expiracion_str:
            errores.append('La fecha de expiración es requerida.')
        else:
            try:
                fecha_expiracion = date.fromisoformat(fecha_expiracion_str)
                if fecha_expiracion <= date.today():
                    errores.append('La fecha de expiración debe ser futura.')
            except ValueError:
                errores.append('Fecha de expiración inválida.')

        if errores:
            for e in errores:
                messages.error(request, e)
            return render(request, 'panel/crear_cupon_reserva.html', {
                'usuarios': usuarios, 'canchas': canchas, 'form_data': request.POST, 'today': date.today(),
            })

        cupon = Cupon.objects.create(
            codigo=Cupon.generar_codigo(),
            usuario=usuario,
            nombre_recurso=nombre_recurso,
            motivo='CANCELACION_SISTEMA',
            fecha_expiracion=fecha_expiracion,
        )
        Notificacion.objects.create(
            user=usuario,
            tipo='CUPON_EMITIDO',
            titulo='Cupón de reserva emitido',
            mensaje=f'El administrador te ha emitido un cupón de reserva: {cupon.codigo}. '
                    f'Válido hasta el {fecha_expiracion.strftime("%d/%m/%Y")}.'
                    + (f' Cancha: {nombre_recurso}.' if nombre_recurso else ''),
        )
        Bitacora.objects.create(
            admin=request.user,
            accion='Cupón de reserva creado manualmente',
            detalles=f'Código: {cupon.codigo} | Usuario: {usuario.email} | Vence: {fecha_expiracion}',
        )
        messages.success(request, f'Cupón de reserva {cupon.codigo} creado para {usuario.email}.')
        return redirect('panel_cupones')

    return render(request, 'panel/crear_cupon_reserva.html', {
        'usuarios': usuarios, 'canchas': canchas, 'form_data': {}, 'today': date.today(),
    })


# ─── Perfil Alcalde ──────────────────────────────────────────────────────────

@alcalde_required
def perfil_alcalde(request):
    pendientes = Reserva.objects.filter(
        estado='PENDIENTE_ALCALDE', tipo_reserva='ORGANIZACION'
    ).order_by('fecha', 'hora_inicio')

    firmadas_qs = Reserva.objects.filter(
        tipo_reserva='ORGANIZACION', estado='APROBADA'
    ).order_by('-fecha')
    firmadas = Paginator(firmadas_qs, 10).get_page(request.GET.get('page_firmadas'))

    rechazadas_qs = Reserva.objects.filter(
        tipo_reserva='ORGANIZACION', estado='RECHAZADA'
    ).order_by('-fecha')
    rechazadas = Paginator(rechazadas_qs, 10).get_page(request.GET.get('page_rechazadas'))

    return render(request, 'alcalde/inicio.html', {
        'pendientes': pendientes,
        'firmadas': firmadas,
        'rechazadas': rechazadas,
    })


@alcalde_required
def alcalde_detalle_reserva(request, reserva_id):
    reserva = get_object_or_404(Reserva, pk=reserva_id, tipo_reserva='ORGANIZACION')
    return render(request, 'alcalde/detalle_reserva.html', {'reserva': reserva})


@alcalde_required
def alcalde_firmar_reserva(request, reserva_id):
    if request.method != 'POST':
        return redirect('perfil_alcalde')
    reserva = get_object_or_404(Reserva, pk=reserva_id, estado='PENDIENTE_ALCALDE')
    reserva.estado = 'APROBADA'
    reserva.gestionado_por = request.user
    if not reserva.firma_alcalde_carta_en:
        reserva.firma_alcalde_carta_en = timezone.now()
    reserva.save(update_fields=['estado', 'gestionado_por', 'firma_alcalde_carta_en', 'actualizado_en'])

    RegistroTesoreria.objects.create(reserva=reserva)

    Bitacora.objects.create(
        admin=request.user,
        accion='Alcalde firmó reserva de organización',
        detalles=f'Reserva #{reserva.pk} — {reserva.nombre_organizacion} — '
                 f'{reserva.fecha.strftime("%d/%m/%Y")} {reserva.hora_inicio.strftime("%H:%M")}',
    )
    _fecha_fmt = f'{reserva.fecha.strftime("%d/%m/%Y")} {reserva.hora_inicio.strftime("%H:%M")}–{reserva.hora_fin.strftime("%H:%M")}'
    if reserva.solicitante:
        crear_notificacion_reserva(
            reserva.solicitante,
            'RESERVA_APROBADA',
            '¡Reserva aprobada!',
            f'Tu solicitud de {reserva.nombre_organizacion} para '
            f'{reserva.cancha.nombre if reserva.cancha else "el recinto"} '
            f'el {_fecha_fmt} fue aprobada por el alcalde.',
            reserva=reserva,
        )
    notificar_admins(
        'RESERVA_APROBADA',
        f'Reserva aprobada: {reserva.nombre_organizacion}',
        f'El alcalde aprobó la solicitud de {reserva.nombre_organizacion} para '
        f'{reserva.cancha.nombre if reserva.cancha else "el recinto"} el {_fecha_fmt}.',
        reserva=reserva,
    )
    messages.success(request, f'Reserva de {reserva.nombre_organizacion} firmada y aprobada.')
    return redirect('perfil_alcalde')


@alcalde_required
def alcalde_rechazar_reserva(request, reserva_id):
    if request.method != 'POST':
        return redirect('perfil_alcalde')
    reserva = get_object_or_404(Reserva, pk=reserva_id, estado='PENDIENTE_ALCALDE')
    motivo_rechazo = request.POST.get('motivo_rechazo', '').strip()
    reserva.estado = 'RECHAZADA'
    reserva.motivo_rechazo = motivo_rechazo
    reserva.gestionado_por = request.user
    reserva.save(update_fields=['estado', 'motivo_rechazo', 'gestionado_por', 'actualizado_en'])

    Bitacora.objects.create(
        admin=request.user,
        accion='Alcalde rechazó reserva de organización',
        detalles=f'Reserva #{reserva.pk} — {reserva.nombre_organizacion} — Motivo: {motivo_rechazo}',
    )
    _fecha_fmt = f'{reserva.fecha.strftime("%d/%m/%Y")} {reserva.hora_inicio.strftime("%H:%M")}–{reserva.hora_fin.strftime("%H:%M")}'
    if reserva.solicitante:
        crear_notificacion_reserva(
            reserva.solicitante,
            'RESERVA_RECHAZADA',
            'Reserva rechazada',
            f'Tu solicitud de {reserva.nombre_organizacion} para '
            f'{reserva.cancha.nombre if reserva.cancha else "el recinto"} '
            f'el {_fecha_fmt} fue rechazada.'
            + (f' Motivo: {motivo_rechazo}' if motivo_rechazo else ''),
            reserva=reserva,
        )
    notificar_admins(
        'RESERVA_RECHAZADA',
        f'Reserva rechazada: {reserva.nombre_organizacion}',
        f'El alcalde rechazó la solicitud de {reserva.nombre_organizacion} para '
        f'{reserva.cancha.nombre if reserva.cancha else "el recinto"} el {_fecha_fmt}.'
        + (f' Motivo: {motivo_rechazo}' if motivo_rechazo else ''),
        reserva=reserva,
    )
    messages.success(request, f'Reserva de {reserva.nombre_organizacion} rechazada.')
    return redirect('perfil_alcalde')


# ─── Perfil Tesorero ──────────────────────────────────────────────────────────

@tesorero_required
def perfil_tesorero(request):
    filtro = request.GET.get('filtro', 'todos')
    busqueda = request.GET.get('q', '').strip()
    qs = RegistroTesoreria.objects.select_related(
        'reserva', 'reserva__cancha', 'reserva__salon'
    ).order_by('-creado_en')

    if filtro == 'pendiente':
        qs = qs.filter(estado__in=['PENDIENTE', 'VISTO'])
    elif filtro == 'ingresado':
        qs = qs.filter(estado='INGRESADO')

    if busqueda:
        qs = qs.filter(
            Q(reserva__tipo_reserva='ORGANIZACION', reserva__nombre_organizacion__icontains=busqueda) |
            Q(reserva__tipo_reserva='PERSONAL', reserva__solicitante_nombre__icontains=busqueda)
        )

    paginator = Paginator(qs, 10)
    page = request.GET.get('page', 1)
    registros = paginator.get_page(page)

    conteos = {
        'todos': RegistroTesoreria.objects.count(),
        'pendiente': RegistroTesoreria.objects.filter(estado__in=['PENDIENTE', 'VISTO']).count(),
        'ingresado': RegistroTesoreria.objects.filter(estado='INGRESADO').count(),
    }
    filtros_labels = [
        ('todos', f'Todos ({conteos["todos"]})'),
        ('pendiente', f'Pendientes ({conteos["pendiente"]})'),
        ('ingresado', f'Ingresados ({conteos["ingresado"]})'),
    ]
    return render(request, 'tesorero/inicio.html', {
        'registros': registros,
        'filtro': filtro,
        'busqueda': busqueda,
        'conteos': conteos,
        'filtros': filtros_labels,
    })


@tesorero_required
def tesorero_actualizar_estado(request, registro_id):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)
    registro = get_object_or_404(RegistroTesoreria, pk=registro_id)
    nuevo_estado = request.POST.get('estado', '').strip()

    # Validar que el estado sea reconocido
    estados_validos = [e[0] for e in RegistroTesoreria.ESTADO_CHOICES]
    if nuevo_estado not in estados_validos:
        return JsonResponse({'status': 'error', 'message': 'Estado inválido'}, status=400)

    registro.estado = nuevo_estado
    registro.notas = request.POST.get('notas', registro.notas).strip()
    registro.save(update_fields=['estado', 'notas', 'actualizado_en'])
    return JsonResponse({'status': 'success', 'estado': nuevo_estado, 'estado_display': registro.get_estado_display()})


# ─── Panel de Administración ─────────────────────────────────────────────────




@staff_required
def panel_inicio(request):
    stats = {
        'total_reservas': Reserva.objects.exclude(estado='ELIMINADA').count(),
        'pendientes': Reserva.objects.filter(estado='PENDIENTE').count(),
        'aprobadas': Reserva.objects.filter(estado='APROBADA').count(),
        'total_usuarios': User.objects.filter(is_staff=False).count(),
    }
    from django.core.paginator import Paginator

    # Notificaciones del sistema
    notificaciones_list = Notificacion.objects.filter(user=request.user).order_by('-creada_en')
    paginator_notif = Paginator(notificaciones_list, 10)
    page_num_notif = request.GET.get('page_notificaciones')
    notificaciones = paginator_notif.get_page(page_num_notif)

    return render(request, 'panel/inicio.html', {
        'stats': stats, 
        'notificaciones': notificaciones
    })


@staff_required
def panel_reservas(request):
    """Listado maestro de todas las reservas del sistema."""
    estado_filtro = request.GET.get('estado', '')
    tipo_filtro = request.GET.get('tipo', 'personal')  # 'personal' u 'organizacion'

    base = Reserva.objects.exclude(estado='ELIMINADA').select_related('salon', 'cancha', 'solicitante')

    if tipo_filtro == 'organizacion':
        qs = base.filter(tipo_reserva='ORGANIZACION')
    else:
        qs = base.filter(tipo_reserva='PERSONAL')

    qs = qs.order_by('fecha', 'hora_inicio')

    if estado_filtro:
        qs = qs.filter(estado=estado_filtro)

    # Conteos sobre el tipo activo
    base_tipo = base.filter(tipo_reserva='ORGANIZACION' if tipo_filtro == 'organizacion' else 'PERSONAL')
    conteos = {
        'PENDIENTE': base_tipo.filter(estado__in=['PENDIENTE', 'PENDIENTE_ALCALDE']).count(),
        'APROBADA': base_tipo.filter(estado='APROBADA').count(),
        'RECHAZADA': base_tipo.filter(estado='RECHAZADA').count(),
    }

    paginator = Paginator(qs, 20)
    reservas = paginator.get_page(request.GET.get('page'))

    return render(request, 'panel/reservas.html', {
        'reservas': reservas,
        'estado_filtro': estado_filtro,
        'tipo_filtro': tipo_filtro,
        'total': base_tipo.count(),
        'conteos': conteos
    })


@staff_required
def panel_eliminar_reserva(request, reserva_id):
    """Borrado lógico de una reserva desde el panel admin."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    reserva = get_object_or_404(Reserva, pk=reserva_id)
    recurso = reserva.salon.nombre if reserva.salon else reserva.cancha.nombre
    usuario_str = reserva.solicitante.username if reserva.solicitante else 'Usuario eliminado'
    detalles = f"Recinto: {recurso} | Usuario: {usuario_str} | Fecha: {reserva.fecha.strftime('%d-%m-%Y')}"
    
    reserva.estado = 'ELIMINADA'
    reserva.save(update_fields=['estado'])
    
    registrar_accion(request.user, f"Eliminó reserva {reserva_id}", detalles)
    messages.success(request, 'Reserva marcada como eliminada y movida al historial.')
    return redirect('panel_reservas')


@staff_required
def panel_cancelar_reserva(request, reserva_id):
    """Cancelación administrativa de una reserva."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    reserva = get_object_or_404(Reserva, pk=reserva_id)
    
    if reserva.estado in ('CANCELADA', 'RECHAZADA', 'ELIMINADA'):
        messages.error(request, 'Esta reserva ya no puede ser cancelada.')
        return redirect('panel_reservas')

    reserva.estado = 'CANCELADA'
    reserva.gestionado_por = request.user
    reserva.save(update_fields=['estado', 'gestionado_por'])

    recurso = reserva.salon.nombre if reserva.salon else reserva.cancha.nombre

    # Notificar al usuario solicitante solo si aún existe
    if reserva.solicitante:
        Notificacion.objects.create(
            user=reserva.solicitante,
            tipo='RESERVA_CANCELADA',
            titulo='Reserva cancelada por el administrador',
            mensaje=f'Tu reserva para {recurso} del {reserva.fecha.strftime("%d/%m/%Y")} ha sido cancelada por la administración.',
            reserva=reserva,
        )

    # Registrar en bitácora
    usuario_str = reserva.solicitante.username if reserva.solicitante else 'Usuario eliminado'
    detalles = f"Recinto: {recurso} | Usuario: {usuario_str}"
    registrar_accion(request.user, f"Canceló reserva {reserva_id}", detalles)
    
    messages.success(request, 'Reserva cancelada correctamente.')
    return redirect('panel_reservas')


@staff_required
def panel_editar_reserva(request, reserva_id):
    """Edición administrativa de una reserva con todas las restricciones del sistema."""
    reserva = get_object_or_404(Reserva, pk=reserva_id)

    # Reservas personales: solo editables cuando están PENDIENTE
    if reserva.tipo_reserva == 'PERSONAL' and reserva.estado != 'PENDIENTE':
        messages.error(request, 'Las reservas personales solo pueden editarse mientras están pendientes.')
        return redirect('panel_reservas')

    # Reservas de organización: solo editables cuando están PENDIENTE_ALCALDE
    if reserva.tipo_reserva == 'ORGANIZACION' and reserva.estado != 'PENDIENTE_ALCALDE':
        messages.error(request, 'Las reservas de organización solo pueden editarse mientras están pendientes de firma.')
        return redirect('panel_reservas')

    # Datos para restricciones en el HTML
    hoy_val = date.today().strftime('%Y-%m-%d')
    limite_val = (date.today() + timedelta(days=31)).strftime('%Y-%m-%d')
    contexto = {
        'reserva': reserva,
        'hoy': hoy_val,
        'limite_futuro': limite_val
    }

    if request.method == 'POST':
        if reserva.estado == 'ELIMINADA':
            messages.error(request, 'No puedes editar una reserva que ha sido eliminada.')
            return redirect('panel_historial_reservas')

        fecha_str = request.POST.get('fecha')
        hora_inicio_str = request.POST.get('hora_inicio')
        hora_fin_str = request.POST.get('hora_fin')
        estado = request.POST.get('estado')

        estados_validos = [e[0] for e in Reserva.ESTADO_RESERVA]
        if estado not in estados_validos:
            messages.error(request, 'Estado de reserva no válido.')
            return render(request, 'panel/editar_reserva.html', contexto)

        try:
            f = datetime.strptime(fecha_str, "%Y-%m-%d").date()
            h_ini = datetime.strptime(hora_inicio_str, "%H:%M").time()
            h_fin = datetime.strptime(hora_fin_str, "%H:%M").time()
            
            # 1. Rango de fecha: Máximo 1 mes (31 días) a futuro
            limite_futuro = date.today() + timedelta(days=31)
            if f > limite_futuro:
                messages.error(request, 'Error: Solo se permiten reservas hasta 31 días a futuro.')
                return render(request, 'panel/editar_reserva.html', contexto)
            
            # 2. Rango de horario: 09:00 a 23:00
            if h_ini < time(9, 0) or h_fin > time(23, 0):
                messages.error(request, 'Error: El horario permitido es de 09:00 a 23:00.')
                return render(request, 'panel/editar_reserva.html', contexto)

            # 3. Validación: exactamente 1 hora
            dt_ini = datetime.combine(f, h_ini)
            dt_fin = datetime.combine(f, h_fin)
            if dt_fin <= dt_ini:
                dt_fin += timedelta(days=1)
                
            diff = dt_fin - dt_ini
            if diff != timedelta(hours=1):
                messages.error(request, 'Error: Las reservas deben ser de exactamente 1 hora (ej: 09:00 a 10:00).')
                return render(request, 'panel/editar_reserva.html', contexto)

            # 4. Disponibilidad: No chocar con otras reservas (excluyendo esta misma)
            choca = Reserva.objects.filter(
                fecha=f,
                estado__in=ESTADOS_OCUPAN_SLOT
            ).exclude(pk=reserva.pk)
            
            # Filtro según recurso (Salón o Cancha)
            if reserva.salon:
                choca = choca.filter(salon=reserva.salon)
            else:
                choca = choca.filter(cancha=reserva.cancha)
            
            # Verificar solapamiento
            overlap = choca.filter(
                hora_inicio__lt=h_fin,
                hora_fin__gt=h_ini
            ).exists()
            
            if overlap:
                messages.error(request, f'Error: El horario {h_ini.strftime("%H:%M")} - {h_fin.strftime("%H:%M")} ya está ocupado.')
                return render(request, 'panel/editar_reserva.html', contexto)

            # Guardar cambios
            reserva.fecha = f
            reserva.hora_inicio = h_ini
            reserva.hora_fin = h_fin
            reserva.motivo = request.POST.get('motivo', '').strip()

            reserva.estado = estado
            estado_log = estado
            messages.success(request, 'Reserva actualizada correctamente.')

            reserva.save()
            registrar_accion(request.user, f"Editó reserva {reserva.id}", f"Estado: {estado_log} | Fecha: {f.strftime('%d-%m-%Y')} | Hora: {h_ini.strftime('%H:%M')}")
            return redirect('panel_reservas')
        except (ValueError, TypeError) as e:
            messages.error(request, f'Datos inválidos: {e}')
        except Exception:
            logger.exception('Error inesperado en panel_editar_reserva')
            messages.error(request, 'Ocurrió un error interno al guardar los cambios.')

    return render(request, 'panel/editar_reserva.html', contexto)


@staff_required
def panel_usuarios(request):
    # Usuarios con rol especial (alcalde / tesorero)
    alcaldes = User.objects.filter(
        is_staff=False, perfil__es_alcalde=True
    ).select_related('perfil').order_by('first_name', 'last_name')
    tesoreros = User.objects.filter(
        is_staff=False, perfil__es_tesorero=True
    ).select_related('perfil').order_by('first_name', 'last_name')

    # Usuarios normales y organizaciones (sin rol especial)
    base_qs = User.objects.filter(
        is_staff=False, perfil__es_alcalde=False, perfil__es_tesorero=False
    ).select_related('perfil').order_by('-date_joined')

    usuarios_personales = base_qs.filter(perfil__es_organizacion=False)
    organizaciones = base_qs.filter(perfil__es_organizacion=True)

    admins = User.objects.filter(is_staff=True).select_related('perfil').order_by('-date_joined')
    total_superusuarios = User.objects.filter(is_superuser=True).count()
    return render(request, 'panel/usuarios.html', {
        'usuarios': usuarios_personales,
        'organizaciones': organizaciones,
        'admins': admins,
        'alcaldes': alcaldes,
        'tesoreros': tesoreros,
        'total_superusuarios': total_superusuarios,
        'max_superusuarios': 3,
    })


@staff_required
def panel_crear_usuario(request):
    MAX_SUPERUSUARIOS = settings.MAX_SUPERUSUARIOS

    # Rol pre-definido desde URL (?rol=alcalde o ?rol=tesorero)
    rol_fijo = request.GET.get('rol', '') if request.method == 'GET' else request.POST.get('rol_fijo', '')
    rol_fijo = rol_fijo if rol_fijo in ('alcalde', 'tesorero') else ''

    # Bloquear creación si ya existe ese rol único
    if rol_fijo == 'alcalde' and UserProfile.objects.filter(es_alcalde=True).exists():
        messages.error(request, 'Ya existe un perfil de alcalde. Edítalo desde la lista.')
        return redirect('panel_usuarios')
    if rol_fijo == 'tesorero' and UserProfile.objects.filter(es_tesorero=True).exists():
        messages.error(request, 'Ya existe un perfil de tesorero. Edítalo desde la lista.')
        return redirect('panel_usuarios')

    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        apellido = request.POST.get('apellido', '').strip()
        email = request.POST.get('email', '').strip().lower()
        telefono = request.POST.get('telefono', '').strip()
        rut_ingresado = request.POST.get('rut', '').strip()
        rut = normalizar_rut(rut_ingresado)
        clave = request.POST.get('contrasena', '')
        clave2 = request.POST.get('contrasena2', '')
        tipo_perfil = request.POST.get('tipo_perfil', 'personal')
        es_organizacion = (tipo_perfil == 'organizacion')
        nombre_organizacion = request.POST.get('nombre_organizacion', '').strip()
        # Organizaciones siempre son rol usuario; si hay rol_fijo, ese manda
        rol = rol_fijo if rol_fijo else ('usuario' if es_organizacion else request.POST.get('rol', 'usuario'))

        errores = {}

        if es_organizacion and not nombre_organizacion:
            errores['nombre_organizacion'] = 'El nombre de la organización es obligatorio.'
        if not nombre:
            lbl = 'El nombre del representante legal' if es_organizacion else 'El nombre'
            errores['nombre'] = f'{lbl} es obligatorio.'
        if not email:
            errores['email'] = 'El correo es obligatorio.'
        elif User.objects.filter(email=email).exists():
            errores['email'] = 'Ya existe una cuenta con ese correo.'
        if rut_ingresado and not rut_valido(rut):
            errores['rut'] = 'Ingresa un RUT válido.'
        elif rut_valido(rut) and UserProfile.objects.filter(rut=rut).exists():
            errores['rut'] = 'Ya existe una cuenta con ese RUT.'
        if len(clave) < 8:
            errores['contrasena'] = 'La contraseña debe tener al menos 8 caracteres.'
        elif clave != clave2:
            errores['contrasena'] = 'Las contraseñas no coinciden.'
        if rol == 'admin' and not request.user.is_superuser:
            errores['rol'] = 'Solo un superusuario puede crear cuentas de administrador.'
        if rol == 'admin' and User.objects.filter(is_superuser=True).count() >= MAX_SUPERUSUARIOS:
            errores['rol'] = f'Ya se alcanzó el límite de {MAX_SUPERUSUARIOS} superusuarios.'

        ctx = {
            'errores': errores, 'datos': request.POST,
            'total_superusuarios': User.objects.filter(is_superuser=True).count(),
            'max_superusuarios': MAX_SUPERUSUARIOS, 'rol_fijo': rol_fijo,
            'tipo_perfil': tipo_perfil,
        }

        if errores:
            return render(request, 'panel/crear_usuario.html', ctx)

        es_admin = rol == 'admin'
        user = User.objects.create_user(
            username=email, email=email, password=clave,
            first_name=nombre, last_name=apellido,
            is_staff=es_admin, is_superuser=es_admin,
        )
        UserProfile.objects.create(
            user=user,
            telefono=telefono,
            rut=rut if rut_valido(rut) else None,
            es_alcalde=(rol == 'alcalde'),
            es_tesorero=(rol == 'tesorero'),
            es_organizacion=es_organizacion,
            nombre_organizacion=nombre_organizacion if es_organizacion else '',
        )
        rol_display = {'admin': 'Administrador', 'alcalde': 'Alcalde', 'tesorero': 'Tesorero'}.get(rol, 'Usuario')
        if es_organizacion:
            rol_display = f'Organización ({nombre_organizacion})'
        registrar_accion(request.user, f"Creó {rol_display.lower()} {email}", f"Nombre: {nombre} {apellido}")
        messages.success(request, f'{rol_display} creado correctamente.')
        return redirect('panel_usuarios')

    total_superusuarios = User.objects.filter(is_superuser=True).count()
    hay_alcalde = UserProfile.objects.filter(es_alcalde=True).exists()
    hay_tesorero = UserProfile.objects.filter(es_tesorero=True).exists()
    return render(request, 'panel/crear_usuario.html', {
        'total_superusuarios': total_superusuarios,
        'max_superusuarios': MAX_SUPERUSUARIOS,
        'rol_fijo': rol_fijo,
        'hay_alcalde': hay_alcalde,
        'hay_tesorero': hay_tesorero,
    })


@staff_required
def panel_editar_usuario(request, usuario_id):
    usuario = get_object_or_404(User, pk=usuario_id, is_staff=False)
    perfil_obj, _ = UserProfile.objects.get_or_create(user=usuario)

    ctx = {'u': usuario, 'perfil_obj': perfil_obj}

    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        apellido = request.POST.get('apellido', '').strip()
        email = request.POST.get('email', '').strip().lower()
        telefono = request.POST.get('telefono', '').strip()
        rut = normalizar_rut(request.POST.get('rut', '').strip())

        if not nombre:
            messages.error(request, 'El nombre es obligatorio.')
            return render(request, 'panel/editar_usuario.html', ctx)
        if rut and not rut_valido(rut):
            messages.error(request, 'Ingresa un RUT válido.')
            return render(request, 'panel/editar_usuario.html', ctx)
        if User.objects.filter(email=email).exclude(pk=usuario.pk).exists():
            messages.error(request, 'Ese correo ya está en uso por otra cuenta.')
            return render(request, 'panel/editar_usuario.html', ctx)
        if rut and UserProfile.objects.filter(rut=rut).exclude(user=usuario).exists():
            messages.error(request, 'Ese RUT ya está en uso por otra cuenta.')
            return render(request, 'panel/editar_usuario.html', ctx)

        usuario.first_name = nombre
        usuario.last_name = apellido
        usuario.email = email
        usuario.username = email
        usuario.save()

        es_organizacion = request.POST.get('es_organizacion') in ('on', '1', 'true')
        nombre_organizacion = request.POST.get('nombre_organizacion', '').strip()
        if es_organizacion and not nombre_organizacion:
            messages.error(request, 'El nombre de la organización es obligatorio.')
            return render(request, 'panel/editar_usuario.html', ctx)

        perfil_obj.telefono = telefono
        perfil_obj.rut = rut if rut_valido(rut) else perfil_obj.rut
        perfil_obj.es_organizacion = es_organizacion
        perfil_obj.nombre_organizacion = nombre_organizacion if es_organizacion else ''
        perfil_obj.save()

        registrar_accion(request.user, f'Editó usuario {usuario.username}', 'Datos personales actualizados.')
        messages.success(request, 'Usuario actualizado correctamente.')
        return redirect('panel_usuarios')

    return render(request, 'panel/editar_usuario.html', ctx)


@staff_required
def panel_toggle_usuario(request, usuario_id):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    usuario = get_object_or_404(User, pk=usuario_id, is_staff=False)

    usuario.is_active = not usuario.is_active
    usuario.save(update_fields=['is_active'])

    estado = 'deshabilitada' if not usuario.is_active else 'habilitada'
    registrar_accion(request.user, f"{estado.capitalize()} usuario {usuario.username}")
    messages.success(request, f'Cuenta {estado} correctamente.')
    return redirect('panel_usuarios')


@staff_required
def panel_eliminar_usuario(request, usuario_id):
    usuario = get_object_or_404(User, pk=usuario_id, is_staff=False)

    if request.method == 'POST':
        username = usuario.username
        usuario.delete()
        registrar_accion(request.user, f"Eliminó permanentemente usuario {username}")
        messages.success(request, 'Usuario eliminado correctamente.')
        return redirect('panel_usuarios')

    return render(request, 'panel/eliminar_usuario.html', {'u': usuario})


@staff_required
def panel_precios(request):
    """Permite al administrador editar el precio por hora de cada cancha."""
    canchas = ConfigCancha.objects.order_by('nombre')

    if request.method == 'POST':
        for cancha in canchas:
            val = request.POST.get(f'precio_{cancha.pk}', '').strip()
            try:
                cancha.precio = max(0, int(val))
                cancha.save(update_fields=['precio'])
            except (ValueError, TypeError):
                pass
        registrar_accion(request.user, 'Actualizó precios de canchas')
        messages.success(request, 'Precios actualizados correctamente.')
        return redirect('panel_precios')

    return render(request, 'panel/precios.html', {'canchas': canchas})


@staff_required
def panel_datos_transferencia(request):
    """Vista para configurar los datos bancarios que ven los clientes al pagar."""
    datos, _ = DatosTransferencia.objects.get_or_create(id=1)

    if request.method == 'POST':
        datos.banco = request.POST.get('banco')
        datos.titular = request.POST.get('titular')
        datos.tipo_cuenta = request.POST.get('tipo_cuenta')
        datos.numero_cuenta = request.POST.get('numero_cuenta')
        datos.rut = request.POST.get('rut')
        datos.email = request.POST.get('email')
        datos.save()
        registrar_accion(request.user, "Actualizó datos bancarios", f"Banco: {datos.banco}")
        messages.success(request, 'Datos de transferencia actualizados correctamente.')
        return redirect('panel_datos_transferencia')

    return render(request, 'panel/datos_transferencia.html', {'datos': datos})


from django.core.paginator import Paginator


@staff_required
def panel_registro(request):
    """Muestra el historial de acciones (log de auditoría) para los administradores con paginación."""
    logs_list = Bitacora.objects.all().order_by('-creado_en')
    paginator = Paginator(logs_list, 10) # 10 registros por página
    
    page_number = request.GET.get('page')
    logs = paginator.get_page(page_number)
    
    return render(request, 'panel/registro.html', {'logs': logs})


@staff_required
def panel_bloqueos_periodo(request):
    """Lista todas las restricciones de periodo activas con su complejo y rango de fechas."""
    bloqueos = BloqueoPeriodo.objects.select_related('cancha', 'creado_por').order_by('fecha_inicio')
    return render(request, 'panel/bloqueos_periodo.html', {'bloqueos': bloqueos})


@staff_required
def panel_modificar_bloqueo_periodo(request, bloqueo_id):
    """Modifica la fecha_fin de un bloqueo de periodo existente."""
    if not request.user.is_superuser:
        messages.error(request, 'No tienes permisos para realizar esta acción.')
        return redirect('panel_bloqueos_periodo')

    bloqueo = get_object_or_404(BloqueoPeriodo, id=bloqueo_id)

    if request.method == 'POST':
        nueva_fecha_fin_str = request.POST.get('fecha_fin')
        try:
            nueva_fecha_fin = datetime.strptime(nueva_fecha_fin_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            messages.error(request, 'Fecha inválida.')
            return redirect('panel_bloqueos_periodo')

        if nueva_fecha_fin < bloqueo.fecha_inicio:
            messages.error(request, 'La nueva fecha de fin no puede ser anterior a la fecha de inicio.')
            return redirect('panel_bloqueos_periodo')

        # Verificar que el nuevo rango no se superponga con otro bloqueo distinto de la misma cancha
        solapamiento = BloqueoPeriodo.objects.filter(
            cancha=bloqueo.cancha,
            fecha_inicio__lte=nueva_fecha_fin,
            fecha_fin__gte=bloqueo.fecha_inicio,
        ).exclude(pk=bloqueo.pk).first()
        if solapamiento:
            fi = solapamiento.fecha_inicio.strftime("%d/%m/%Y")
            ff = solapamiento.fecha_fin.strftime("%d/%m/%Y")
            messages.error(request, f'La nueva fecha se superpone con otro período bloqueado ({fi} → {ff}).')
            return redirect('panel_bloqueos_periodo')

        fecha_fin_anterior = bloqueo.fecha_fin
        bloqueo.fecha_fin = nueva_fecha_fin
        bloqueo.save()
        registrar_accion(
            request.user,
            "Modificó período bloqueado",
            f"{bloqueo.cancha.nombre}: {bloqueo.fecha_inicio.strftime('%d/%m/%Y')} → {fecha_fin_anterior.strftime('%d/%m/%Y')} (nuevo fin: {nueva_fecha_fin.strftime('%d/%m/%Y')})"
        )
        messages.success(request, 'Fecha de fin actualizada correctamente.')

    return redirect('panel_bloqueos_periodo')


@staff_required
def panel_eliminar_bloqueo_periodo(request, bloqueo_id):
    """Elimina un bloqueo de periodo, liberando esas fechas nuevamente."""
    if not request.user.is_superuser:
        messages.error(request, 'No tienes permisos para realizar esta acción.')
        return redirect('panel_bloqueos_periodo')

    bloqueo = get_object_or_404(BloqueoPeriodo, id=bloqueo_id)

    if request.method == 'POST':
        detalle = f"{bloqueo.cancha.nombre}: {bloqueo.fecha_inicio.strftime('%d/%m/%Y')} → {bloqueo.fecha_fin.strftime('%d/%m/%Y')}"
        bloqueo.delete()
        registrar_accion(request.user, "Eliminó período bloqueado", detalle)
        messages.success(request, 'Restricción eliminada. Las fechas están disponibles nuevamente.')

    return redirect('panel_bloqueos_periodo')


@staff_required
def panel_historial_reservas(request):
    """Muestra un listado tabular de todas las reservas históricas con búsqueda, orden dinámico y filtros circulares."""
    sort_by = request.GET.get('sort', 'id')
    direction = request.GET.get('dir', 'desc')
    estado_filtro = request.GET.get('estado', '')
    pago_filtro = request.GET.get('pago', '')
    search_query = request.GET.get('q', '')
    
    # Secuencia circular para estados
    estados_ciclo = ['', 'PENDIENTE', 'APROBADA', 'RECHAZADA', 'CANCELADA', 'ELIMINADA']
    next_estado = estados_ciclo[(estados_ciclo.index(estado_filtro) + 1) % len(estados_ciclo)] if estado_filtro in estados_ciclo else 'PENDIENTE'

    # Secuencia circular para pagos
    pagos_ciclo = ['', 'TARJETA', 'TRANSFERENCIA', 'CUPON']
    next_pago = pagos_ciclo[(pagos_ciclo.index(pago_filtro) + 1) % len(pagos_ciclo)] if pago_filtro in pagos_ciclo else 'TARJETA'

    # Construcción de la Query
    qs = Reserva.objects.all()
    
    if search_query:
        qs = qs.filter(
            Q(id__icontains=search_query) |
            Q(solicitante__first_name__icontains=search_query) |
            Q(solicitante__last_name__icontains=search_query) |
            Q(solicitante__username__icontains=search_query) |
            Q(solicitante_nombre__icontains=search_query) |
            Q(solicitante_email__icontains=search_query) |
            Q(salon__nombre__icontains=search_query) |
            Q(cancha__nombre__icontains=search_query) |
            Q(estado__icontains=search_query) |
            Q(metodo_pago__icontains=search_query) |
            Q(fecha__icontains=search_query)
        )

    if estado_filtro:
        qs = qs.filter(estado=estado_filtro)
    if pago_filtro:
        qs = qs.filter(metodo_pago=pago_filtro)
    
    # Mapeo de campos permitidos para ordenamiento
    allowed_sorts = {'id': 'id', 'fecha': 'fecha'}
    field = allowed_sorts.get(sort_by, 'id')
    prefix = '-' if direction == 'desc' else ''
    
    if field == 'fecha':
        qs = qs.order_by(f"{prefix}fecha", f"{prefix}hora_inicio")
    else:
        qs = qs.order_by(f"{prefix}{field}")

    paginator = Paginator(qs, 20)
    page_number = request.GET.get('page')
    reservas = paginator.get_page(page_number)
    
    return render(request, 'panel/historial_reservas.html', {
        'reservas': reservas,
        'sort_by': sort_by,
        'direction': direction,
        'estado_filtro': estado_filtro,
        'next_estado': next_estado,
        'pago_filtro': pago_filtro,
        'next_pago': next_pago,
        'search_query': search_query
    })

def emitir_cupon(usuario, reserva, motivo):
    nombre_recurso = ''
    if reserva:
        nombre_recurso = reserva.salon.nombre if reserva.salon else (reserva.cancha.nombre if reserva.cancha else '')

    # Reintenta hasta 5 veces por si hay colisión de código en inserción concurrente
    for intento in range(5):
        try:
            cupon = Cupon.objects.create(
                codigo=Cupon.generar_codigo(),
                usuario=usuario,
                reserva_origen=reserva,
                nombre_recurso=nombre_recurso,
                motivo=motivo,
                fecha_expiracion=timezone.now().date() + timedelta(days=90),
            )
            break
        except IntegrityError:
            if intento == 4:
                raise
            continue

    if motivo == 'CANCELACION_USUARIO':
        motivo_txt = 'cancelaste tu reserva'
    else:
        motivo_txt = 'tu reserva fue cancelada por el sistema'

    Notificacion.objects.create(
        user=usuario,
        tipo='CUPON_EMITIDO',
        titulo='Cupón de reserva emitido',
        mensaje=(
            f'Dado que {motivo_txt} en {nombre_recurso}, se te emitió el cupón '
            f'{cupon.codigo} válido para una futura reserva. '
            f'Vence el {cupon.fecha_expiracion.strftime("%d/%m/%Y")}.'
        ),
    )
    return cupon


def registrar_accion(admin_user, accion, detalles=""):
    """Registra una acción en la bitácora administrativa."""
    return Bitacora.objects.create(
        admin=admin_user,
        accion=accion,
        detalles=detalles
    )

def crear_notificacion_reserva(user, tipo, titulo, mensaje, reserva=None):
    """Crea una notificación para un usuario."""
    return Notificacion.objects.create(
        user=user,
        tipo=tipo,
        titulo=titulo,
        mensaje=mensaje,
        reserva=reserva
    )

def notificar_admins(tipo, titulo, mensaje, reserva=None):
    """Crea una notificación para todos los administradores del sistema."""
    admins = User.objects.filter(Q(is_staff=True) | Q(is_superuser=True))
    for admin in admins:
        Notificacion.objects.create(
            user=admin,
            tipo=tipo,
            titulo=titulo,
            mensaje=mensaje,
            reserva=reserva,
        )
    return True


def notificar_alcaldes(tipo, titulo, mensaje, reserva=None):
    """Crea una notificación para todos los usuarios con perfil de alcalde."""
    alcaldes = User.objects.filter(perfil__es_alcalde=True)
    for alcalde in alcaldes:
        Notificacion.objects.create(
            user=alcalde,
            tipo=tipo,
            titulo=titulo,
            mensaje=mensaje,
            reserva=reserva,
        )
    return True


@login_required
def carta_solicitud_recinto(request, reserva_id):
    """Carta de solicitud de recinto con flujo de dos firmas: organización → alcalde."""
    reserva = get_object_or_404(Reserva, pk=reserva_id, tipo_reserva='ORGANIZACION')

    perfil = getattr(request.user, 'perfil', None)
    es_admin = request.user.is_staff or request.user.is_superuser
    es_alcalde = bool(perfil and perfil.es_alcalde)
    es_org_propia = bool(perfil and perfil.es_organizacion and reserva.solicitante == request.user)

    if not (es_admin or es_alcalde or es_org_propia):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    if request.method == 'POST':
        accion = request.POST.get('accion')

        if accion == 'firma_org' and (es_admin or es_org_propia) and not reserva.firma_org_en:
            reserva.firma_org_en = timezone.now()
            reserva.save(update_fields=['firma_org_en'])
            registrar_accion(
                request.user,
                'Firma de organización en carta',
                f'Reserva #{reserva.pk} — {reserva.nombre_organizacion} — '
                f'{reserva.fecha.strftime("%d/%m/%Y")} {reserva.hora_inicio.strftime("%H:%M")}',
            )
            notificar_alcaldes(
                'RESERVA_PENDIENTE',
                f'Carta firmada: {reserva.nombre_organizacion}',
                f'{reserva.nombre_organizacion} firmó la carta de solicitud para '
                f'{reserva.cancha.nombre if reserva.cancha else "el recinto"} el '
                f'{reserva.fecha.strftime("%d/%m/%Y")}. Pendiente de tu firma.',
                reserva=reserva,
            )
            messages.success(request, f'Documento firmado correctamente. La carta de {reserva.nombre_organizacion} fue enviada al alcalde.')
            if es_admin:
                return redirect('panel_reservas')
            return redirect('mis_reservas')

        elif accion == 'firma_alcalde' and es_alcalde and reserva.firma_org_en and not reserva.firma_alcalde_carta_en:
            reserva.firma_alcalde_carta_en = timezone.now()
            reserva.save(update_fields=['firma_alcalde_carta_en'])

        return redirect('carta_solicitud_recinto', reserva_id=reserva.pk)

    nombre_recinto = reserva.cancha.nombre if reserva.cancha else (reserva.salon.nombre if reserva.salon else '')
    modo_fin_dia = bool(reserva.cancha and reserva.hora_fin >= reserva.cancha.hora_fin)

    meses_es = {
        1: 'enero', 2: 'febrero', 3: 'marzo', 4: 'abril', 5: 'mayo', 6: 'junio',
        7: 'julio', 8: 'agosto', 9: 'septiembre', 10: 'octubre', 11: 'noviembre', 12: 'diciembre',
    }
    hoy = timezone.now()
    fecha_carta = f"{hoy.day} de {meses_es[hoy.month]} de {hoy.year}"

    return render(request, 'carta_solicitud_recinto.html', {
        'reserva': reserva,
        'nombre_recinto': nombre_recinto,
        'modo_fin_dia': modo_fin_dia,
        'fecha_carta': fecha_carta,
        'es_admin': es_admin,
        'es_alcalde': es_alcalde,
        'es_org_propia': es_org_propia,
    })


def _carta_pdf_cabecera():
    """Devuelve los flowables de la cabecera institucional (escudo | texto | logo)."""
    import os
    from django.conf import settings as djsettings
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle, Image as RLImage, Paragraph, Spacer
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT

    negro = colors.black
    escudo_path = os.path.join(djsettings.BASE_DIR, 'static', 'escudo_empedrado.jpeg')
    logo_path   = os.path.join(djsettings.BASE_DIR, 'static', 'logo_empedrado.jpeg')

    est_titulo = ParagraphStyle('cab_t', fontName='Helvetica-Bold', fontSize=8,   leading=11, textColor=negro, alignment=TA_LEFT)
    est_sub    = ParagraphStyle('cab_s', fontName='Helvetica',      fontSize=7.5, leading=11, textColor=negro, alignment=TA_LEFT)

    escudo_cell = RLImage(escudo_path, width=1.5*cm, height=1.8*cm) if os.path.exists(escudo_path) else Spacer(1.5*cm, 1.8*cm)

    texto_cell = [
        Paragraph('I. MUNICIPALIDAD DE EMPEDRADO', est_titulo),
        Paragraph('REGIÓN DEL MAULE - CHILE',       est_sub),
        Paragraph('ADMINISTRACIÓN MUNICIPAL',        est_sub),
    ]

    logo_cell = RLImage(logo_path, width=1.8*cm, height=1.8*cm) if os.path.exists(logo_path) else Spacer(1.8*cm, 1.8*cm)

    cabecera = Table([[escudo_cell, texto_cell, logo_cell]], colWidths=[1.8*cm, 9.2*cm, 5*cm])
    cabecera.setStyle(TableStyle([
        ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
        ('ALIGN',         (2,0), (2,0),   'RIGHT'),
        ('LEFTPADDING',   (1,0), (1,0),   6),
        ('RIGHTPADDING',  (1,0), (1,0),   0),
        ('LINEBELOW',     (0,0), (-1,-1), 0.5, negro),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))

    return [cabecera, Spacer(1, 0.6*cm)]


def carta_preview_pdf(request):
    """PDF de carta. Con reserva_id carga desde DB mostrando firmas reales.
    Sin reserva_id genera preview en blanco desde params GET."""
    if not request.user.is_authenticated:
        return redirect('login')

    from django.http import HttpResponse
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_RIGHT, TA_JUSTIFY, TA_CENTER
    import os

    perfil = getattr(request.user, 'perfil', None)
    es_admin    = request.user.is_staff or request.user.is_superuser
    es_alcalde  = bool(perfil and perfil.es_alcalde)
    es_tesorero = bool(perfil and perfil.es_tesorero)
    es_org      = bool(perfil and perfil.es_organizacion)
    if not (es_admin or es_alcalde or es_tesorero or es_org):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    # ── Fuente de datos ──
    reserva_id_str = request.GET.get('reserva_id')
    firma_org_en = None
    firma_alc_en = None

    if reserva_id_str:
        # Carga real desde DB con firmas
        try:
            reserva = Reserva.objects.get(pk=int(reserva_id_str), tipo_reserva='ORGANIZACION')
        except (Reserva.DoesNotExist, ValueError):
            from django.http import HttpResponseNotFound
            return HttpResponseNotFound()
        if not (es_admin or es_alcalde or es_tesorero or (es_org and reserva.solicitante == request.user)):
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden()
        nombre_org      = reserva.nombre_organizacion or ''
        rep_nombre      = reserva.representante_nombre or ''
        rep_rut         = reserva.representante_rut or ''
        motivo          = reserva.motivo or ''
        nombre_recinto  = (reserva.cancha.nombre if reserva.cancha else
                           reserva.salon.nombre if reserva.salon else 'el recinto municipal')
        fecha_reserva   = reserva.fecha.strftime("%d/%m/%Y")
        hora_inicio_str = reserva.hora_inicio.strftime("%H:%M")
        hora_fin_str    = reserva.hora_fin.strftime("%H:%M")
        modo_fin_dia    = bool(reserva.cancha and reserva.hora_fin >= reserva.cancha.hora_fin)
        firma_org_en    = reserva.firma_org_en
        firma_alc_en    = reserva.firma_alcalde_carta_en
    else:
        # Preview desde params GET (flujo crear reserva)
        fecha_str       = request.GET.get('fecha', '')
        hora_inicio_str = request.GET.get('hora_inicio', '')
        hora_fin_str    = request.GET.get('hora_fin', '')
        nombre_org      = request.GET.get('nombre_org', '')
        rep_nombre      = request.GET.get('rep_nombre', '')
        rep_rut         = request.GET.get('rep_rut', '')
        motivo          = request.GET.get('motivo', '')
        modo_fin_dia    = request.GET.get('modo_fin_dia') == '1'
        nombre_recinto  = request.GET.get('nombre_recinto', 'el recinto municipal')
        try:
            from datetime import datetime as dt
            fecha_reserva = dt.strptime(fecha_str, "%Y-%m-%d").date().strftime("%d/%m/%Y")
        except (ValueError, TypeError):
            fecha_reserva = fecha_str

    meses_es = {1:'enero',2:'febrero',3:'marzo',4:'abril',5:'mayo',6:'junio',
                7:'julio',8:'agosto',9:'septiembre',10:'octubre',11:'noviembre',12:'diciembre'}
    hoy = timezone.now()
    fecha_carta = f"{hoy.day} de {meses_es[hoy.month]} de {hoy.year}"

    bloque_horario = (
        f"desde las <b>{hora_inicio_str}</b> horas hasta el cierre del recinto"
        if modo_fin_dia else
        f"desde las <b>{hora_inicio_str}</b> hasta las <b>{hora_fin_str}</b> horas"
    )
    motivo_txt = motivo if motivo else '…………………………………………………………………………'
    cuerpo_txt = (
        f"Junto con saludarlos espero que se encuentren bien, vengo a solicitar recinto "
        f"específicamente <b>{nombre_recinto}</b>, el día <b>{fecha_reserva}</b> {bloque_horario}, "
        f"con motivo de <b>{motivo_txt}</b>, por lo cual nos comprometemos a entregar el "
        f"recinto en las condiciones adecuadas y limpia posterior al uso de nuestro evento."
    )

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'inline; filename="carta.pdf"'
    doc = SimpleDocTemplate(
        response, pagesize=LETTER,
        leftMargin=3*cm, rightMargin=3*cm, topMargin=2*cm, bottomMargin=3*cm,
    )

    azul      = colors.HexColor('#1a3a6b')
    negro     = colors.black
    gris      = colors.HexColor('#6b7280')
    gris_claro = colors.HexColor('#9ca3af')

    base        = ParagraphStyle('base',  fontName='Times-Roman',  fontSize=11, leading=18, textColor=negro)
    est_fecha   = ParagraphStyle('fecha', parent=base, alignment=TA_RIGHT, fontSize=11)
    est_dest    = ParagraphStyle('dest',  parent=base, fontName='Times-Bold', textColor=azul, fontSize=11, leading=17)
    est_cuerpo  = ParagraphStyle('cuerpo',parent=base, alignment=TA_JUSTIFY, firstLineIndent=42, fontSize=11, leading=20)
    est_fn      = ParagraphStyle('fn',    fontName='Times-Bold',   fontSize=10, leading=14, alignment=TA_CENTER, textColor=negro)
    est_fs      = ParagraphStyle('fs',    fontName='Times-Roman',  fontSize=9,  leading=13, alignment=TA_CENTER, textColor=gris)
    est_fn_gris = ParagraphStyle('fng',   fontName='Times-Roman',  fontSize=10, leading=14, alignment=TA_CENTER, textColor=gris_claro)
    est_fs_gris = ParagraphStyle('fsg',   fontName='Times-Roman',  fontSize=9,  leading=13, alignment=TA_CENTER, textColor=gris_claro)

    sello_path = os.path.join(settings.BASE_DIR, 'static', 'sello_municipalidad.jpeg')
    sello_ok   = os.path.exists(sello_path)

    def _bloque(firmado, fecha_firma, n1, n2, n3):
        items = []
        t = Table([['']], colWidths=[7*cm])
        if firmado:
            items.append(RLImage(sello_path, width=2.8*cm, height=2.8*cm) if sello_ok else Spacer(1, 2.8*cm))
            items.append(Spacer(1, 4))
            t.setStyle(TableStyle([('LINEABOVE', (0,0), (-1,-1), 0.8, negro)]))
            items += [t, Spacer(1, 5), Paragraph(n1, est_fn)]
            if n2: items.append(Paragraph(n2, est_fs))
            if n3: items.append(Paragraph(n3, est_fs))
            if fecha_firma:
                items += [Spacer(1, 3), Paragraph(
                    f"Firmado el {fecha_firma.strftime('%d/%m/%Y a las %H:%M')}h", est_fs_gris)]
        else:
            t.setStyle(TableStyle([('LINEABOVE', (0,0), (-1,-1), 0.6, gris_claro)]))
            items += [Spacer(1, 2.2*cm), t, Spacer(1, 5), Paragraph(n1, est_fn_gris)]
            if n2: items.append(Paragraph(n2, est_fs_gris))
            if n3: items.append(Paragraph(n3, est_fs_gris))
        return items

    rut_rep   = f"RUT: {rep_rut}" if rep_rut else ''
    org_items = _bloque(bool(firma_org_en), firma_org_en,
                        rep_nombre or 'Representante Legal', rut_rep, nombre_org)
    alc_items = _bloque(bool(firma_alc_en), firma_alc_en,
                        'Carlos Correa Miños', 'Alcalde', 'I. Municipalidad de Empedrado')

    firma_tabla = Table([[org_items, alc_items]], colWidths=[8*cm, 8*cm])
    firma_tabla.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'BOTTOM'),
        ('ALIGN',  (0,0), (-1,-1), 'CENTER'),
    ]))

    story = _carta_pdf_cabecera()
    story += [
        Paragraph(f"Empedrado, {fecha_carta}", est_fecha),
        Spacer(1, 0.8*cm),
        Paragraph("SR. CARLOS CORREA MIÑOS", est_dest),
        Paragraph("ALCALDE", est_dest),
        Paragraph("ILUSTRE MUNICIPALIDAD DE EMPEDRADO", est_dest),
        Spacer(1, 0.9*cm),
        Paragraph(cuerpo_txt, est_cuerpo),
        Spacer(1, 0.5*cm),
        Paragraph("Sin más que agregar y esperando una positiva respuesta le saluda atte.", est_cuerpo),
        Spacer(1, 1.4*cm),
        firma_tabla,
    ]
    doc.build(story)
    return response


def carta_solicitud_pdf(request, reserva_id):
    """Genera y descarga la carta de solicitud de recinto como PDF usando ReportLab."""
    if not request.user.is_authenticated:
        return redirect('login')

    from django.http import HttpResponse
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage,
    )
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.enums import TA_RIGHT, TA_LEFT, TA_CENTER, TA_JUSTIFY
    import os

    reserva = get_object_or_404(Reserva, pk=reserva_id, tipo_reserva='ORGANIZACION')

    perfil = getattr(request.user, 'perfil', None)
    es_admin = request.user.is_staff or request.user.is_superuser
    es_alcalde = bool(perfil and perfil.es_alcalde)
    es_org_propia = bool(perfil and perfil.es_organizacion and reserva.solicitante == request.user)
    if not (es_admin or es_alcalde or es_org_propia):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    # ── Respuesta HTTP ──
    response = HttpResponse(content_type='application/pdf')
    nombre_archivo = f"carta_solicitud_{reserva.nombre_organizacion.replace(' ', '_')}_{reserva.fecha}.pdf"
    response['Content-Disposition'] = f'inline; filename="{nombre_archivo}"'

    # ── Documento ──
    doc = SimpleDocTemplate(
        response,
        pagesize=LETTER,
        leftMargin=3 * cm, rightMargin=3 * cm,
        topMargin=2 * cm, bottomMargin=3 * cm,
    )

    # ── Estilos ──
    azul = colors.HexColor('#1a3a6b')
    negro = colors.black
    gris = colors.HexColor('#6b7280')

    base = ParagraphStyle('base', fontName='Times-Roman', fontSize=11, leading=18, textColor=negro)
    est_fecha = ParagraphStyle('fecha', parent=base, alignment=TA_RIGHT, textColor=negro, fontSize=11)
    est_dest = ParagraphStyle('dest', parent=base, fontName='Times-Bold', textColor=azul, fontSize=11, leading=17)
    est_cuerpo = ParagraphStyle('cuerpo', parent=base, alignment=TA_JUSTIFY, firstLineIndent=42, fontSize=11, leading=20)
    est_firma_nombre = ParagraphStyle('firma_n', fontName='Times-Bold', fontSize=10, leading=14, alignment=TA_CENTER, textColor=negro)
    est_firma_sub = ParagraphStyle('firma_s', fontName='Times-Roman', fontSize=9, leading=13, alignment=TA_CENTER, textColor=gris)

    # ── Contenido ──
    meses_es = {1:'enero',2:'febrero',3:'marzo',4:'abril',5:'mayo',6:'junio',
                7:'julio',8:'agosto',9:'septiembre',10:'octubre',11:'noviembre',12:'diciembre'}
    hoy = timezone.now()
    fecha_carta = f"{hoy.day} de {meses_es[hoy.month]} de {hoy.year}"

    nombre_recinto = (reserva.cancha.nombre if reserva.cancha else
                      reserva.salon.nombre if reserva.salon else 'el recinto municipal')
    modo_fin_dia = bool(reserva.cancha and reserva.hora_fin >= reserva.cancha.hora_fin)
    fecha_reserva = reserva.fecha.strftime("%d/%m/%Y")
    hora_ini = reserva.hora_inicio.strftime("%H:%M")
    hora_fin_str = reserva.hora_fin.strftime("%H:%M")

    if modo_fin_dia:
        bloque_horario = f"desde las <b>{hora_ini}</b> horas hasta el cierre del recinto"
    else:
        bloque_horario = f"desde las <b>{hora_ini}</b> hasta las <b>{hora_fin_str}</b> horas"

    motivo_txt = reserva.motivo if reserva.motivo else '…………………………………………………………………………'

    cuerpo_txt = (
        f"Junto con saludarlos espero que se encuentren bien, vengo a solicitar recinto "
        f"específicamente <b>{nombre_recinto}</b>, el día <b>{fecha_reserva}</b> {bloque_horario}, "
        f"con motivo de <b>{motivo_txt}</b>, por lo cual nos comprometemos a entregar el "
        f"recinto en las condiciones adecuadas y limpia posterior al uso de nuestro evento."
    )
    cierre_txt = "Sin más que agregar y esperando una positiva respuesta le saluda atte."

    # Ruta del sello
    sello_path = os.path.join(settings.BASE_DIR, 'static', 'sello_municipalidad.jpeg')
    sello_disponible = os.path.exists(sello_path)

    def _bloque_firma(firmado, nombre_linea1, nombre_linea2, nombre_linea3, fecha_firma=None):
        """Devuelve una lista de flowables para un bloque de firma."""
        items = []
        if firmado and sello_disponible:
            img = RLImage(sello_path, width=2.8 * cm, height=2.8 * cm)
            items.append(img)
            items.append(Spacer(1, 4))
        else:
            items.append(Spacer(1, 2.8 * cm + 4))

        # Línea de firma (simulada con tabla de 1 celda con borde superior)
        t = Table([['']], colWidths=[7 * cm])
        t.setStyle(TableStyle([('LINEABOVE', (0, 0), (-1, -1), 0.8, negro)]))
        items.append(t)
        items.append(Spacer(1, 5))
        items.append(Paragraph(nombre_linea1, est_firma_nombre))
        if nombre_linea2:
            items.append(Paragraph(nombre_linea2, est_firma_sub))
        if nombre_linea3:
            items.append(Paragraph(nombre_linea3, est_firma_sub))
        if fecha_firma:
            fecha_fmt = fecha_firma.strftime("%d/%m/%Y a las %H:%M") + "h"
            items.append(Spacer(1, 3))
            items.append(Paragraph(f"Firmado el {fecha_fmt}", est_firma_sub))
        return items

    # Firma org
    rut_rep = f"RUT: {reserva.representante_rut}" if reserva.representante_rut else ''
    firma_org_items = _bloque_firma(
        firmado=bool(reserva.firma_org_en),
        nombre_linea1=reserva.representante_nombre or 'Representante Legal',
        nombre_linea2=rut_rep,
        nombre_linea3=reserva.nombre_organizacion,
        fecha_firma=reserva.firma_org_en,
    )

    # Firma alcalde
    firma_alc_items = _bloque_firma(
        firmado=bool(reserva.firma_alcalde_carta_en),
        nombre_linea1='Carlos Correa Miños',
        nombre_linea2='Alcalde',
        nombre_linea3='I. Municipalidad de Empedrado',
        fecha_firma=reserva.firma_alcalde_carta_en,
    )

    # Tabla de dos firmas lado a lado
    firma_tabla = Table(
        [[firma_org_items, firma_alc_items]],
        colWidths=[8 * cm, 8 * cm],
    )
    firma_tabla.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'BOTTOM'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ]))

    story = _carta_pdf_cabecera()
    story += [
        Paragraph(f"Empedrado, {fecha_carta}", est_fecha),
        Spacer(1, 0.8 * cm),
        Paragraph("SR. CARLOS CORREA MIÑOS", est_dest),
        Paragraph("ALCALDE", est_dest),
        Paragraph("ILUSTRE MUNICIPALIDAD DE EMPEDRADO", est_dest),
        Spacer(1, 0.9 * cm),
        Paragraph(cuerpo_txt, est_cuerpo),
        Spacer(1, 0.5 * cm),
        Paragraph(cierre_txt, est_cuerpo),
        Spacer(1, 1.4 * cm),
        firma_tabla,
    ]

    doc.build(story)
    return response
