from django.contrib.auth.models import User
from django.db.models import Q


def es_encargado(user):
    perfil = getattr(user, 'perfil', None) if user.is_authenticated else None
    return bool(perfil and perfil.es_encargado and user.is_active)


def es_admin(user):
    return bool(user.is_authenticated and (user.is_staff or user.is_superuser))


def es_gestion(user):
    """Administrador o encargado: puede gestionar reservas y horarios de los complejos."""
    return es_admin(user) or es_encargado(user)


def usuarios_gestion():
    """Cuentas que reciben avisos de gestión (administradores y encargados)."""
    return User.objects.filter(Q(is_staff=True) | Q(perfil__es_encargado=True), is_active=True).distinct()
