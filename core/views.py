import hashlib
import hmac
import os
import subprocess
import sys

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

    wsgi_file = settings.WSGI_RELOAD_FILE
    reloaded = False
    if pull.returncode == 0 and wsgi_file and os.path.exists(wsgi_file):
        os.utime(wsgi_file, None)
        reloaded = True

    return JsonResponse({
        'ok': pull.returncode == 0 and (collectstatic is None or collectstatic.returncode == 0),
        'reloaded': reloaded,
        'pull_stdout': pull.stdout,
        'pull_stderr': pull.stderr,
        'collectstatic_stdout': collectstatic.stdout if collectstatic else None,
        'collectstatic_stderr': collectstatic.stderr if collectstatic else None,
    })
