from .models import Notificacion
from .permisos import es_admin, es_encargado, es_gestion


def notificaciones_context(request):
    if request.user.is_authenticated:
        return {
            'es_gestion': es_gestion(request.user),
            'es_admin_total': es_admin(request.user),
            'es_encargado': es_encargado(request.user),
            'notificaciones_sin_leer': Notificacion.objects.filter(
                user=request.user,
                leida=False,
            ).count()
        }
    return {'notificaciones_sin_leer': 0, 'es_gestion': False, 'es_admin_total': False, 'es_encargado': False}
