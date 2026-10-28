import hashlib
import hmac
import os
import subprocess
import sys

import requests
from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST


def service_worker(request):
    """Sirve el service worker en la raiz (/sw.js) para que su alcance
    cubra todo el sitio y no solo /static/, que es donde vive el archivo."""
    sw_path = settings.BASE_DIR / 'static' / 'sw.js'
    with open(sw_path, 'r', encoding='utf-8') as f:
        contenido = f.read()
    response = HttpResponse(contenido, content_type='application/javascript')
    response['Service-Worker-Allowed'] = '/'
    return response


@csrf_exempt
@require_POST
def deploy_webhook(request):
    """Recibe el webhook de push de GitHub, hace git pull y recarga la app."""
    secret = settings.GITHUB_WEBHOOK_SECRET
    if not secret:
        return HttpResponseForbidden('Webhook no configurado en el servidor')

    signature = request.headers.get('X-Hub-Signature-256', '')
    expected = 'sha256=' + hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return HttpResponseForbidden('Firma invalida')

    pull = subprocess.run(
        ['git', 'pull'],
        cwd=settings.BASE_DIR,
        capture_output=True,
        text=True,
        timeout=60,
    )

    collectstatic = None
    if pull.returncode == 0:
        collectstatic = subprocess.run(
            [sys.executable, 'manage.py', 'collectstatic', '--noinput'],
            cwd=settings.BASE_DIR,
            capture_output=True,
            text=True,
            timeout=60,
        )

    # Intento 1 (preferido): API oficial de PythonAnywhere. El proceso de la
    # app web corre en un entorno restringido que normalmente no puede
    # tocar archivos fuera de su propio proyecto, asi que esta es la forma
    # confiable de pedir la recarga.
    reloaded = False
    reload_detail = None
    if pull.returncode == 0 and settings.PYTHONANYWHERE_API_TOKEN and settings.PYTHONANYWHERE_USERNAME:
        api_url = (
            f'https://www.pythonanywhere.com/api/v0/user/'
            f'{settings.PYTHONANYWHERE_USERNAME}/webapps/{settings.WSGI_RELOAD_DOMAIN}/reload/'
        )
        try:
            resp = requests.post(
                api_url,
                headers={'Authorization': f'Token {settings.PYTHONANYWHERE_API_TOKEN}'},
                timeout=30,
            )
            reloaded = resp.status_code == 200
            reload_detail = f'{resp.status_code}: {resp.text[:300]}'
        except requests.RequestException as exc:
            reload_detail = str(exc)

    # Intento 2 (respaldo, puede no tener permisos): tocar el archivo WSGI.
    if not reloaded:
        wsgi_file = settings.WSGI_RELOAD_FILE
        if pull.returncode == 0 and wsgi_file and os.path.exists(wsgi_file):
            try:
                os.utime(wsgi_file, None)
                reloaded = True
            except OSError as exc:
                reload_detail = (reload_detail or '') + f' | touch wsgi fallo: {exc}'

    return JsonResponse({
        'ok': pull.returncode == 0 and (collectstatic is None or collectstatic.returncode == 0),
        'reloaded': reloaded,
        'reload_detail': reload_detail,
        'pull_stdout': pull.stdout,
        'pull_stderr': pull.stderr,
        'collectstatic_stdout': collectstatic.stdout if collectstatic else None,
        'collectstatic_stderr': collectstatic.stderr if collectstatic else None,
    })
