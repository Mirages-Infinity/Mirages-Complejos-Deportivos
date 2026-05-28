# Complejos Deportivos

Sistema web de gestión de reservas de recintos deportivos, construido con Django.

Permite a los usuarios reservar canchas por bloques horarios, subir comprobantes de transferencia, recibir notificaciones por correo, y a los administradores gestionar complejos, canchas, bloqueos y métodos de pago.

## Stack

- **Backend:** Django 5.2 (LTS)
- **Base de datos:** SQLite (desarrollo)
- **CSS:** Tailwind CSS v4 (CLI standalone)
- **Imágenes:** Pillow
- **Configuración:** python-decouple (`.env`)

## Requisitos previos

- Python 3.11 o superior
- [Tailwind CSS CLI standalone](https://tailwindcss.com/blog/standalone-cli) en el `PATH` (solo si quieres regenerar el CSS; el archivo `output.css` se genera en build)
- Una cuenta de Gmail con [App Password](https://support.google.com/accounts/answer/185833) si deseas envío real de correos

## Instalación rápida (Windows)

1. Clona el repositorio:

   ```bash
   git clone https://github.com/<tu-usuario>/complejos_deportivos.git
   cd complejos_deportivos
   ```

2. Copia las variables de entorno de ejemplo y edítalas:

   ```bash
   copy .env.example .env
   ```

   Luego abre `.env` y completa los valores (ver sección [Variables de entorno](#variables-de-entorno)).

3. Ejecuta el script de arranque:

   ```bash
   iniciar.bat
   ```

   La primera vez creará el entorno virtual, instalará las dependencias desde `requirements.txt` y lanzará Tailwind + Django.

4. Aplica las migraciones y carga datos iniciales (en otra terminal, con el venv activado):

   ```bash
   venv\Scripts\activate
   python manage.py migrate
   python manage.py loaddata fixtures/inicial.json
   python manage.py createsuperuser
   ```

5. Abre el navegador en [http://127.0.0.1:8000](http://127.0.0.1:8000).

## Instalación manual (cualquier SO)

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# Linux / Mac
source venv/bin/activate

pip install -r requirements.txt
python manage.py migrate
python manage.py loaddata fixtures/inicial.json
python manage.py createsuperuser
python manage.py runserver
```

Para regenerar el CSS de Tailwind (necesario si editas plantillas o `input.css`):

```bash
tailwindcss -i static/css/input.css -o static/css/output.css --watch
```

## Variables de entorno

El proyecto usa `python-decouple` para leer `.env`. Copia `.env.example` a `.env` y completa:

| Variable | Descripción | Ejemplo |
|---|---|---|
| `SECRET_KEY` | Clave secreta de Django. Genera una con `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"` | `django-insecure-...` |
| `DEBUG` | `True` en desarrollo, `False` en producción | `True` |
| `ALLOWED_HOSTS` | Lista separada por comas | `localhost,127.0.0.1` |
| `EMAIL_BACKEND` | Backend de correo. En dev: `django.core.mail.backends.console.EmailBackend` | `...console.EmailBackend` |
| `EMAIL_HOST_USER` | (Solo si usas SMTP) Correo Gmail que envía notificaciones | `tucuenta@gmail.com` |
| `EMAIL_HOST_PASSWORD` | (Solo si usas SMTP) [App Password](https://support.google.com/accounts/answer/185833) de Gmail | `xxxx xxxx xxxx xxxx` |

> **Importante:** `.env` está en `.gitignore` y nunca debe subirse al repositorio.

## Estructura del proyecto

```
core/           Configuración del proyecto Django (settings, urls, wsgi)
reservas/       App de reservas, usuarios, notificaciones, cupones, bitácora
complejos/      App de complejos deportivos, canchas, bloqueos
templates/      Plantillas HTML compartidas
static/         CSS, JS, imágenes estáticas
fixtures/       Datos semilla para cargar con `loaddata`
media/          Archivos subidos por usuarios (gitignorado)
```

### Datos semilla (fixtures)

El archivo `fixtures/inicial.json` contiene la configuración inicial de canchas y datos de transferencia. Para regenerarlo después de cambios en el admin:

```bash
python manage.py dumpdata complejos --indent 2 --output fixtures/inicial.json
```

Para cargarlo en una base de datos limpia:

```bash
python manage.py loaddata fixtures/inicial.json
```

## Comandos útiles

```bash
python manage.py migrate                          # aplica migraciones
python manage.py createsuperuser                  # crea usuario admin
python manage.py runserver                        # corre servidor de desarrollo
python manage.py cancelar_reservas_bloqueadas     # comando custom para limpiar reservas
```

## Primera subida del proyecto a GitHub

Si recibiste este proyecto comprimido y eres quien va a crear el repositorio en GitHub, sigue estos pasos **una sola vez**:

1. Descomprime el `.zip` en una carpeta de tu elección.

2. Abre una terminal dentro de la carpeta del proyecto y verifica que no haya quedado nada que no deba subirse:

   ```bash
   # No deberían existir estas carpetas/archivos al recibir el proyecto:
   #   venv/   db.sqlite3   .env   media/   __pycache__/
   # Si están, bórralos antes de continuar.
   ```

3. Inicializa git y haz el primer commit:

   ```bash
   git init
   git branch -M main
   git add .
   git status                      # revisa que .env NO aparezca en la lista
   git commit -m "Initial commit"
   ```

4. Crea un repositorio **privado** en GitHub (sin README ni .gitignore, ya los tenemos).

5. Vincula y sube:

   ```bash
   git remote add origin https://github.com/<tu-usuario>/complejos-deportivos.git
   git push -u origin main
   ```

6. Invita al otro colaborador desde **Settings → Collaborators** en GitHub.

7. Sigue las instrucciones de la sección [Instalación rápida](#instalación-rápida-windows) para correr el proyecto en tu máquina.

## Restaurar datos iniciales (transferencia privada)

> Esta sección aplica **solo la primera vez**, si recibiste un archivo aparte llamado `Complejos_deportivos_datos.zip` por canal privado. Este ZIP contiene `db.sqlite3` y la carpeta `media/` del proyecto original, con usuarios y reservas reales.
>
> **NUNCA** subas ese ZIP ni su contenido a GitHub. Está cubierto por `.gitignore`, pero conviene tener cuidado al hacer `git add`.

1. Asegúrate de haber seguido la [Instalación rápida](#instalación-rápida-windows) hasta el paso de instalar dependencias (no es necesario haber corrido `loaddata` ni `createsuperuser`).

2. Descomprime `Complejos_deportivos_datos.zip` en cualquier carpeta temporal.

3. Copia los siguientes elementos **dentro de la raíz del proyecto** (al mismo nivel que `manage.py`):

   ```
   db.sqlite3        → raíz del proyecto (reemplaza si ya existe)
   media/            → raíz del proyecto (fusiona con la carpeta media existente)
   ```

4. Aplica migraciones por si hubiera alguna nueva:

   ```bash
   venv\Scripts\activate
   python manage.py migrate
   ```

5. Arranca el servidor:

   ```bash
   iniciar.bat
   ```

6. Entra a `/admin` con las credenciales que recibiste por canal privado.

7. Borra el ZIP de datos de tu carpeta de descargas una vez verificado que todo funciona.

## Trabajo en equipo

### Flujo básico de Git

1. Antes de empezar a trabajar, actualiza tu rama local:
   ```bash
   git checkout main
   git pull
   ```

2. Crea una rama por funcionalidad o arreglo:
   ```bash
   git checkout -b feature/nombre-corto
   # ejemplos: feature/cupones-vencimiento, fix/error-reserva-doble
   ```

3. Haz commits pequeños y descriptivos:
   ```bash
   git add <archivos>
   git commit -m "Agrega validacion de cupones expirados"
   ```

4. Sube tu rama y abre un Pull Request en GitHub para revisión:
   ```bash
   git push -u origin feature/nombre-corto
   ```

5. Una vez aprobado el PR, se hace `merge` a `main`.

### Cuando hay nuevas migraciones del compañero

Después de un `git pull` revisa siempre:

```bash
python manage.py migrate                    # aplica migraciones del compañero
python manage.py makemigrations --check     # confirma que tus modelos no requieren migracion nueva
```

> **Importante:** no edites migraciones ya commiteadas por otra persona. Si necesitas un cambio, crea una migración **nueva**.

### Conflictos en migraciones

Si ambos crean una migración con el mismo número (`0017_*.py` en dos ramas), Django avisará al hacer `migrate`. Solución:

```bash
python manage.py makemigrations --merge
```

### Archivos que NO debes commitear

- `.env` (tus credenciales locales)
- `db.sqlite3` (tu base de datos local — cada uno tiene la suya)
- `media/` (archivos que subiste probando)
- `venv/` (tu entorno virtual)

Todo eso ya está en `.gitignore`. Si por error lo añades, usa `git rm --cached <archivo>` antes del commit.

## Notas para despliegue

- Cambiar `DEBUG=False` en `.env`.
- Configurar `ALLOWED_HOSTS` con tu dominio real.
- Reemplazar SQLite por PostgreSQL u otra base de datos productiva en `core/settings.py`.
- Servir archivos estáticos con `collectstatic` y un servidor web (Nginx, Whitenoise, etc.).
- Cambiar `EMAIL_BACKEND` o credenciales según tu proveedor.

## Licencia

Pendiente de definir.
