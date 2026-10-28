from pathlib import Path
from decouple import config, Csv

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config('SECRET_KEY')

DEBUG = config('DEBUG', default=False, cast=bool)

ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost,127.0.0.1', cast=Csv())

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'cloudinary_storage',
    'cloudinary',
    'reservas',
    'complejos',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'reservas.middleware.RolRestringidoMiddleware',
]

ROOT_URLCONF = 'core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'reservas.context_processors.notificaciones_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'es-cl'
TIME_ZONE = 'America/Santiago'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# --- Almacenamiento de archivos subidos ---
# Si hay credenciales de Cloudinary en el entorno, las imágenes que suban los
# usuarios (recintos, comprobantes, etc.) se guardan ahí en vez del disco
# local — útil en PythonAnywhere free, que solo da 512 MB de cuota.
# Sin esas variables (ej. en desarrollo local), se usa el disco normal.
CLOUDINARY_CLOUD_NAME = config('CLOUDINARY_CLOUD_NAME', default='')

if CLOUDINARY_CLOUD_NAME:
    CLOUDINARY_STORAGE = {
        'CLOUD_NAME': CLOUDINARY_CLOUD_NAME,
        'API_KEY': config('CLOUDINARY_API_KEY'),
        'API_SECRET': config('CLOUDINARY_API_SECRET'),
    }
    STORAGES = {
        'default': {
            'BACKEND': 'cloudinary_storage.storage.MediaCloudinaryStorage',
        },
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    }

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Email — configurable por entorno
# En desarrollo: usar 'django.core.mail.backends.console.EmailBackend' (imprime los correos)
# En producción: 'django.core.mail.backends.smtp.EmailBackend' + credenciales Gmail
EMAIL_BACKEND = config(
    'EMAIL_BACKEND',
    default='django.core.mail.backends.console.EmailBackend',
)
EMAIL_HOST = config('EMAIL_HOST', default='smtp.gmail.com')
EMAIL_PORT = config('EMAIL_PORT', default=587, cast=int)
EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=True, cast=bool)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = config(
    'DEFAULT_FROM_EMAIL',
    default=f'Sistema de Reservas IME <{EMAIL_HOST_USER or "noreply@localhost"}>',
)

# Límite de tamaño de archivos subidos (5 MB)
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# Cantidad máxima de superusuarios permitidos
MAX_SUPERUSUARIOS = 3

# ─── Seguridad HTTP (producción) ─────────────────────────────────────────────
# Estas opciones solo aplican cuando DEBUG=False (producción con HTTPS).
if not DEBUG:
    SECURE_SSL_REDIRECT = True                 # Redirige HTTP → HTTPS automáticamente
    SECURE_HSTS_SECONDS = 31536000             # 1 año de HSTS
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True               # Cookie de sesión solo por HTTPS
    CSRF_COOKIE_SECURE = True                  # Cookie CSRF solo por HTTPS
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'

# IPs de proxies de confianza para el rate limiter (dejar vacío si no hay proxy)
# Ejemplo con nginx local: TRUSTED_PROXY_IPS = ['127.0.0.1']
TRUSTED_PROXY_IPS = config('TRUSTED_PROXY_IPS', default='', cast=Csv())

# --- Despliegue automatico (webhook de GitHub) ---
# Si se define, habilita /deploy/webhook/: GitHub llama esa URL en cada push,
# el servidor valida la firma con este secreto, hace git pull y se recarga solo.
# Vacio en desarrollo local (el webhook queda deshabilitado).
GITHUB_WEBHOOK_SECRET = config('GITHUB_WEBHOOK_SECRET', default='')

_primary_host = ALLOWED_HOSTS[0] if ALLOWED_HOSTS else ''
WSGI_RELOAD_FILE = config(
    'WSGI_RELOAD_FILE',
    default=f'/var/www/{_primary_host.replace(".", "_")}_wsgi.py' if _primary_host else '',
)
WSGI_RELOAD_DOMAIN = config('WSGI_RELOAD_DOMAIN', default=_primary_host)

# API de PythonAnywhere (https://www.pythonanywhere.com/account/#api_token)
# Permite pedir la recarga de la app por codigo, ya que el propio proceso
# web no tiene permiso para tocar archivos fuera de su proyecto.
PYTHONANYWHERE_API_TOKEN = config('PYTHONANYWHERE_API_TOKEN', default='')
PYTHONANYWHERE_USERNAME = config('PYTHONANYWHERE_USERNAME', default='')
