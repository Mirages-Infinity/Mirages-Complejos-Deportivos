from .models import Notificacion


def notificaciones_context(request):
    if request.user.is_authenticated:
        return {
            'notificaciones_sin_leer': Notificacion.objects.filter(
                user=request.user,
                leida=False,
            ).count()
        }
    return {'notificaciones_sin_leer': 0}
