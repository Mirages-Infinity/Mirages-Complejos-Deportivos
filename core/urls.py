"""
URL configuration for core project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from reservas import views
from core.views import deploy_webhook, service_worker

urlpatterns = [
    path('admin/', admin.site.urls),
    path('deploy/webhook/', deploy_webhook, name='deploy_webhook'),
    path('sw.js', service_worker, name='service_worker'),
    path('', views.inicio, name='inicio'),
    path('registro/', views.registro, name='registro'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('recuperar-clave/', views.recuperar_clave, name='recuperar_clave'),
    path('resetear-clave/<uidb64>/<token>/', views.resetear_clave, name='resetear_clave'),

    # Reservas
    path('reservas/', views.mis_reservas, name='mis_reservas'),
    path('reservas/nueva/<int:salon_id>/', views.crear_reserva, name='crear_reserva'),
    path('reservas/<int:reserva_id>/editar/', views.editar_mi_reserva, name='editar_mi_reserva'),
    path('reservas/<int:reserva_id>/editar-org/', views.editar_reserva_organizacion, name='editar_reserva_organizacion'),
    path('reservas/<int:reserva_id>/cancelar/', views.cancelar_mi_reserva, name='cancelar_mi_reserva'),
    path('reservas/<int:reserva_id>/gestionar/', views.gestionar_reserva, name='gestionar_reserva'),
    
    path('reserva-tenis/', views.reserva_tenis, name='reserva_tenis'),
    path('reserva-tenis/toggle-bloqueo/', views.toggle_bloqueo, name='toggle_bloqueo'),
    path('canchas/notificar-bloqueos/', views.notificar_bloqueos_batch, name='notificar_bloqueos_batch'),
    path('canchas/bloquear-periodo/', views.bloquear_periodo, name='bloquear_periodo'),
    path('canchas/check-periodo/', views.check_periodo_bloqueado, name='check_periodo_bloqueado'),
    path('canchas/check-horario/', views.check_bloqueo_horario, name='check_bloqueo_horario'),
    path('canchas/check-reservada/', views.check_hora_reservada, name='check_hora_reservada'),
    path('config-cancha/toggle-status/', views.toggle_cancha_status, name='toggle_cancha_status'),
    path('config-cancha/actualizar-horario/', views.actualizar_horario_cancha, name='actualizar_horario_cancha'),
    path('config-cancha/toggle-findes/', views.toggle_findes_semana_cancha, name='toggle_findes_semana_cancha'),
    path('canchas/admin/crear-reserva/', views.crear_reserva_admin, name='crear_reserva_admin'),
    path('canchas/admin/crear-reserva/organizacion/', views.crear_reserva_organizacion, name='crear_reserva_organizacion'),
    path('reserva/<int:reserva_id>/carta/', views.carta_solicitud_recinto, name='carta_solicitud_recinto'),
    path('reserva/<int:reserva_id>/carta/pdf/', views.carta_solicitud_pdf, name='carta_solicitud_pdf'),
    path('carta/preview-pdf/', views.carta_preview_pdf, name='carta_preview_pdf'),
    path('reserva-tenis/finalizar/', views.finalizar_reserva_tenis, name='finalizar_reserva_tenis'),

    path('reserva-gimnasio/', views.reserva_gimnasio, name='reserva_gimnasio'),
    path('reserva-gimnasio/finalizar/', views.finalizar_reserva_gimnasio, name='finalizar_reserva_gimnasio'),

    path('reserva-futbolito/', views.reserva_futbolito, name='reserva_futbolito'),
    path('reserva-futbolito/finalizar/', views.finalizar_reserva_futbolito, name='finalizar_reserva_futbolito'),

    path('reserva-estadio/', views.reserva_estadio, name='reserva_estadio'),
    path('reserva-estadio/finalizar/', views.finalizar_reserva_estadio, name='finalizar_reserva_estadio'),

    # Panel de administración
    path('panel/', views.panel_inicio, name='panel_inicio'),
    path('panel/reservas/', views.panel_reservas, name='panel_reservas'),
    path('panel/reservas/<int:reserva_id>/cancelar/', views.panel_cancelar_reserva, name='panel_cancelar_reserva'),
    path('panel/reservas/<int:reserva_id>/editar/', views.panel_editar_reserva, name='panel_editar_reserva'),
    path('panel/reservas/<int:reserva_id>/eliminar/', views.panel_eliminar_reserva, name='panel_eliminar_reserva'),
    path('panel/usuarios/', views.panel_usuarios, name='panel_usuarios'),
    path('panel/usuarios/crear/', views.panel_crear_usuario, name='panel_crear_usuario'),
    path('panel/usuarios/<int:usuario_id>/editar/', views.panel_editar_usuario, name='panel_editar_usuario'),
    path('panel/usuarios/<int:usuario_id>/toggle/', views.panel_toggle_usuario, name='panel_toggle_usuario'),
    path('panel/usuarios/<int:usuario_id>/eliminar/', views.panel_eliminar_usuario, name='panel_eliminar_usuario'),
    path('panel/precios/', views.panel_precios, name='panel_precios'),
    path('panel/datos-transferencia/', views.panel_datos_transferencia, name='panel_datos_transferencia'),
    path('panel/registro/', views.panel_registro, name='panel_registro'),
    path('panel/bloqueos-periodo/', views.panel_bloqueos_periodo, name='panel_bloqueos_periodo'),
    path('panel/bloqueos-periodo/<int:bloqueo_id>/modificar/', views.panel_modificar_bloqueo_periodo, name='panel_modificar_bloqueo_periodo'),
    path('panel/bloqueos-periodo/<int:bloqueo_id>/eliminar/', views.panel_eliminar_bloqueo_periodo, name='panel_eliminar_bloqueo_periodo'),
    path('panel/historial-reservas/', views.panel_historial_reservas, name='panel_historial_reservas'),
    path('panel/cupones/', views.panel_cupones, name='panel_cupones'),
    path('panel/cupones/crear-reserva/', views.panel_crear_cupon_reserva, name='panel_crear_cupon_reserva'),

    # Alcalde
    path('alcalde/', views.perfil_alcalde, name='perfil_alcalde'),
    path('alcalde/reserva/<int:reserva_id>/', views.alcalde_detalle_reserva, name='alcalde_detalle_reserva'),
    path('alcalde/reserva/<int:reserva_id>/firmar/', views.alcalde_firmar_reserva, name='alcalde_firmar_reserva'),
    path('alcalde/reserva/<int:reserva_id>/rechazar/', views.alcalde_rechazar_reserva, name='alcalde_rechazar_reserva'),

    # Tesorero
    path('tesorero/', views.perfil_tesorero, name='perfil_tesorero'),
    path('tesorero/registro/<int:registro_id>/estado/', views.tesorero_actualizar_estado, name='tesorero_actualizar_estado'),

    # Perfil
    path('perfil/', views.perfil, name='perfil'),
    path('perfil/mis-datos/', views.mis_datos, name='mis_datos'),
    path('perfil/cambiar-email/', views.cambiar_email, name='cambiar_email'),
    path('perfil/confirmar-email/<str:token>/', views.confirmar_cambio_email, name='confirmar_cambio_email'),
    path('perfil/cambiar-contrasena/', views.cambiar_contrasena, name='cambiar_contrasena'),
    path('perfil/mis-tarjetas/', views.mis_tarjetas, name='mis_tarjetas'),
    path('perfil/notificaciones/', views.notificaciones, name='notificaciones'),
    path('perfil/mis-cupones/', views.mis_cupones, name='mis_cupones'),
    path('perfil/validar-cupon/', views.validar_cupon, name='validar_cupon'),

    # Webpay Plus
    path('webpay/iniciar/<int:reserva_id>/', views.webpay_iniciar, name='webpay_iniciar'),
    path('webpay/retorno/', views.webpay_retorno, name='webpay_retorno'),
    path('panel/configuracion-webpay/', views.panel_configuracion_webpay, name='panel_configuracion_webpay'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
