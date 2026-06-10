from django.shortcuts import redirect


RUTAS_PERMITIDAS_TESORERO = ('/tesorero/', '/media/', '/logout/', '/accounts/logout/')
RUTAS_PERMITIDAS_ALCALDE  = ('/alcalde/',  '/media/', '/logout/', '/accounts/logout/')


class RolRestringidoMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated:
            perfil = getattr(user, 'perfil', None)
            path = request.path

            if perfil and perfil.es_tesorero:
                if not any(path.startswith(r) for r in RUTAS_PERMITIDAS_TESORERO):
                    return redirect('perfil_tesorero')

            elif perfil and perfil.es_alcalde:
                if not any(path.startswith(r) for r in RUTAS_PERMITIDAS_ALCALDE):
                    return redirect('perfil_alcalde')

        return self.get_response(request)
