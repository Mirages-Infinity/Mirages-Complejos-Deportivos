import hashlib
import hmac
import os
import subprocess

from django.conf import settings
from django.http import HttpResponseForbidden, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST


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

    wsgi_file = settings.WSGI_RELOAD_FILE
    reloaded = False
    if pull.returncode == 0 and wsgi_file and os.path.exists(wsgi_file):
        os.utime(wsgi_file, None)
        reloaded = True

    return JsonResponse({
        'ok': pull.returncode == 0,
        'reloaded': reloaded,
        'stdout': pull.stdout,
        'stderr': pull.stderr,
    })
