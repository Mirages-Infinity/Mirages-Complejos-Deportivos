from django.conf import settings

# Orígenes externos permitidos: imágenes subidas (Cloudinary) y la pasarela de pago (Transbank).
# El resto del sitio (scripts, estilos, fuentes) se sirve desde el propio dominio.
CSP = '; '.join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob: https://res.cloudinary.com",
    "font-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "frame-ancestors 'none'",
    "form-action 'self' https://*.transbank.cl",
])

PERMISSIONS_POLICY = 'camera=(), microphone=(), geolocation=(), payment=(), usb=()'


class SecurityHeadersMiddleware:
    """Agrega CSP y Permissions-Policy a las páginas HTML (no a PDF ni JSON)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.get('Content-Type', '').startswith('text/html'):
            header = 'Content-Security-Policy-Report-Only' if settings.CSP_REPORT_ONLY else 'Content-Security-Policy'
            response.setdefault(header, CSP)
            response.setdefault('Permissions-Policy', PERMISSIONS_POLICY)
        return response
