import csv
import hashlib
import hmac
import io
import math
import os
import re
import secrets
import time
from datetime import date, timedelta

import psycopg2
import psycopg2.extras
import psycopg2.pool
from flask import (Flask, Response, abort, flash, get_flashed_messages, redirect,
                   render_template_string, request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
# Si no hay SECRET_KEY se deriva una clave estable, igual en todos los procesos y reinicios
# (con una clave aleatoria por proceso, las sesiones y el CSRF fallan de forma intermitente).
app.secret_key = os.environ.get("SECRET_KEY") or hashlib.sha256(
    ("granja-linda|" + os.environ.get("DATABASE_URL", "") + "|" + os.environ.get("ADMIN_PASSWORD", "")).encode()
).hexdigest()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SEGURA", "1") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),
    MAX_CONTENT_LENGTH=64 * 1024,
)
DATABASE_URL = os.environ.get("DATABASE_URL", "")
MAX_INTENTOS = 5
VENTANA_BLOQUEO = 15 * 60
intentos = {}  # ip -> (cantidad de fallos, momento del primer fallo)
MAX_REGISTROS_HORA = 5
registros = {}  # ip -> momentos en que se crearon cuentas públicas

COMISION_APPS = 0.18
CANALES = ["Salón", "Delivery propio", "Apps de delivery", "Llevar (take away)"]
PAGOS = ["Efectivo", "Yape", "Plin", "POS (tarjeta)"]
CAUSAS = ["Mano", "Razón", "Vencimiento", "Preparación"]
PUESTOS = ["Cocinero(a) principal", "Ayudante de cocina", "Cajero(a)", "Delivery", "Otro"]
CONCEPTOS_CIF = ["Servicios públicos", "Arrendamiento del local", "Mantenimiento de equipos",
                 "Arbitrios", "Otro"]
# Puestos que elige cada persona (al registrarse o al ser agregada por el administrador)
PUESTOS_PERSONA = ["Cajero(a)", "Cocinero(a)", "Ayudante de cocina", "Delivery",
                   "Limpieza", "Administración", "Compras", "Otro"]

ROL_CUENTA = {"admin": "Administrador", "usuario": "Usuario"}
REGISTRO_USUARIO = {"ventas", "mermas"}

CAMPOS = {
    "ventas": [("fecha", "Fecha", "fecha", None), ("plato", "Plato", "texto", None),
               ("cantidad", "Cantidad", "entero", None), ("canal", "Canal", "texto", CANALES),
               ("pago", "Pago", "texto", PAGOS)],
    "mermas": [("fecha", "Fecha", "fecha", None), ("insumo", "Insumo", "texto", None),
               ("cantidad", "Cantidad", "decimal", None), ("costo", "Costo (S/)", "decimal", None),
               ("causa", "Causa", "texto", CAUSAS)],
    "planilla_mod": [("rol", "Puesto", "texto", PUESTOS),
                     ("sueldo", "Sueldo mensual (S/)", "decimal", None)],
    "costos_cif": [("concepto", "Concepto", "texto", CONCEPTOS_CIF),
                   ("monto", "Monto mensual (S/)", "decimal", None)],
}

ICONOS = {"rol": "user", "sueldo": "coin", "concepto": "file-text", "monto": "coin",
          "fecha": "calendar", "plato": "tools-kitchen-2", "cantidad": "hash",
          "canal": "truck-delivery", "pago": "credit-card", "insumo": "carrot",
          "costo": "coin", "causa": "alert-triangle"}

SECCIONES = [("dashboard", "chart-bar", "Dashboard"), ("ventas", "shopping-cart", "Ventas"),
             ("mermas", "box", "Mermas"), ("costos", "calculator", "Costos MOD/CIF"),
             ("fichas", "file-text", "Fichas técnicas"), ("equilibrio", "scale", "Punto de equilibrio"),
             ("reportes", "report", "Reportes"), ("usuarios", "users", "Usuarios")]

# ---------- Colores de la aplicación ----------
COLORES_DEFECTO = {"principal": "#e0661f", "secundario": "#2a7f6e", "acento": "#2e6aa8"}

PRESETS = [
    {"nombre": "Granja original", "principal": "#e0661f", "secundario": "#2a7f6e", "acento": "#2e6aa8"},
    {"nombre": "Bosque", "principal": "#8a5a2b", "secundario": "#3f7d21", "acento": "#c9a227"},
    {"nombre": "Océano", "principal": "#2e6aa8", "secundario": "#1f9e9a", "acento": "#ef7a6b"},
    {"nombre": "Atardecer", "principal": "#c0392b", "secundario": "#e0661f", "acento": "#7b4fa0"},
    {"nombre": "Pastel", "principal": "#e88aa6", "secundario": "#7cc0a0", "acento": "#8fb3e8"},
]


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c))) for c in rgb)


def mezclar(h, otro, t):
    a, b = _rgb(h), _rgb(otro)
    return _hex([a[i] + (b[i] - a[i]) * t for i in range(3)])


def oscurecer(h, t):
    return mezclar(h, "#000000", t)


def aclarar(h, t):
    return mezclar(h, "#ffffff", t)


def tema_css(c):
    """Genera las variables CSS a partir de los tres colores elegidos."""
    def juego(nombre, col):
        return (f"--{nombre}:{col};--{nombre}-osc:{oscurecer(col, .25)};"
                f"--{nombre}-cl:{aclarar(col, .86)};--{nombre}-cl2:{aclarar(col, .7)};")
    return ":root{" + juego("p", c["principal"]) + juego("s", c["secundario"]) + juego("a", c["acento"]) + "}"


@app.template_filter("soles")
def soles(n):
    return f"S/ {n:,.2f}"


# ---------- Ilustraciones (SVG) ----------
CASA_SVG = """<svg viewBox="0 0 48 48" aria-hidden="true">
  <path d="M24 5 L45 23 H39 V43 H9 V23 H3 Z" fill="#fff"/>
  <rect x="20" y="29" width="8" height="14" rx="1" fill="#dd6a23"/>
  <rect x="12" y="26" width="6" height="6" rx="1" fill="#dd6a23"/>
  <rect x="30" y="26" width="6" height="6" rx="1" fill="#dd6a23"/>
</svg>"""

GALLO_SVG = """<svg viewBox="0 0 200 180" aria-hidden="true">
  <g stroke="#f0a060" stroke-width="4" stroke-linecap="round">
    <line x1="10" y1="62" x2="26" y2="62"/><line x1="18" y1="40" x2="30" y2="50"/>
    <line x1="18" y1="84" x2="30" y2="74"/><line x1="176" y1="100" x2="192" y2="100"/>
  </g>
  <path d="M60 110 C 40 70, 60 30, 96 22 C 78 50, 80 80, 92 110 Z" fill="#f3a46a"/>
  <path d="M96 140 C 120 118, 160 96, 178 40" stroke="#b5561c" stroke-width="3" fill="none"/>
  <ellipse cx="110" cy="105" rx="52" ry="36" fill="#fff" stroke="#f1d9bf" stroke-width="2"/>
  <path d="M86 100 q26 -32 58 -6 q-22 26 -58 6z" fill="#f6e3cc"/>
  <path d="M100 138 l-4 24 M118 138 l4 24" stroke="#f2a53a" stroke-width="4" stroke-linecap="round"/>
  <circle cx="150" cy="58" r="20" fill="#fff" stroke="#f1d9bf" stroke-width="2"/>
  <path d="M138 44 q6 -20 16 -6 q6 -14 14 2 q6 -8 8 6 q-10 6 -18 2 q-10 6 -20 -4z" fill="#e0412c"/>
  <path d="M166 56 l18 6 l-18 6z" fill="#f2a53a"/>
  <circle cx="156" cy="54" r="3" fill="#2a1a0e"/>
  <path d="M158 72 q5 12 -2 18 q-5 -6 -6 -15z" fill="#e0412c"/>
</svg>"""

OLA_SVG = """<svg viewBox="0 0 600 120" preserveAspectRatio="none" aria-hidden="true">
  <path d="M0 70 C 150 20, 300 110, 450 55 S 600 30, 600 30 V120 H0Z" fill="#fde9d2"/>
  <path d="M0 95 C 180 60, 360 110, 600 80 V120 H0Z" fill="#fbdcb8"/>
</svg>"""

GALLINA_SVG = """<svg viewBox="0 0 160 110" aria-hidden="true">
  <path d="M0 100 C 40 70, 90 80, 160 60 V110 H0Z" fill="#f8d7ae"/>
  <path d="M14 96 q-6 -22 6 -32 q4 14 0 32z" fill="#f2b67a"/>
  <path d="M136 92 q-4 -26 8 -38 q2 22 -4 38z" fill="#f2b67a"/>
  <ellipse cx="68" cy="66" rx="30" ry="20" fill="#f7b97c"/>
  <circle cx="96" cy="46" r="13" fill="#f7b97c"/>
  <path d="M90 34 q4 -10 10 -2 q4 -8 8 2 q-6 4 -10 2 q-6 2 -8 -2z" fill="#e0412c"/>
  <path d="M108 44 l10 4 l-10 4z" fill="#f2a53a"/>
  <circle cx="99" cy="44" r="2" fill="#2a1a0e"/>
  <path d="M42 60 q-20 -14 -10 -30 q14 10 18 26z" fill="#eda163"/>
</svg>"""


# ---------- Base de datos (Supabase / PostgreSQL) ----------
def conectar():
    if not DATABASE_URL:
        raise RuntimeError("Falta la variable DATABASE_URL (cadena de conexión de Supabase).")
    return psycopg2.connect(DATABASE_URL, sslmode="require",
                            cursor_factory=psycopg2.extras.RealDictCursor)


_pool = None


def _tomar():
    """Toma una conexión ya abierta del grupo (evita reconectar a Supabase en cada consulta)."""
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError("Falta la variable DATABASE_URL (cadena de conexión de Supabase).")
        _pool = psycopg2.pool.ThreadedConnectionPool(
            1, 5, DATABASE_URL, sslmode="require",
            cursor_factory=psycopg2.extras.RealDictCursor,
            keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3)
    con = _pool.getconn()
    if not con.autocommit:
        con.autocommit = True
    return con


def _soltar(con, rota=False):
    try:
        _pool.putconn(con, close=bool(rota or con.closed))
    except Exception:
        pass


def _correr(sql, params, modo):
    for intento in (1, 2):
        con = _tomar()
        rota = False
        try:
            with con.cursor() as cur:
                cur.execute(sql, params)
                if modo == "uno":
                    return cur.fetchone()
                if modo == "filas":
                    return cur.rowcount
                return cur.fetchall()
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            # Conexión caída por inactividad: se descarta y se reintenta con una nueva
            rota = True
            if intento == 2:
                raise
        finally:
            _soltar(con, rota)


def consultar(sql, params=(), uno=False):
    return _correr(sql, params, "uno" if uno else "todos")


def ejecutar(sql, params=()):
    return _correr(sql, params, "filas")


def escalar(sql, params=()):
    fila = consultar(sql, params, uno=True)
    return list(fila.values())[0] if fila else None


def init_db():
    con = conectar()
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS platos (id SERIAL PRIMARY KEY, nombre TEXT UNIQUE,
            costo DOUBLE PRECISION, precio DOUBLE PRECISION);
        CREATE TABLE IF NOT EXISTS ventas (id SERIAL PRIMARY KEY, fecha TEXT, plato TEXT,
            cantidad INTEGER, canal TEXT, pago TEXT);
        CREATE TABLE IF NOT EXISTS mermas (id SERIAL PRIMARY KEY, fecha TEXT, insumo TEXT,
            cantidad DOUBLE PRECISION, costo DOUBLE PRECISION, causa TEXT);
        CREATE TABLE IF NOT EXISTS planilla_mod (id SERIAL PRIMARY KEY, rol TEXT,
            sueldo DOUBLE PRECISION);
        CREATE TABLE IF NOT EXISTS costos_cif (id SERIAL PRIMARY KEY, concepto TEXT,
            monto DOUBLE PRECISION);
        CREATE TABLE IF NOT EXISTS configuracion (clave TEXT PRIMARY KEY, valor TEXT);
        CREATE TABLE IF NOT EXISTS cuentas (id SERIAL PRIMARY KEY, usuario TEXT UNIQUE NOT NULL,
            clave TEXT NOT NULL, rol TEXT NOT NULL DEFAULT 'usuario');
        ALTER TABLE cuentas ADD COLUMN IF NOT EXISTS nombre TEXT NOT NULL DEFAULT '';
        ALTER TABLE cuentas ADD COLUMN IF NOT EXISTS puesto TEXT NOT NULL DEFAULT 'Sin asignar';
        ALTER TABLE cuentas ADD COLUMN IF NOT EXISTS activo INTEGER NOT NULL DEFAULT 1;
    """)
    for t in ["platos", "ventas", "mermas", "planilla_mod", "costos_cif", "configuracion", "cuentas"]:
        cur.execute(f"ALTER TABLE public.{t} ENABLE ROW LEVEL SECURITY;")

    semillas = {
        "platos": ("INSERT INTO platos(nombre,costo,precio) VALUES(%s,%s,%s)", [
            ("1/4 Pollo", 22, 38), ("Pollo entero", 75, 115), ("Parrilla familiar", 110, 180)]),
        "planilla_mod": ("INSERT INTO planilla_mod(rol,sueldo) VALUES(%s,%s)", [
            ("Cocinero(a) principal", 1200), ("Ayudante de cocina", 1025),
            ("Cajero(a)", 1025), ("Delivery", 1100)]),
        "costos_cif": ("INSERT INTO costos_cif(concepto,monto) VALUES(%s,%s)", [
            ("Servicios públicos", 850), ("Arrendamiento del local", 1500),
            ("Mantenimiento de equipos", 400), ("Arbitrios", 180)]),
    }
    for tabla, (sql, filas) in semillas.items():
        cur.execute(f"SELECT COUNT(*) AS n FROM {tabla}")
        if cur.fetchone()["n"] == 0:
            cur.executemany(sql, filas)

    # Cuenta de administrador: si ADMIN_PASSWORD está definida en Render, esa es la contraseña
    admin_usuario = (os.environ.get("ADMIN_USER", "admin") or "admin").strip()
    clave_env = os.environ.get("ADMIN_PASSWORD", "")
    cur.execute("SELECT id FROM cuentas WHERE usuario=%s", (admin_usuario,))
    fila = cur.fetchone()
    if clave_env:
        if fila:
            cur.execute("UPDATE cuentas SET clave=%s, rol='admin', activo=1 WHERE id=%s",
                        (generate_password_hash(clave_env), fila["id"]))
        else:
            cur.execute("INSERT INTO cuentas(usuario, clave, rol, nombre, puesto) VALUES(%s,%s,'admin','Administrador','Administración')",
                        (admin_usuario, generate_password_hash(clave_env)))
        print(f"[SEGURIDAD] Administrador '{admin_usuario}' listo con ADMIN_PASSWORD.")
    else:
        cur.execute("SELECT COUNT(*) AS n FROM cuentas")
        if fila is None and cur.fetchone()["n"] == 0:
            clave_temp = secrets.token_urlsafe(10)
            cur.execute("INSERT INTO cuentas(usuario, clave, rol, nombre, puesto) VALUES(%s,%s,'admin','Administrador','Administración')",
                        (admin_usuario, generate_password_hash(clave_temp)))
            print(f"[SEGURIDAD] No se definió ADMIN_PASSWORD. Contraseña temporal de '{admin_usuario}': {clave_temp}")

    # Siempre debe existir al menos un administrador activo
    cur.execute("SELECT COUNT(*) AS n FROM cuentas WHERE rol='admin' AND activo=1")
    if cur.fetchone()["n"] == 0:
        cur.execute("UPDATE cuentas SET rol='admin', activo=1 WHERE id=(SELECT MIN(id) FROM cuentas)")
    con.commit()
    con.close()


init_db()


def migrar_db():
    """Agrega columnas y la tabla de historial sin borrar datos existentes."""
    ejecutar("ALTER TABLE ventas ADD COLUMN IF NOT EXISTS registrado_por TEXT NOT NULL DEFAULT ''")
    ejecutar("ALTER TABLE mermas ADD COLUMN IF NOT EXISTS registrado_por TEXT NOT NULL DEFAULT ''")
    ejecutar("ALTER TABLE cuentas ADD COLUMN IF NOT EXISTS ultimo_acceso TIMESTAMP")
    ejecutar("""CREATE TABLE IF NOT EXISTS auditoria (
                    id SERIAL PRIMARY KEY,
                    momento TIMESTAMP NOT NULL DEFAULT NOW(),
                    quien TEXT NOT NULL,
                    accion TEXT NOT NULL,
                    detalle TEXT NOT NULL DEFAULT '')""")
    ejecutar("ALTER TABLE public.auditoria ENABLE ROW LEVEL SECURITY")


migrar_db()


def obtener_colores():
    colores = dict(COLORES_DEFECTO)
    for f in consultar("SELECT clave, valor FROM configuracion"):
        if f["clave"] in colores and re.fullmatch(r"#[0-9a-f]{6}", f["valor"] or ""):
            colores[f["clave"]] = f["valor"]
    return colores


def indicadores():
    f = consultar("""
        SELECT
          (SELECT COALESCE(SUM(v.cantidad*p.precio),0) FROM ventas v JOIN platos p ON p.nombre=v.plato) AS ing,
          (SELECT COALESCE(SUM(v.cantidad*p.precio),0) FROM ventas v JOIN platos p ON p.nombre=v.plato
             WHERE v.canal='Apps de delivery') AS ing_apps,
          (SELECT COALESCE(SUM(v.cantidad*p.costo),0) FROM ventas v JOIN platos p ON p.nombre=v.plato) AS cvar,
          (SELECT COALESCE(SUM(v.cantidad*p.precio),0) FROM ventas v JOIN platos p ON p.nombre=v.plato
             WHERE v.pago<>'Efectivo') AS dig,
          (SELECT COALESCE(SUM(costo),0) FROM mermas) AS mermas,
          (SELECT COALESCE(SUM(sueldo),0) FROM planilla_mod) AS mano_obra,
          (SELECT COALESCE(SUM(monto),0) FROM costos_cif) AS cif""", uno=True)
    ing, ing_apps, cvar, dig = f["ing"], f["ing_apps"], f["cvar"], f["dig"]
    mermas, mod, cif = f["mermas"], f["mano_obra"], f["cif"]

    contrib = ing - ing_apps * COMISION_APPS - cvar
    fijos = mod + cif
    return {
        "ingresos": ing,
        "utilidad": contrib - mermas - fijos,
        "pct_mermas": mermas / cvar * 100 if cvar else 0,
        "pe": fijos * ing / contrib if ing and contrib > 0 else None,
        "pct_digital": dig / ing * 100 if ing else 0,
    }


def campos_de(tabla, nombres):
    return [(n, e, t, nombres if n == "plato" else o) for n, e, t, o in CAMPOS[tabla]]


def valor_de(tipo, texto, etiqueta):
    texto = (texto or "").strip()
    if tipo in ("texto", "fecha"):
        if not texto:
            raise ValueError(f"'{etiqueta}' no puede estar vacío.")
        if len(texto) > 100:
            raise ValueError(f"'{etiqueta}' es demasiado largo.")
        return texto
    try:
        v = float(texto.replace(",", "."))
    except ValueError:
        raise ValueError(f"'{etiqueta}' debe ser un número.")
    if v < 0:
        raise ValueError(f"'{etiqueta}' no puede ser negativo.")
    return int(v) if tipo == "entero" else v


def num_arg(nombre, defecto):
    try:
        return float(request.args.get(nombre, defecto).replace(",", "."))
    except ValueError:
        return None


def logo_archivo():
    """Usa static/logo.png si existe; si no, se muestra la casa dibujada."""
    ruta = os.path.join(app.root_path, "static", "logo.png")
    return "/static/logo.png" if os.path.exists(ruta) else None


# ---------- Seguridad: sesión, roles, intentos y CSRF ----------
def token_csrf():
    """Token secreto ligado a la sesión; cada formulario POST debe enviarlo."""
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def ip_actual():
    return request.remote_addr or "desconocida"


def bloqueado(ip):
    ahora = time.time()
    n, desde = intentos.get(ip, (0, ahora))
    if ahora - desde > VENTANA_BLOQUEO:
        intentos.pop(ip, None)
        return False
    return n >= MAX_INTENTOS


def registrar_fallo(ip):
    ahora = time.time()
    n, desde = intentos.get(ip, (0, ahora))
    if ahora - desde > VENTANA_BLOQUEO:
        n, desde = 0, ahora
    intentos[ip] = (n + 1, desde)


def registro_permitido(ip):
    ahora = time.time()
    recientes = [t for t in registros.get(ip, []) if ahora - t < 3600]
    registros[ip] = recientes
    return len(recientes) < MAX_REGISTROS_HORA


def es_admin():
    return session.get("rol") == "admin"


def solo_admin():
    """Devuelve True (y avisa) si quien actúa no es administrador."""
    if not es_admin():
        flash("Esta acción solo la puede hacer el administrador.", "error")
        return True
    return False


def admins_activos():
    return escalar("SELECT COUNT(*) FROM cuentas WHERE rol='admin' AND activo=1")


def quedaria_sin_admin(fila):
    """True si quitar el rol o desactivar esta cuenta dejaría sin administradores."""
    return fila["rol"] == "admin" and fila["activo"] == 1 and admins_activos() <= 1


def auditar(accion, detalle=""):
    """Guarda en el historial quién hizo qué (acciones del administrador)."""
    ejecutar("INSERT INTO auditoria(quien, accion, detalle) VALUES(%s,%s,%s)",
             (session.get("usuario", "sistema"), accion, detalle[:300]))


PUBLICAS = {"login", "registro", "static"}


@app.before_request
def control_acceso():
    session.permanent = True
    if request.method == "POST":
        enviado = request.form.get("csrf_token", "")
        esperado = session.get("csrf", "")
        if not esperado or not hmac.compare_digest(enviado, esperado):
            session.clear()
            flash("Tu sesión expiró o el formulario no es válido. Inicia sesión de nuevo.", "error")
            return redirect(url_for("login"))
    if request.endpoint in PUBLICAS:
        return None
    if "usuario" not in session:
        return redirect(url_for("login"))
    fila = consultar("SELECT rol, activo, nombre, puesto FROM cuentas WHERE usuario=%s", (session["usuario"],), uno=True)
    if not fila or fila["activo"] != 1:
        session.clear()
        flash("Tu cuenta no está activa. Pide al administrador que la revise.", "error")
        return redirect(url_for("login"))
    session["rol"] = fila["rol"]
    session["puesto"] = fila["puesto"]
    session["nombre"] = fila["nombre"] or session["usuario"]
    return None


@app.after_request
def cabeceras_seguridad(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "font-src 'self' https://cdn.jsdelivr.net data:; img-src 'self' data:; "
        "frame-ancestors 'none'; form-action 'self'; base-uri 'self'")
    if "usuario" in session:
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------- Plantilla de inicio de sesión y registro ----------
AUTH_HTML = """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% if vista == 'registro' %}Crear cuenta{% else %}Iniciar sesión{% endif %} - La Granja Linda</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.19.0/dist/tabler-icons.min.css">
<style>
:root{--tx:#2a1a0e;--mu:#7d6b5d}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;
  font-family:system-ui,"Segoe UI",Arial,sans-serif;color:var(--tx);overflow-x:hidden;position:relative;
  background:linear-gradient(160deg,var(--p-cl),#faf6f1 55%,var(--s-cl))}
.halo{position:fixed;border-radius:50%;filter:blur(60px);opacity:.5;z-index:0;pointer-events:none}
.halo.h1{width:420px;height:420px;background:var(--p-cl2);top:-140px;left:-120px}
.halo.h2{width:360px;height:360px;background:var(--s-cl2);bottom:-140px;right:-100px}
.shell{position:relative;z-index:1;width:min(980px,100%);min-height:600px;display:grid;grid-template-columns:1fr 1.05fr;
  background:#fff;border-radius:28px;overflow:hidden;box-shadow:0 24px 60px rgba(120,60,20,.16);animation:entra .6s ease both}
@keyframes entra{from{opacity:0;transform:translateY(16px) scale(.98)}to{opacity:1;transform:none}}
.izq{position:relative;display:flex;flex-direction:column;align-items:center;text-align:center;padding:40px 36px 0;
  background:linear-gradient(175deg,var(--p-cl),var(--p-cl2));overflow:hidden}
.logo{width:92px;height:92px;border-radius:24px;background:linear-gradient(135deg,var(--p),var(--p-osc));
  display:flex;align-items:center;justify-content:center;padding:16px;box-shadow:0 10px 24px rgba(120,60,20,.2)}
.logo svg,.logo img{width:100%;height:100%;object-fit:contain}
.izq h2{margin:18px 0 2px;font-size:28px;font-weight:600;color:var(--p-osc)}
.lema{display:flex;align-items:center;gap:10px;margin:0;font-size:14px;color:var(--p-osc)}
.lema::before,.lema::after{content:"";width:28px;height:1px;background:var(--p)}
.desc{margin:16px 0 0;font-size:14px;line-height:1.55;color:#5a4636;max-width:300px}
.escena{width:100%;margin-top:24px;flex:1;display:flex;align-items:flex-end}
.escena svg{width:100%;height:auto;display:block}
.frase{width:calc(100% + 72px);margin:0 -36px;padding:20px 30px 22px;color:#fff;background:var(--p);
  font-size:18px;font-style:italic;text-align:left;border-radius:60% 40% 0 0 / 40% 30% 0 0;box-shadow:0 -6px 18px rgba(0,0,0,.05)}
.der{position:relative;padding:48px 52px 34px;display:flex;flex-direction:column;justify-content:center}
h1{margin:0;font-size:32px;font-weight:600;color:#1f1a16}
.sub{margin:10px 0 24px;font-size:15px;color:var(--mu);line-height:1.5}
.tabs{display:grid;grid-template-columns:1fr 1fr;gap:6px;padding:5px;background:#f6f1ec;border-radius:16px;margin-bottom:22px}
.tab{height:50px;border:0;border-radius:12px;background:transparent;color:#4a3a2e;font-size:15px;font-weight:500;cursor:pointer;
  display:flex;align-items:center;justify-content:center;gap:10px;font-family:inherit;transition:background .25s,color .25s,transform .15s}
.tab i{font-size:20px}
.tab:hover{transform:translateY(-1px)}
.tab.on{background:linear-gradient(90deg,var(--p),var(--p-osc));color:#fff;box-shadow:0 8px 18px color-mix(in srgb,var(--p-osc) 30%,transparent)}
.campo{display:flex;flex-direction:column;gap:8px;font-size:15px;font-weight:500;margin-bottom:16px}
.caja{position:relative;display:flex;align-items:center}
.caja > i.ic{position:absolute;left:16px;font-size:20px;color:#8c8076;pointer-events:none;z-index:1}
input[type=text],input[type=password]{width:100%;height:54px;padding:0 48px;border:1px solid #e3d9cf;border-radius:14px;
  font-size:15px;font-family:inherit;color:var(--tx);background:#fff;transition:border-color .2s,box-shadow .2s}
select{width:100%;height:54px;padding:0 44px 0 48px;border:1px solid #e3d9cf;border-radius:14px;
  font-size:15px;font-family:inherit;color:var(--tx);background:#fff;appearance:none;-webkit-appearance:none;cursor:pointer;
  transition:border-color .2s,box-shadow .2s}
select:invalid{color:#a79f97}
input::placeholder{color:#a79f97}
input:focus,select:focus{outline:none;border-color:var(--p);box-shadow:0 0 0 4px color-mix(in srgb,var(--p) 14%,transparent)}
.caja:focus-within > i.ic{color:var(--p)}
.caja.sel::after{content:"";position:absolute;right:20px;top:45%;width:8px;height:8px;border-right:2px solid #9a8a7a;
  border-bottom:2px solid #9a8a7a;transform:rotate(45deg);pointer-events:none}
.ver{position:absolute;right:10px;width:38px;height:38px;border:0;border-radius:10px;background:transparent;color:#8c8076;cursor:pointer;
  display:flex;align-items:center;justify-content:center;font-size:20px}
.ver:hover{background:#f6f1ec;color:var(--p)}
.ayuda{font-size:13px;color:var(--mu);margin:-6px 0 14px}
.fila{display:flex;justify-content:space-between;align-items:center;margin:4px 0 22px;font-size:14px}
.rec{display:flex;align-items:center;gap:10px;color:#4a3a2e;cursor:pointer}
.rec input{width:18px;height:18px;accent-color:var(--p);margin:0}
.olvide{color:var(--p-osc);text-decoration:underline;text-underline-offset:3px}
.entrar{width:100%;height:56px;border:0;border-radius:16px;color:#fff;font-size:17px;font-weight:500;cursor:pointer;font-family:inherit;
  display:flex;align-items:center;justify-content:center;gap:12px;background:linear-gradient(90deg,var(--p),var(--p-osc));
  box-shadow:0 12px 24px color-mix(in srgb,var(--p-osc) 30%,transparent);transition:transform .15s,filter .2s}
.entrar:hover{filter:brightness(1.05);transform:translateY(-1px)}
.entrar:active{transform:scale(.98)}
.entrar i{font-size:22px}
.pie{margin:26px 0 0;padding-top:20px;border-top:1px solid #efe7de;text-align:center;font-size:14px;color:var(--mu)}
.pie a{color:var(--p-osc);text-decoration:none}
.pie b{font-weight:600}
.flash{background:#fdeeee;color:#a8322d;padding:12px 16px;border-radius:12px;margin-bottom:18px;font-size:14px}
.flash.ok{background:#eef7ea;color:#3f7d21}
@media(max-width:820px){
  .shell{grid-template-columns:1fr;min-height:0}
  .izq{padding:28px 24px 0}
  .escena{display:none}
  .frase{font-size:16px;padding:16px 24px}
  .der{padding:32px 26px 28px}
  h1{font-size:26px}
}
</style>
<style>{{ tema|safe }}</style>
</head>
<body>
<div class="halo h1"></div>
<div class="halo h2"></div>

<main class="shell">
  <section class="izq">
    <div class="logo">{% if logo_img %}<img src="{{ logo_img }}" alt="Logo">{% else %}{{ casa_svg|safe }}{% endif %}</div>
    <h2>La Granja Linda</h2>
    <p class="lema">Tu granja, nuestra prioridad</p>
    <p class="desc">Gestiona costos, mermas y presupuestos de manera fácil, segura y eficiente.</p>
    <div class="escena" aria-hidden="true">
      <svg viewBox="0 0 360 220">
        <rect x="0" y="168" width="360" height="60" fill="#e9bd93" opacity=".55"/>
        <rect x="80" y="112" width="200" height="72" rx="4" fill="#fffaf4" stroke="#e7d2bd" stroke-width="2"/>
        <path d="M58 118 L180 40 L302 118 Z" style="fill:var(--p-osc)"/>
        <rect x="104" y="132" width="34" height="26" rx="2" fill="#f3d9bf"/>
        <rect x="222" y="132" width="34" height="26" rx="2" fill="#f3d9bf"/>
        <rect x="164" y="136" width="32" height="48" rx="2" style="fill:var(--p)"/>
        <circle cx="290" cy="190" r="11" fill="none" stroke="#b8aca0" stroke-width="5"/>
        <rect x="300" y="186" width="44" height="6" rx="3" fill="#b8aca0"/>
        <rect x="330" y="192" width="5" height="10" rx="2" fill="#b8aca0"/>
      </svg>
    </div>
    <div class="frase">Más que una granja,<br>una familia</div>
  </section>

  <section class="der">
    {% if vista == 'registro' %}
      <h1>Crear cuenta</h1>
      <p class="sub">Regístrate con tu nombre y tu puesto. Tu cuenta será de tipo Usuario.</p>
    {% else %}
      <h1>Iniciar sesión</h1>
      <p class="sub">Selecciona el tipo de cuenta e ingresa tus datos para continuar.</p>
    {% endif %}

    {% for cat, m in get_flashed_messages(with_categories=true) %}<div class="flash {{ cat }}">{{ m }}</div>{% endfor %}

    {% if vista != 'registro' %}
    <div class="tabs" role="tablist">
      <button type="button" class="tab {{ 'on' if tipo == 'usuario' else '' }}" data-tipo="usuario" role="tab">
        <i class="ti ti-user" aria-hidden="true"></i>Usuario</button>
      <button type="button" class="tab {{ 'on' if tipo == 'admin' else '' }}" data-tipo="admin" role="tab">
        <i class="ti ti-shield-check" aria-hidden="true"></i>Administrador</button>
    </div>
    {% endif %}

    <form method="post" action="{{ url_for('registro') if vista == 'registro' else url_for('login') }}" autocomplete="on">
      <input type="hidden" name="csrf_token" value="{{ csrf_tok }}">
      {% if vista != 'registro' %}<input type="hidden" name="tipo" id="tipo" value="{{ tipo }}">{% endif %}

      {% if vista == 'registro' %}
      <label class="campo">Nombre completo
        <span class="caja"><i class="ti ti-id ic" aria-hidden="true"></i>
          <input type="text" name="nombre" placeholder="Ej. Juan Pérez" autocomplete="name" required maxlength="60"></span>
      </label>

      <label class="campo">Puesto en el negocio
        <span class="caja sel"><i class="ti ti-briefcase ic" aria-hidden="true"></i>
          <select name="puesto" required>
            <option value="" disabled selected>Selecciona tu puesto</option>
            {% for pu in puestos_persona %}<option>{{ pu }}</option>{% endfor %}
          </select></span>
      </label>
      {% endif %}

      <label class="campo">Usuario
        <span class="caja"><i class="ti ti-user ic" aria-hidden="true"></i>
          <input type="text" name="usuario" placeholder="{{ 'Elige un nombre de usuario' if vista == 'registro' else 'Ingresa tu usuario' }}"
            autocomplete="username" required maxlength="{{ 30 if vista == 'registro' else 50 }}" {% if vista == 'registro' %}pattern="[A-Za-z0-9_.]{3,30}"{% endif %}></span>
      </label>
      {% if vista == 'registro' %}<p class="ayuda">De 3 a 30 letras, números, punto o guion bajo.</p>{% endif %}

      <label class="campo">Contraseña
        <span class="caja"><i class="ti ti-lock ic" aria-hidden="true"></i>
          <input type="password" name="clave" id="clave" placeholder="{{ 'Mínimo 10 caracteres' if vista == 'registro' else 'Ingresa tu contraseña' }}"
            autocomplete="{{ 'new-password' if vista == 'registro' else 'current-password' }}" required minlength="{{ 10 if vista == 'registro' else 1 }}" maxlength="200">
          <button type="button" class="ver" id="ver" aria-label="Mostrar u ocultar contraseña"><i class="ti ti-eye" aria-hidden="true"></i></button></span>
      </label>

      {% if vista == 'registro' %}
      <label class="campo">Repite la contraseña
        <span class="caja"><i class="ti ti-lock ic" aria-hidden="true"></i>
          <input type="password" name="confirmar" placeholder="Vuelve a escribir la contraseña" autocomplete="new-password" required minlength="10" maxlength="128"></span>
      </label>
      <div style="height:8px"></div>
      <button class="entrar" type="submit"><i class="ti ti-user-plus" aria-hidden="true"></i>Crear cuenta</button>
      {% else %}
      <div class="fila">
        <label class="rec"><input type="checkbox"> Recordarme</label>
        <a href="#" class="olvide" id="olvide">¿Olvidaste tu contraseña?</a>
      </div>
      <button class="entrar" type="submit"><i class="ti ti-login-2" aria-hidden="true"></i>Iniciar sesión</button>
      {% endif %}
    </form>

    {% if vista == 'registro' %}
      <p class="pie">¿Ya tienes una cuenta? <a href="{{ url_for('login') }}"><b>Iniciar sesión</b></a></p>
    {% else %}
      <p class="pie">¿No tienes una cuenta? <a href="{{ url_for('registro') }}"><b>Crear cuenta</b></a></p>
    {% endif %}
  </section>
</main>

<script>
(function () {
  var tabs = document.querySelectorAll('.tab');
  var tipo = document.getElementById('tipo');
  tabs.forEach(function (t) {
    t.addEventListener('click', function () {
      tabs.forEach(function (x) { x.classList.remove('on'); });
      t.classList.add('on');
      if (tipo) tipo.value = t.getAttribute('data-tipo');
    });
  });
  var ver = document.getElementById('ver');
  var clave = document.getElementById('clave');
  ver.addEventListener('click', function () {
    var oculta = clave.type === 'password';
    clave.type = oculta ? 'text' : 'password';
    ver.innerHTML = '<i class="ti ' + (oculta ? 'ti-eye-off' : 'ti-eye') + '" aria-hidden="true"></i>';
  });
  var olvide = document.getElementById('olvide');
  if (olvide) {
    olvide.addEventListener('click', function (e) {
      e.preventDefault();
      alert('Pide a tu administrador que restablezca tu contraseña.');
    });
  }
})();
</script>
</body>
</html>
"""


# ---------- Plantilla principal ----------
PLANTILLA = """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>La Granja Linda - Gestión</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.19.0/dist/tabler-icons.min.css">
<style>
:root{--tx:#2a1a0e;--mu:#7d6b5d;--ln:#efe3d5;--crema:#fbf7f2;
  --vd-f:#eef7ea;--vd-i:#d7eec4;--vd-t:#3f7d21;--am-f:#fdf9e6;--am-i:#f8ebb8;--am-t:#8a6200;
  --rj-f:#fdeeee;--rj-i:#f8d2d2;--rj-t:#a8322d;--barra-v:#7cc05a;--barra-r:#ee6e6e}
*{box-sizing:border-box}
body{margin:0;font-family:system-ui,"Segoe UI",Arial,sans-serif;background:var(--crema);color:var(--tx);font-size:14px;
  display:grid;grid-template-columns:240px 1fr;grid-template-rows:auto 1fr;min-height:100vh;overflow-x:hidden}
.fondo-anim{position:fixed;inset:0;overflow:hidden;z-index:0;pointer-events:none}
.fondo-anim .blob{position:absolute;border-radius:50%;filter:blur(40px);opacity:.55;animation:respira 14s ease-in-out infinite}
.fondo-anim .b1{width:420px;height:420px;background:var(--p-cl2);top:-120px;right:-80px}
.fondo-anim .b2{width:360px;height:360px;background:var(--s-cl2);bottom:-140px;left:200px;animation-delay:-5s}
.fondo-anim .b3{width:280px;height:280px;background:var(--a-cl2);top:40%;left:30%;animation-delay:-9s}
.fondo-anim .ic{position:absolute;color:var(--p);opacity:.22;animation:flota 9s ease-in-out infinite}
.fondo-anim .i1{font-size:34px;top:18%;left:44%;animation-delay:-1s}
.fondo-anim .i2{font-size:26px;top:62%;left:58%;animation-delay:-4s;color:var(--s)}
.fondo-anim .i3{font-size:30px;top:80%;left:12%;animation-delay:-6s}
.fondo-anim .i4{font-size:22px;top:30%;left:78%;animation-delay:-2s;color:var(--s)}
.fondo-anim .i5{font-size:28px;top:8%;left:62%;animation-delay:-7s;color:var(--a)}
@keyframes respira{0%,100%{transform:scale(1) translate(0,0)}50%{transform:scale(1.12) translate(20px,-14px)}}
@keyframes flota{0%,100%{transform:translateY(0) rotate(0deg)}50%{transform:translateY(-26px) rotate(14deg)}}
#cursor-glow{position:fixed;left:0;top:0;width:320px;height:320px;border-radius:50%;pointer-events:none;z-index:0;
  background:radial-gradient(circle,color-mix(in srgb,var(--p) 16%,transparent),transparent 70%);opacity:0;transition:opacity .4s;will-change:transform}
header{grid-column:1/-1;background:linear-gradient(90deg,var(--p),var(--p-osc));color:#fff;padding:12px 24px;
  display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;position:relative;z-index:2}
.marca{display:flex;align-items:center;gap:14px}
.marca .casa{width:52px;height:52px;border-radius:12px;background:rgba(255,255,255,.12);display:flex;align-items:center;justify-content:center;padding:6px}
.marca .casa svg{width:100%;height:100%}
.marca img{width:60px;height:60px;object-fit:contain;background:#fff;border-radius:14px;padding:2px;box-shadow:0 2px 8px rgba(0,0,0,.18)}
.plato{flex:1;align-self:stretch;position:relative;overflow:hidden;min-width:120px;pointer-events:none;
  -webkit-mask-image:linear-gradient(90deg,transparent,#000 12%,#000 88%,transparent);mask-image:linear-gradient(90deg,transparent,#000 12%,#000 88%,transparent)}
.plato .papa{position:absolute;display:block;opacity:.9;filter:drop-shadow(0 2px 2px rgba(80,25,0,.3));
  animation:flotar var(--t,9s) ease-in-out var(--d,0s) infinite}
.plato .papa svg{display:block;width:100%;height:100%}
@keyframes flotar{0%,100%{transform:translate(0,0) rotate(var(--r,0deg))}
  50%{transform:translate(var(--dx,14px),var(--dy,-12px)) rotate(calc(var(--r,0deg) * -1.4))}}
@media(prefers-reduced-motion:reduce){.plato .papa{animation:none}}
@media(max-width:1100px){.plato{display:none}}
.marca h1{margin:0;font-size:21px;font-weight:500}
.marca small{display:block;font-size:11px;letter-spacing:2.5px;color:#fff;opacity:.85;text-transform:uppercase;margin-top:2px}
.derecha{display:flex;align-items:center;gap:12px;font-size:13px;color:#fff;flex-wrap:wrap}
.quien{text-align:right;line-height:1.25}
.quien b{display:block;font-weight:600}
.quien span{font-size:12px;opacity:.9}
.rol-chip{display:inline-flex;align-items:center;gap:5px;margin-top:3px;padding:2px 10px;border-radius:999px;background:rgba(255,255,255,.22);opacity:1!important;font-weight:500}
.rol-chip i{font-size:14px}
.avatar{width:40px;height:40px;border-radius:50%;background:#fff;color:var(--p);display:flex;align-items:center;justify-content:center;font-size:20px}
.engranaje{width:40px;height:40px;border-radius:50%;border:0;padding:0;cursor:pointer;background:rgba(255,255,255,.22);color:#fff;display:flex;align-items:center;justify-content:center;font-size:20px;text-decoration:none;transition:transform .3s,background .2s}
.engranaje:hover{background:rgba(255,255,255,.35);transform:rotate(60deg)}
.engranaje.activo{background:#fff;color:var(--p)}
nav{position:sticky;top:0;align-self:start;height:100vh;overflow-y:auto;z-index:2;background:linear-gradient(180deg,var(--p-cl),var(--p-cl2));padding:20px 14px;display:flex;flex-direction:column;gap:6px;border-right:1px solid var(--p-cl2)}
nav a{display:flex;align-items:center;gap:12px;padding:12px 14px;border-radius:12px;text-decoration:none;color:var(--p-osc);font-size:14px;transition:background .2s,transform .15s}
nav a i{font-size:20px}
nav a:hover{background:rgba(255,255,255,.7);transform:translateX(3px)}
nav a.activo{background:var(--p);color:#fff;font-weight:500}
.decor-menu{margin-top:auto;width:100%}
.decor-menu svg{width:100%;height:auto;display:block}
main{min-width:0;padding:22px 26px;max-width:1180px;width:100%;position:relative;z-index:1}
main > *{animation:sube .5s ease both}
main > *:nth-child(2){animation-delay:.05s}
main > *:nth-child(3){animation-delay:.1s}
@keyframes sube{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}
.card{background:#fff;border-radius:18px;padding:18px;margin-bottom:18px;box-shadow:0 2px 14px rgba(120,60,20,.07);overflow:hidden;transition:transform .25s,box-shadow .25s}
.card:hover{transform:translateY(-2px);box-shadow:0 8px 22px rgba(120,60,20,.12)}
.banner{display:flex;align-items:center;gap:16px;padding:18px 20px;margin:-18px -18px 18px;position:relative;overflow:hidden}
.banner.naranja{background:linear-gradient(90deg,var(--p-cl),var(--p-cl2))}
.banner.verde{background:linear-gradient(90deg,var(--s-cl),var(--s-cl2))}
.banner.azul{background:linear-gradient(90deg,var(--a-cl),var(--a-cl2))}
.banner.rojo{background:linear-gradient(90deg,#fde7e4,#f8c9c2)}
.banner.gris{background:linear-gradient(90deg,#f5f1ec,#e8dfd4)}
.ic-circ{width:56px;height:56px;border-radius:50%;color:#fff;display:flex;align-items:center;justify-content:center;font-size:26px;flex-shrink:0;box-shadow:0 6px 14px rgba(0,0,0,.12)}
.naranja .ic-circ{background:linear-gradient(135deg,var(--p),var(--p-osc))}
.verde .ic-circ{background:linear-gradient(135deg,var(--s),var(--s-osc))}
.azul .ic-circ{background:linear-gradient(135deg,var(--a),var(--a-osc))}
.rojo .ic-circ{background:linear-gradient(135deg,#ef7a6b,#c0392b)}
.gris .ic-circ{background:linear-gradient(135deg,#9a8a7a,#6b5a4a)}
.banner h3{margin:0;font-size:19px;font-weight:600}
.banner p{margin:4px 0 0;font-size:13px}
.naranja h3{color:var(--p-osc)}.naranja p{color:var(--p-osc)}
.verde h3{color:var(--s-osc)}.verde p{color:var(--s-osc)}
.azul h3{color:var(--a-osc)}.azul p{color:var(--a-osc)}
.rojo h3{color:#8a2a20}.rojo p{color:#7a3a33}
.gris h3{color:#4a3a2a}.gris p{color:#6b5a4a}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:18px}
@media(max-width:800px){.grid2{grid-template-columns:1fr}body{grid-template-columns:1fr}nav{position:static;height:auto;flex-direction:row;flex-wrap:wrap;border-right:none}nav a{padding:8px 10px}.decor-menu{display:none}.hero .gallo{display:none}#cursor-glow{display:none}}
.hero{position:relative;overflow:hidden;background:#fff;border-radius:18px;padding:24px 26px;display:flex;align-items:center;gap:18px;margin-bottom:18px;min-height:170px;box-shadow:0 2px 14px rgba(120,60,20,.07)}
.hero .txt{position:relative;z-index:2;display:flex;align-items:center;gap:18px}
.hero small{color:var(--mu);font-size:14px;display:block}
.hero b{display:block;font-size:40px;font-weight:500;color:var(--tx);margin:4px 0}
.hero .pie{font-size:13px;color:var(--mu)}
.hero .ola{position:absolute;left:0;right:0;bottom:0;height:110px;z-index:0}
.hero .ola svg{width:100%;height:100%}
.hero .gallo{position:absolute;right:20px;top:4px;width:230px;height:200px;z-index:1}
.hero .gallo svg{width:100%;height:100%}
.bloques{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin-bottom:18px}
.bloque{border-radius:18px;padding:18px;border:1px solid transparent;transition:transform .25s}
.bloque:hover{transform:translateY(-3px)}
.bloque .top{display:flex;justify-content:space-between;align-items:flex-start}
.bloque .circ{width:44px;height:44px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:20px}
.bloque .tend{font-size:20px;opacity:.9}
.bloque small{font-size:14px;display:block;margin-top:14px}
.bloque b{display:block;font-size:26px;font-weight:500;margin:4px 0 2px;color:var(--tx)}
.bloque span{font-size:12px;color:var(--mu)}
.b-ambar{background:var(--p-cl);border-color:var(--p-cl2)}.b-ambar .tend{color:var(--p)}.b-ambar .circ{background:var(--p-cl2);color:var(--p-osc)}
.b-azul{background:var(--a-cl);border-color:var(--a-cl2)}.b-azul .tend{color:var(--a-osc)}.b-azul .circ{background:var(--a-cl2);color:var(--a-osc)}
.b-verde{background:var(--vd-f);border-color:#dcefc9}.b-verde .tend{color:var(--vd-t)}.b-verde .circ{background:var(--vd-i);color:var(--vd-t)}
.b-rojo{background:var(--rj-f);border-color:#f7d3d3}.b-rojo .tend{color:var(--rj-t)}.b-rojo .circ{background:var(--rj-i);color:var(--rj-t)}
.b-amarillo{background:var(--am-f);border-color:#f5e8b5}.b-amarillo .circ{background:var(--am-i);color:var(--am-t)}
.fila-plato{display:grid;grid-template-columns:150px 1fr 60px;align-items:center;gap:14px;font-size:14px;padding:10px 0}
.barra{height:12px;background:#f3eee8;border-radius:8px;overflow:hidden}
.barra div{height:100%;border-radius:8px;transition:width .8s ease}
.nota{color:var(--mu);font-size:12px;margin:10px 0 0}
.filas div{display:flex;justify-content:space-between;gap:12px;padding:12px 0;border-bottom:1px solid var(--ln);font-size:13px}
.filas div:last-child{border-bottom:none}
form.reg{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px 18px;margin-bottom:6px;align-items:end}
.campo{display:flex;flex-direction:column;gap:8px;font-size:13px;color:var(--tx);font-weight:500}
.campo .lbl{display:flex;align-items:center;gap:8px}
.campo .lbl i{font-size:17px;color:var(--p)}
.verde .campo .lbl i{color:var(--s)}
.azul .campo .lbl i{color:var(--a)}
.caja{position:relative;display:block}
.caja.sel::after{content:"";position:absolute;right:18px;top:45%;width:8px;height:8px;border-right:2px solid #9a8a7a;border-bottom:2px solid #9a8a7a;transform:rotate(45deg);pointer-events:none}
input,select{height:48px;width:100%;padding:0 14px;border:1px solid #e6d8c6;border-radius:12px;font-size:14px;background:#fff;color:var(--tx);font-family:inherit;transition:border-color .2s,box-shadow .2s}
input[type=color]{padding:4px;cursor:pointer}
input::placeholder{color:#a79685}
select{appearance:none;-webkit-appearance:none;padding-right:38px;cursor:pointer}
select:invalid{color:#a79685}
input:focus,select:focus{outline:none;border-color:var(--p);box-shadow:0 0 0 3px color-mix(in srgb,var(--p) 15%,transparent)}
.verde input:focus,.verde select:focus{border-color:var(--s);box-shadow:0 0 0 3px color-mix(in srgb,var(--s) 15%,transparent)}
.azul input:focus,.azul select:focus{border-color:var(--a);box-shadow:0 0 0 3px color-mix(in srgb,var(--a) 15%,transparent)}
.accion{grid-column:1/-1;display:flex;align-items:center;gap:14px}
.btn,.btn-sec,button{position:relative;overflow:hidden}
.btn{height:48px;padding:0 18px;border-radius:12px;border:none;color:#fff;font-size:15px;font-weight:500;cursor:pointer;font-family:inherit;display:inline-flex;align-items:center;gap:10px;background:linear-gradient(90deg,var(--p),var(--p-osc));box-shadow:0 6px 14px color-mix(in srgb,var(--p-osc) 30%,transparent);transition:transform .15s,filter .2s,box-shadow .2s}
.btn:hover{filter:brightness(1.06);box-shadow:0 10px 20px color-mix(in srgb,var(--p-osc) 38%,transparent);transform:translateY(-1px)}
.btn:active,.btn-sec:active{transform:scale(.95)}
.btn.verde{background:linear-gradient(90deg,var(--s),var(--s-osc));box-shadow:0 6px 14px color-mix(in srgb,var(--s-osc) 30%,transparent)}
.btn.azul{background:linear-gradient(90deg,var(--a),var(--a-osc));box-shadow:0 6px 14px color-mix(in srgb,var(--a-osc) 30%,transparent)}
.btn.rojo{background:linear-gradient(90deg,#ef7a6b,#c0392b);box-shadow:0 6px 14px rgba(192,57,43,.25)}
.btn.gris{background:linear-gradient(90deg,#9a8a7a,#6b5a4a);box-shadow:0 6px 14px rgba(107,90,74,.25)}
.btn i{font-size:18px}
.btn .flecha{font-size:16px;opacity:.9;transition:transform .25s}
.btn:hover .flecha{transform:translateX(4px)}
.btn-sec{height:auto;padding:7px 14px;border-radius:10px;background:#fff;color:var(--p-osc);border:1px solid var(--p-cl2);font-size:13px;font-weight:500;cursor:pointer;font-family:inherit;display:inline-flex;align-items:center;gap:6px;transition:transform .15s,background .2s;text-decoration:none}
.btn-sec:hover{background:var(--p-cl)}
.btn-sec.verde{color:var(--s-osc);border-color:var(--s-cl2)}
.btn-sec.verde:hover{background:var(--s-cl)}
.btn-sec.azul{color:var(--a-osc);border-color:var(--a-cl2)}
.btn-sec.azul:hover{background:var(--a-cl)}
.btn-sec.rojo{color:#a8322d;border-color:#f4b9b2}
.btn-sec.rojo:hover{background:#fdeeee}
.btn-sec i{font-size:16px}
.btn-sec.activo-p{background:var(--p);color:#fff;border-color:var(--p)}
.ripple{position:absolute;border-radius:50%;background:rgba(255,255,255,.45);transform:scale(0);animation:onda .6s ease-out forwards;pointer-events:none}
.btn-sec .ripple{background:color-mix(in srgb,var(--p) 22%,transparent)}
@keyframes onda{to{transform:scale(1);opacity:0}}
.tabla-wrap{border:1px solid var(--ln);border-radius:14px;overflow:hidden;margin-top:16px}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;font-size:13px;color:var(--tx);font-weight:500;padding:12px 16px;background:var(--p-cl);border-bottom:1px solid var(--ln)}
th.verde-h{background:var(--s-cl);color:var(--s-osc)}
th.azul-h{background:var(--a-cl);color:var(--a-osc)}
td{padding:12px 16px;border-bottom:1px solid #f4ece3;transition:background .2s}
tbody tr:hover td,table tr:hover td{background:#fffaf5}
tr:last-child td{border-bottom:none}
td.acc{text-align:right;width:260px}
td.acc form{display:inline-block;margin-left:4px}
.res{background:var(--p-cl);color:var(--p-osc);border-radius:12px;padding:14px 16px;margin-top:14px;font-weight:500}
.total{display:flex;align-items:center;gap:16px;border-radius:16px;padding:18px 20px;margin-top:16px;color:#fff;position:relative;overflow:hidden}
.total.naranja{background:linear-gradient(90deg,var(--p),var(--p-osc))}
.total.verde{background:linear-gradient(90deg,var(--s),var(--s-osc))}
.total .circ{width:48px;height:48px;border-radius:50%;background:rgba(255,255,255,.25);display:flex;align-items:center;justify-content:center;font-size:24px}
.total small{font-size:13px;opacity:.9;display:block}
.total b{font-size:26px;font-weight:500}
.total .tend{position:absolute;right:22px;top:50%;transform:translateY(-50%);font-size:34px;opacity:.85}
.solo-lectura{font-size:12px;color:var(--mu);margin:12px 0 0}
.estado{display:inline-block;padding:3px 10px;border-radius:999px;font-size:12px;font-weight:500}
.estado.on{background:var(--vd-i);color:var(--vd-t)}
.estado.off{background:var(--rj-i);color:var(--rj-t)}
.flash{background:var(--rj-f);color:var(--rj-t);padding:12px 16px;border-radius:12px;margin-bottom:14px}
.flash.ok{background:var(--s-cl);color:var(--s-osc)}
.paletas{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-top:4px}
.paleta{border:1px solid var(--ln);border-radius:14px;padding:14px;background:#fff;cursor:pointer;text-align:left;font-family:inherit;transition:transform .2s,box-shadow .2s}
.paleta:hover{transform:translateY(-2px);box-shadow:0 6px 16px rgba(120,60,20,.12)}
.paleta .nombre{font-size:14px;font-weight:500;color:var(--tx);margin-bottom:10px;display:block}
.paleta .muestras{display:flex;gap:6px}
.paleta .muestras span{width:34px;height:34px;border-radius:10px;display:block}
.vista-previa{border:1px solid var(--ln);border-radius:16px;overflow:hidden;background:#fff}
.vista-previa .pv-barra{padding:14px 18px;color:#fff;font-weight:500;background:var(--pv-p,#e0661f)}
.vista-previa .pv-cuerpo{padding:18px;display:flex;flex-wrap:wrap;gap:12px;align-items:center}
.pv-btn{height:44px;padding:0 18px;border-radius:12px;border:none;color:#fff;font-size:14px;font-family:inherit;display:inline-flex;align-items:center;gap:8px}
.pv-btn.p{background:linear-gradient(90deg,var(--pv-p,#e0661f),color-mix(in srgb,var(--pv-p,#e0661f) 75%,black))}
.pv-btn.s{background:linear-gradient(90deg,var(--pv-s,#2a7f6e),color-mix(in srgb,var(--pv-s,#2a7f6e) 75%,black))}
.pv-chip{padding:8px 14px;border-radius:999px;color:#fff;font-size:13px;background:var(--pv-a,#2e6aa8)}
.pv-tarjeta{flex:1;min-width:180px;border-radius:14px;padding:14px;background:color-mix(in srgb,var(--pv-p,#e0661f) 14%,white)}
.pv-tarjeta b{display:block;font-size:20px;font-weight:500;color:var(--tx)}
@media (prefers-reduced-motion: reduce){
  *,*::before,*::after{animation:none!important;transition:none!important}
  #cursor-glow{display:none}
}
</style>
<style>{{ tema|safe }}</style>
</head>
<body>

<div class="fondo-anim" aria-hidden="true">
  <span class="blob b1"></span>
  <span class="blob b2"></span>
  <span class="blob b3"></span>
  <i class="ti ti-leaf ic i1"></i>
  <i class="ti ti-egg ic i2"></i>
  <i class="ti ti-feather ic i3"></i>
  <i class="ti ti-plant-2 ic i4"></i>
  <i class="ti ti-egg ic i5"></i>
</div>
<div id="cursor-glow" aria-hidden="true"></div>

{% macro ico(nombre) %}<i class="ti ti-{{ nombre }}" aria-hidden="true"></i>{% endmacro %}

{% macro banner(icono, titulo, sub='', color='naranja') %}
  <div class="banner {{ color }}">
    <div class="ic-circ"><i class="ti ti-{{ icono }}" aria-hidden="true"></i></div>
    <div><h3>{{ titulo }}</h3>{% if sub %}<p>{{ sub }}</p>{% endif %}</div>
  </div>
{% endmacro %}

{% macro registro(tabla, campos, columnas, filas, color='naranja', boton='Agregar') %}
  {% if es_admin or tabla in ['ventas', 'mermas'] %}
  <form class="reg {{ color }}" method="post" action="{{ url_for('agregar', tabla=tabla) }}">
    <input type="hidden" name="tab" value="{{ tab }}">
    <input type="hidden" name="csrf_token" value="{{ csrf_tok }}">
    {% for n, e, t, opciones in campos %}
      <label class="campo">
        <span class="lbl"><i class="ti ti-{{ iconos.get(n, 'point') }}" aria-hidden="true"></i>{{ e }}</span>
        {% if opciones %}
          <span class="caja sel">
            <select name="{{ n }}" required>
              <option value="" disabled selected>Selecciona una opción</option>
              {% for o in opciones %}<option>{{ o }}</option>{% endfor %}
            </select>
          </span>
        {% elif t == 'fecha' %}
          <span class="caja"><input type="date" name="{{ n }}" value="{{ hoy }}" required></span>
        {% elif t == 'texto' %}
          <span class="caja"><input type="text" name="{{ n }}" maxlength="100" placeholder="Ingresa {{ e|lower }}" required></span>
        {% else %}
          <span class="caja"><input type="number" name="{{ n }}" min="0" step="{{ '1' if t == 'entero' else 'any' }}" placeholder="Ingresa {{ e|lower }}" required></span>
        {% endif %}
      </label>
    {% endfor %}
    {% if tabla == 'ventas' %}
    <div class="res" id="total-venta" style="grid-column:1/-1;font-size:18px">Total a cobrar: S/ 0.00</div>
    <script>
    (function () {
      var precios = {{ precios_platos|tojson }};
      var f = document.querySelector('form[action$="/agregar/ventas"]');
      if (!f) return;
      var plato = f.querySelector('select[name="plato"]');
      var cant = f.querySelector('input[name="cantidad"]');
      var out = document.getElementById('total-venta');
      function calcular() {
        var precio = precios[plato.value] || 0;
        var cantidad = parseFloat(cant.value) || 0;
        out.textContent = 'Total a cobrar: S/ ' + (precio * cantidad).toLocaleString('es-PE', {minimumFractionDigits: 2, maximumFractionDigits: 2});
      }
      plato.addEventListener('change', calcular);
      cant.addEventListener('input', calcular);
    })();
    </script>
    {% endif %}
    <div class="accion">
      <button class="btn {{ color }}" type="submit"><i class="ti ti-plus" aria-hidden="true"></i>{{ boton }}<i class="ti ti-chevron-right flecha" aria-hidden="true"></i></button>
    </div>
  </form>
  {% endif %}
  {% if columnas %}
  <div class="tabla-wrap">
  <table>
    <tr>{% for c, lbl in columnas %}<th class="{{ 'verde-h' if color == 'verde' }} {{ 'azul-h' if color == 'azul' }}">{{ lbl }}</th>{% endfor %}{% if es_admin or tabla in ['ventas', 'mermas'] %}<th class="{{ 'verde-h' if color == 'verde' }} {{ 'azul-h' if color == 'azul' }}"></th>{% endif %}</tr>
    {% for f in filas %}
    <tr>
      {% for c, lbl in columnas %}<td>{% if c == 'total' %}{{ f[c]|soles }}{% else %}{{ f[c] }}{% endif %}</td>{% endfor %}
      {% if es_admin or (tabla in ['ventas', 'mermas'] and f['registrado_por'] == usuario and f['fecha'] == hoy) %}
      <td class="acc">
        <form method="post" action="{{ url_for('eliminar', tabla=tabla, id_=f['id']) }}">
          <input type="hidden" name="tab" value="{{ tab }}">
          <input type="hidden" name="csrf_token" value="{{ csrf_tok }}">
          <button class="btn-sec {{ color }}" type="submit"><i class="ti ti-trash" aria-hidden="true"></i>Eliminar</button>
        </form>
      </td>
      {% endif %}
    </tr>
    {% endfor %}
  </table>
  </div>
  {% endif %}
{% endmacro %}

<header>
  <div class="marca">
    {% if logo_img %}<img src="{{ logo_img }}" alt="Logo La Granja Linda">
    {% else %}<div class="casa">{{ casa_svg|safe }}</div>{% endif %}
    <div>
      <h1>La Granja Linda</h1>
      <small>Pollería · Gestión de costos</small>
    </div>
  </div>
  <div class="plato" aria-hidden="true">
    <svg width="0" height="0" style="position:absolute">
      <defs>
        <symbol id="fr-palo" viewBox="0 0 10 26">
          <rect x="1" y="1" width="8" height="24" rx="3" fill="#f7c948" stroke="#c98a1d" stroke-width="1.2"/>
          <rect x="2.6" y="3.5" width="2" height="15" rx="1" fill="#fff3b0" opacity=".85"/>
        </symbol>
        <symbol id="fr-caja" viewBox="0 0 26 26">
          <g stroke="#c98a1d" stroke-width=".9" fill="#f7c948">
            <rect x="5" y="2" width="4.2" height="14" rx="2" transform="rotate(-14 7 9)"/>
            <rect x="9.6" y="0.5" width="4.2" height="15" rx="2" transform="rotate(-3 11 8)"/>
            <rect x="14" y="1" width="4.2" height="15" rx="2" transform="rotate(8 16 8)"/>
            <rect x="17.4" y="3" width="4.2" height="13" rx="2" transform="rotate(18 19 9)"/>
          </g>
          <path d="M3.5 13h19l-2.4 12H5.9z" fill="#e23b2e" stroke="#a62118" stroke-width="1"/>
          <path d="M6.4 16h13.2l-.4 2.2H6.8z" fill="#fff" opacity=".85"/>
        </symbol>
      </defs>
    </svg>
    <span class="papa" style="left:4%;top:18%;width:26px;height:26px;--d:-1s;--t:9s;--r:-12deg;--dx:16px;--dy:-10px"><svg><use href="#fr-caja"/></svg></span>
    <span class="papa" style="left:13%;top:58%;width:9px;height:24px;--d:-4s;--t:7s;--r:20deg;--dx:10px;--dy:-14px"><svg><use href="#fr-palo"/></svg></span>
    <span class="papa" style="left:22%;top:12%;width:10px;height:26px;--d:-2s;--t:10s;--r:-25deg;--dx:-12px;--dy:12px"><svg><use href="#fr-palo"/></svg></span>
    <span class="papa" style="left:31%;top:52%;width:24px;height:24px;--d:-6s;--t:11s;--r:10deg;--dx:18px;--dy:-8px"><svg><use href="#fr-caja"/></svg></span>
    <span class="papa" style="left:40%;top:16%;width:9px;height:22px;--d:-3s;--t:8s;--r:35deg;--dx:12px;--dy:10px"><svg><use href="#fr-palo"/></svg></span>
    <span class="papa" style="left:48%;top:60%;width:10px;height:26px;--d:-7s;--t:9s;--r:-15deg;--dx:-14px;--dy:-12px"><svg><use href="#fr-palo"/></svg></span>
    <span class="papa" style="left:56%;top:14%;width:26px;height:26px;--d:-5s;--t:12s;--r:14deg;--dx:-16px;--dy:10px"><svg><use href="#fr-caja"/></svg></span>
    <span class="papa" style="left:65%;top:56%;width:9px;height:24px;--d:-8s;--t:7s;--r:-30deg;--dx:12px;--dy:-12px"><svg><use href="#fr-palo"/></svg></span>
    <span class="papa" style="left:73%;top:18%;width:10px;height:26px;--d:-1s;--t:10s;--r:22deg;--dx:-10px;--dy:14px"><svg><use href="#fr-palo"/></svg></span>
    <span class="papa" style="left:81%;top:54%;width:24px;height:24px;--d:-9s;--t:10s;--r:-10deg;--dx:14px;--dy:-10px"><svg><use href="#fr-caja"/></svg></span>
    <span class="papa" style="left:90%;top:16%;width:9px;height:22px;--d:-10s;--t:8s;--r:28deg;--dx:-12px;--dy:12px"><svg><use href="#fr-palo"/></svg></span>
    <span class="papa" style="left:94%;top:58%;width:10px;height:24px;--d:-11s;--t:9s;--r:-20deg;--dx:8px;--dy:-14px"><svg><use href="#fr-palo"/></svg></span>
  </div>
  <div class="derecha">
    <span>{{ ico('calendar') }} Octubre 2026 · Nuevo Chimbote</span>
    <a href="{{ url_for('index', tab='configuracion') }}" class="engranaje{{ ' activo' if tab=='configuracion' else '' }}" title="Configuración" aria-label="Configuración"><i class="ti ti-settings" aria-hidden="true"></i></a>
    <form method="post" action="{{ url_for('salir') }}" style="display:inline">
      <input type="hidden" name="csrf_token" value="{{ csrf_tok }}">
      <button class="engranaje" type="submit" title="Cerrar sesión" aria-label="Cerrar sesión"><i class="ti ti-logout" aria-hidden="true"></i></button>
    </form>
    <div class="quien"><b>{{ nombre }}</b>
      <span class="rol-chip"><i class="ti ti-{{ 'shield-check' if es_admin else 'user' }}" aria-hidden="true"></i>{{ rol_txt }}{% if puesto %} · {{ puesto }}{% endif %}</span></div>
    <div class="avatar" title="{{ nombre }} ({{ rol_txt }})">{{ ico('shield-check' if es_admin else 'user') }}</div>
  </div>
</header>

<nav>
  {% for id, icono, nombre_sec in secciones %}
    <a href="{{ url_for('index', tab=id) }}" class="{{ 'activo' if tab==id }}"><i class="ti ti-{{ icono }}" aria-hidden="true"></i>{{ nombre_sec }}</a>
  {% endfor %}
  <div class="decor-menu">{{ gallina_svg|safe }}</div>
</nav>

<main>
{% for cat, m in get_flashed_messages(with_categories=true) %}<div class="flash {{ cat }}">{{ m }}</div>{% endfor %}

{% if tab == 'dashboard' %}
  <section class="hero">
    <div class="txt">
      <div style="width:60px;height:60px;border-radius:50%;font-size:28px;background:linear-gradient(135deg,var(--p),var(--p-osc));color:#fff;display:flex;align-items:center;justify-content:center">{{ ico('chart-bar') }}</div>
      <div>
        <small>Utilidad estimada del periodo</small>
        <b>{{ ind.utilidad|soles }}</b>
        <span class="pie">Calculada con las ventas, mermas y costos fijos registrados</span>
      </div>
    </div>
    <div class="gallo">{{ gallo_svg|safe }}</div>
    <div class="ola">{{ ola_svg|safe }}</div>
  </section>

  <div class="bloques">
    <div class="bloque b-ambar">
      <div class="top"><div class="circ">{{ ico('coins') }}</div><span class="tend">{{ ico('trending-up') }}</span></div>
      <small>Ventas totales</small><b>{{ ind.ingresos|soles }}</b><span>Ingresos registrados</span>
    </div>
    <div class="bloque b-azul">
      <div class="top"><div class="circ">{{ ico('target') }}</div><span class="tend">{{ ico('trending-up') }}</span></div>
      <small>Punto de equilibrio</small><b>{{ ind.pe|soles if ind.pe else '—' }}</b><span>En ventas mensuales</span>
    </div>
    <div class="bloque {{ 'b-rojo' if ind.pct_mermas > 10 else 'b-verde' }}">
      <div class="top"><div class="circ">{{ ico('leaf') }}</div><span class="tend">{{ ico('circle-check') }}</span></div>
      <small>Mermas sobre insumos</small><b>{{ '%.1f'|format(ind.pct_mermas) }}%</b><span>Objetivo menor a 10%</span>
    </div>
    <div class="bloque b-amarillo">
      <div class="top"><div class="circ">{{ ico('wallet') }}</div></div>
      <small>Pagos digitales</small><b>{{ '%.1f'|format(ind.pct_digital) }}%</b><span>Yape, Plin y POS</span>
    </div>
  </div>

  <div class="card">
    {{ banner('chart-bar', 'Platos por margen', 'Margen de cada plato según su costo y precio de venta', 'naranja') }}
    {% for p in platos %}
    <div class="fila-plato">
      <span>{{ p['nombre'] }}</span>
      <div class="barra"><div style="width:{{ p['margen'] }}%;background:{{ 'var(--barra-v)' if p['margen'] >= 35 else 'var(--barra-r)' }}"></div></div>
      <span style="text-align:right">{{ p['margen'] }}%</span>
    </div>
    {% endfor %}
    <p class="nota">Verde: margen de 35% o más. Rojo: menos de 35%.</p>
  </div>

  <div class="card filas">
    {{ banner('file-text', 'Detalle por plato', 'Costo, precio y ganancia de cada plato del menú', 'gris') }}
    {% for p in platos %}
    <div><span>{{ p['nombre'] }}</span><span>Costo {{ p['costo']|soles }} · Precio {{ p['precio']|soles }} · Ganancia {{ (p['precio']-p['costo'])|soles }}</span></div>
    {% endfor %}
  </div>
{% endif %}

{% if tab == 'ventas' %}
  <div class="card">
    {{ banner('shopping-cart', 'Registrar venta', 'Anota cada venta por plato, canal y medio de pago', 'naranja') }}
    {{ registro('ventas', campos.ventas,
       [('fecha','Fecha'),('plato','Plato'),('cantidad','Cantidad'),('canal','Canal'),('pago','Pago'),('total','Total')], ventas,
       'naranja', 'Registrar venta') }}
  </div>
{% endif %}

{% if tab == 'mermas' %}
  <div class="card">
    {{ banner('box', 'Registrar merma', 'Anota los insumos desperdiciados en cocina y su costo', 'rojo') }}
    {{ registro('mermas', campos.mermas,
       [('fecha','Fecha'),('insumo','Insumo'),('cantidad','Cantidad'),('costo','Costo'),('causa','Causa')], mermas,
       'rojo', 'Registrar merma') }}
    <div class="res">Costo total de mermas registradas: {{ mermas_total|soles }}</div>
  </div>
{% endif %}

{% if tab == 'costos' %}
  <div class="grid2">
    <div class="card">
      {{ banner('helmet', 'Mano de Obra Directa (MOD)', 'Registra el costo de la mano de obra directa de tu producción', 'naranja') }}
      {{ registro('planilla_mod', campos.planilla_mod, [('rol','Puesto'),('sueldo','Sueldo')], mod, 'naranja', 'Agregar') }}
      <div class="total naranja">
        <div class="circ"><i class="ti ti-coins" aria-hidden="true"></i></div>
        <div><small>Total MOD:</small><b>{{ total_mod|soles }}</b></div>
        <i class="ti ti-trending-up tend" aria-hidden="true"></i>
      </div>
    </div>
    <div class="card">
      {{ banner('settings', 'Costos Indirectos (CIF)', 'Registra los costos que no se relacionan directamente con la producción', 'verde') }}
      {{ registro('costos_cif', campos.costos_cif, [('concepto','Concepto'),('monto','Monto')], cif, 'verde', 'Agregar') }}
      <div class="total verde">
        <div class="circ"><i class="ti ti-coins" aria-hidden="true"></i></div>
        <div><small>Total CIF:</small><b>{{ total_cif|soles }}</b></div>
        <i class="ti ti-trending-up tend" aria-hidden="true"></i>
      </div>
    </div>
  </div>
  <div class="card">
    {{ banner('calculator', 'Prorrateo por plato', 'Reparte MOD y CIF entre las unidades vendidas del mes', 'azul') }}
    <form class="reg azul" method="get">
      <input type="hidden" name="tab" value="costos">
      <label class="campo"><span class="lbl"><i class="ti ti-hash" aria-hidden="true"></i>Unidades vendidas al mes</span>
        <span class="caja"><input type="number" name="unid" value="{{ unid|int }}" min="1"></span></label>
      <div class="accion"><button class="btn azul" type="submit"><i class="ti ti-calculator" aria-hidden="true"></i>Calcular<i class="ti ti-chevron-right flecha" aria-hidden="true"></i></button></div>
    </form>
    <div class="res">MOD + CIF: {{ (total_mod+total_cif)|soles }}
      {% if pro %} → {{ pro|soles }} por plato{% endif %}</div>
  </div>
{% endif %}

{% if tab == 'fichas' %}
  <div class="card">
    {{ banner('file-text', 'Ficha técnica de costo unitario', 'Calcula el costo, la ganancia y el margen de un plato', 'naranja') }}
    <form class="reg" method="get">
      <input type="hidden" name="tab" value="fichas">
      <label class="campo"><span class="lbl"><i class="ti ti-coin" aria-hidden="true"></i>MPD (S/)</span><span class="caja"><input type="number" step="any" name="f_mpd" value="60"></span></label>
      <label class="campo"><span class="lbl"><i class="ti ti-user" aria-hidden="true"></i>MOD (S/)</span><span class="caja"><input type="number" step="any" name="f_mod" value="8"></span></label>
      <label class="campo"><span class="lbl"><i class="ti ti-file-text" aria-hidden="true"></i>CIF (S/)</span><span class="caja"><input type="number" step="any" name="f_cif" value="7"></span></label>
      <label class="campo"><span class="lbl"><i class="ti ti-tag" aria-hidden="true"></i>Precio de venta (S/)</span><span class="caja"><input type="number" step="any" name="f_precio" value="115"></span></label>
      <div class="accion"><button class="btn" type="submit"><i class="ti ti-calculator" aria-hidden="true"></i>Calcular<i class="ti ti-chevron-right flecha" aria-hidden="true"></i></button></div>
    </form>
    {% if ficha %}
    <div class="res">Costo total: {{ ficha.costo|soles }} · Ganancia: {{ ficha.ganancia|soles }} · Margen: {{ '%.1f'|format(ficha.margen) }}%</div>
    {% endif %}
  </div>
  <div class="card">
    {{ banner('tools-kitchen-2', 'Platos del menú', 'Costo, precio y margen de cada presentación', 'gris') }}
    <div class="tabla-wrap">
    <table>
      <tr><th>Plato</th><th>Costo</th><th>Precio</th><th>Margen</th></tr>
      {% for p in platos %}
      <tr><td>{{ p['nombre'] }}</td><td>{{ p['costo']|soles }}</td><td>{{ p['precio']|soles }}</td><td>{{ p['margen'] }}%</td></tr>
      {% endfor %}
    </table>
    </div>
  </div>
{% endif %}

{% if tab == 'equilibrio' %}
  <div class="card">
    {{ banner('scale', 'Cálculo del punto de equilibrio', 'Unidades mínimas que debes vender para no tener pérdidas', 'azul') }}
    <form class="reg azul" method="get">
      <input type="hidden" name="tab" value="equilibrio">
      <label class="campo"><span class="lbl"><i class="ti ti-coin" aria-hidden="true"></i>Costos fijos (S/)</span><span class="caja"><input type="number" step="any" name="e_fijos" value="56000"></span></label>
      <label class="campo"><span class="lbl"><i class="ti ti-tag" aria-hidden="true"></i>Precio (S/)</span><span class="caja"><input type="number" step="any" name="e_precio" value="115"></span></label>
      <label class="campo"><span class="lbl"><i class="ti ti-coin" aria-hidden="true"></i>Costo variable (S/)</span><span class="caja"><input type="number" step="any" name="e_var" value="75"></span></label>
      <label class="campo"><span class="lbl"><i class="ti ti-target" aria-hidden="true"></i>Meta (unidades)</span><span class="caja"><input type="number" name="e_meta" value="1800"></span></label>
      <div class="accion"><button class="btn azul" type="submit"><i class="ti ti-calculator" aria-hidden="true"></i>Calcular<i class="ti ti-chevron-right flecha" aria-hidden="true"></i></button></div>
    </form>
    {% if eq %}
      {% if eq.error %}
        <div class="flash">{{ eq.error }}</div>
      {% else %}
        <div class="res">{{ '{:,}'.format(eq.pe) }} unidades al mes ({{ eq.pe_ventas|soles }} en ventas)</div>
        <p>Margen de contribución: {{ eq.mc|soles }} por unidad</p>
        <p>Holgura sobre la meta: <b style="color:{{ 'var(--vd-t)' if eq.holg >= 0 else 'var(--rj-t)' }}">{{ '%+.1f'|format(eq.holg) }}%</b></p>
      {% endif %}
    {% endif %}
    <p class="nota">Punto de equilibrio = Costos fijos / (Precio − Costo variable unitario)</p>
  </div>
{% endif %}

{% if tab == 'reportes' %}
  <div class="card">
    {{ banner('report', 'Resultados por periodo', 'Ingresos, costos y margen por día, por semana o por mes', 'naranja') }}
    <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;margin:-4px 0 12px">
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <a href="{{ url_for('index', tab='reportes', periodo='diario') }}" class="btn-sec {{ 'activo-p' if periodo == 'diario' }}"><i class="ti ti-calendar-event" aria-hidden="true"></i>Diario</a>
        <a href="{{ url_for('index', tab='reportes', periodo='semanal') }}" class="btn-sec {{ 'activo-p' if periodo == 'semanal' }}"><i class="ti ti-calendar-week" aria-hidden="true"></i>Semanal</a>
        <a href="{{ url_for('index', tab='reportes', periodo='mensual') }}" class="btn-sec {{ 'activo-p' if periodo == 'mensual' }}"><i class="ti ti-calendar-month" aria-hidden="true"></i>Mensual</a>
      </div>
      <a href="{{ url_for('exportar', periodo=periodo) }}" class="btn-sec"><i class="ti ti-download" aria-hidden="true"></i>Exportar a CSV</a>
    </div>
    <div class="tabla-wrap">
    <table>
      <tr><th>{{ etiqueta_periodo }}</th><th>Ingresos</th><th>Costo variable</th><th>Margen bruto</th><th>Margen %</th></tr>
      {% for r in reportes %}
      <tr><td>{{ r.mes }}</td><td>{{ r.ing|soles }}</td><td>{{ r.cv|soles }}</td><td>{{ r.margen|soles }}</td><td>{{ '%.1f'|format(r.pct) }}%</td></tr>
      {% endfor %}
    </table>
    </div>
  </div>
{% endif %}

{% if tab == 'usuarios' %}
  <div class="card">
    {{ banner('users', 'Personal y accesos', 'Cada persona tiene un solo registro: nombre, puesto, usuario para entrar y su rol.', 'naranja') }}
    <div class="tabla-wrap">
    <table>
      <tr><th>Nombre</th><th>Usuario</th><th>Puesto</th><th>Rol</th><th>Estado</th>{% if es_admin %}<th></th>{% endif %}</tr>
      {% for p in personas %}
      <tr>
        <td>{{ p['nombre'] or '—' }}{% if p['usuario'] == usuario %} <small style="color:var(--mu)">(tú)</small>{% endif %}</td>
        <td>{{ p['usuario'] }}</td>
        <td>{{ p['puesto'] }}</td>
        <td>{{ 'Administrador' if p['rol'] == 'admin' else 'Usuario' }}</td>
        <td><span class="estado {{ 'on' if p['activo'] == 1 else 'off' }}">{{ 'Activo' if p['activo'] == 1 else 'Inactivo' }}</span></td>
        {% if es_admin %}
        <td class="acc">
          {% if p['usuario'] != usuario %}
          <form method="post" action="{{ url_for('cambiar_rol', id_=p['id']) }}">
            <input type="hidden" name="csrf_token" value="{{ csrf_tok }}">
            <button class="btn-sec" type="submit">{{ 'Hacer usuario' if p['rol'] == 'admin' else 'Hacer administrador' }}</button>
          </form>
          <form method="post" action="{{ url_for('cambiar_estado', id_=p['id']) }}">
            <input type="hidden" name="csrf_token" value="{{ csrf_tok }}">
            <button class="btn-sec" type="submit">{{ 'Desactivar' if p['activo'] == 1 else 'Activar' }}</button>
          </form>
          <form method="post" action="{{ url_for('eliminar_persona', id_=p['id']) }}">
            <input type="hidden" name="csrf_token" value="{{ csrf_tok }}">
            <button class="btn-sec rojo" type="submit"><i class="ti ti-trash" aria-hidden="true"></i>Eliminar</button>
          </form>
          {% endif %}
        </td>
        {% endif %}
      </tr>
      {% endfor %}
    </table>
    </div>
    {% if not es_admin %}<p class="solo-lectura">Solo el administrador puede agregar personas, cambiar roles o activar cuentas.</p>{% endif %}
  </div>

  {% if es_admin %}
  <div class="card">
    {{ banner('user-plus', 'Agregar persona', 'Crea el registro y su cuenta de acceso en un solo paso', 'verde') }}
    <form class="reg verde" method="post" action="{{ url_for('crear_persona') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_tok }}">
      <label class="campo"><span class="lbl"><i class="ti ti-id" aria-hidden="true"></i>Nombre completo</span>
        <span class="caja"><input type="text" name="nombre" maxlength="60" placeholder="Ej. María López" required></span></label>
      <label class="campo"><span class="lbl"><i class="ti ti-briefcase" aria-hidden="true"></i>Puesto</span>
        <span class="caja sel"><select name="puesto" required>
          {% for pu in puestos_persona %}<option>{{ pu }}</option>{% endfor %}
        </select></span></label>
      <label class="campo"><span class="lbl"><i class="ti ti-user" aria-hidden="true"></i>Usuario para entrar</span>
        <span class="caja"><input type="text" name="usuario" maxlength="30" pattern="[A-Za-z0-9_.]{3,30}" placeholder="Ej. caja1" required></span></label>
      <label class="campo"><span class="lbl"><i class="ti ti-key" aria-hidden="true"></i>Contraseña inicial</span>
        <span class="caja"><input type="password" name="clave" minlength="10" maxlength="128" autocomplete="new-password" placeholder="Mínimo 10 caracteres" required></span></label>
      <label class="campo"><span class="lbl"><i class="ti ti-shield" aria-hidden="true"></i>Rol de acceso</span>
        <span class="caja sel"><select name="rol" required>
          <option value="usuario">Usuario</option>
          <option value="admin">Administrador</option>
        </select></span></label>
      <div class="accion"><button class="btn verde" type="submit"><i class="ti ti-plus" aria-hidden="true"></i>Agregar persona<i class="ti ti-chevron-right flecha" aria-hidden="true"></i></button></div>
    </form>
  </div>

  <div class="card">
    {{ banner('lock', 'Restablecer contraseña', 'Asigna una nueva contraseña a una persona', 'azul') }}
    <form class="reg azul" method="post" action="{{ url_for('restablecer_clave') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_tok }}">
      <label class="campo"><span class="lbl"><i class="ti ti-user" aria-hidden="true"></i>Persona</span>
        <span class="caja sel"><select name="cuenta_id" required>
          {% for p in personas %}<option value="{{ p['id'] }}">{{ p['nombre'] or p['usuario'] }} ({{ p['usuario'] }})</option>{% endfor %}
        </select></span></label>
      <label class="campo"><span class="lbl"><i class="ti ti-key" aria-hidden="true"></i>Nueva contraseña</span>
        <span class="caja"><input type="password" name="nueva" minlength="10" maxlength="128" autocomplete="new-password" required></span></label>
      <div class="accion"><button class="btn azul" type="submit"><i class="ti ti-device-floppy" aria-hidden="true"></i>Guardar contraseña<i class="ti ti-chevron-right flecha" aria-hidden="true"></i></button></div>
    </form>
  </div>
  <div class="card">
    {{ banner('edit', 'Editar persona', 'Cambia el nombre o el puesto de alguien del equipo', 'azul') }}
    <form class="reg azul" method="post" action="{{ url_for('editar_persona') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_tok }}">
      <label class="campo"><span class="lbl"><i class="ti ti-user" aria-hidden="true"></i>Persona</span>
        <span class="caja sel"><select name="cuenta_id" required>
          {% for p in personas %}<option value="{{ p['id'] }}">{{ p['nombre'] or p['usuario'] }} ({{ p['usuario'] }})</option>{% endfor %}
        </select></span></label>
      <label class="campo"><span class="lbl"><i class="ti ti-id" aria-hidden="true"></i>Nuevo nombre</span>
        <span class="caja"><input type="text" name="nombre" maxlength="60" required></span></label>
      <label class="campo"><span class="lbl"><i class="ti ti-briefcase" aria-hidden="true"></i>Nuevo puesto</span>
        <span class="caja sel"><select name="puesto" required>
          {% for pu in puestos_persona %}<option>{{ pu }}</option>{% endfor %}
        </select></span></label>
      <div class="accion"><button class="btn azul" type="submit"><i class="ti ti-device-floppy" aria-hidden="true"></i>Guardar cambios<i class="ti ti-chevron-right flecha" aria-hidden="true"></i></button></div>
    </form>
  </div>

  <div class="card">
    {{ banner('history', 'Actividad reciente', 'Últimas acciones del administrador (hora de Supabase, UTC)', 'gris') }}
    <div class="tabla-wrap">
    <table>
      <tr><th>Cuándo</th><th>Quién</th><th>Acción</th><th>Detalle</th></tr>
      {% for a in auditoria %}
      <tr><td>{{ a['momento'].strftime('%d/%m %H:%M') }}</td><td>{{ a['quien'] }}</td><td>{{ a['accion'] }}</td><td>{{ a['detalle'] }}</td></tr>
      {% else %}
      <tr><td colspan="4">Todavía no hay actividad registrada.</td></tr>
      {% endfor %}
    </table>
    </div>
  </div>
  {% endif %}
{% endif %}

{% if tab == 'configuracion' %}
  {% if es_admin %}
  <div class="card">
    {{ banner('palette', 'Apariencia', 'Elige los colores de la aplicación. Se aplican a todas las secciones y a todos los usuarios.', 'naranja') }}
    <form class="reg" method="post" action="{{ url_for('guardar_colores') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_tok }}">
      <label class="campo"><span class="lbl"><i class="ti ti-color-picker" aria-hidden="true"></i>Color principal</span>
        <span class="caja"><input type="color" name="principal" id="c_principal" value="{{ colores.principal }}"></span></label>
      <label class="campo"><span class="lbl"><i class="ti ti-color-picker" aria-hidden="true"></i>Color secundario</span>
        <span class="caja"><input type="color" name="secundario" id="c_secundario" value="{{ colores.secundario }}"></span></label>
      <label class="campo"><span class="lbl"><i class="ti ti-color-picker" aria-hidden="true"></i>Color de acento</span>
        <span class="caja"><input type="color" name="acento" id="c_acento" value="{{ colores.acento }}"></span></label>
      <div class="accion">
        <button class="btn" type="submit"><i class="ti ti-device-floppy" aria-hidden="true"></i>Guardar colores<i class="ti ti-chevron-right flecha" aria-hidden="true"></i></button>
      </div>
    </form>
    <form method="post" action="{{ url_for('restaurar_colores') }}" style="margin-top:12px">
      <input type="hidden" name="csrf_token" value="{{ csrf_tok }}">
      <button class="btn-sec" type="submit"><i class="ti ti-refresh" aria-hidden="true"></i>Restaurar colores originales</button>
    </form>
  </div>

  <div class="card">
    {{ banner('wand', 'Combinaciones sugeridas', 'Toca una para verla en la vista previa y luego guarda', 'gris') }}
    <div class="paletas">
      {% for pr in presets %}
      <button type="button" class="paleta" onclick='aplicarPreset({{ pr|tojson }})'>
        <span class="nombre">{{ pr.nombre }}</span>
        <span class="muestras">
          <span style="background:{{ pr.principal }}"></span>
          <span style="background:{{ pr.secundario }}"></span>
          <span style="background:{{ pr.acento }}"></span>
        </span>
      </button>
      {% endfor %}
    </div>
  </div>

  <div class="card">
    {{ banner('eye', 'Vista previa', 'Así se verán los botones, encabezados y etiquetas con los colores elegidos', 'azul') }}
    <div class="vista-previa" id="preview">
      <div class="pv-barra">La Granja Linda · Gestión de costos</div>
      <div class="pv-cuerpo">
        <button class="pv-btn p" type="button"><i class="ti ti-plus" aria-hidden="true"></i>Agregar</button>
        <button class="pv-btn s" type="button"><i class="ti ti-device-floppy" aria-hidden="true"></i>Guardar</button>
        <span class="pv-chip">Etiqueta de acento</span>
        <div class="pv-tarjeta"><small style="color:var(--mu)">Utilidad estimada</small><b>S/ 15,200.50</b></div>
      </div>
    </div>
  </div>
  {% else %}
  <div class="card">
    {{ banner('palette', 'Apariencia', 'Estos son los colores de la aplicación. Solo el administrador puede cambiarlos.', 'naranja') }}
    <div class="paletas">
      <div class="paleta"><span class="nombre">Color principal</span><span class="muestras"><span style="background:{{ colores.principal }}"></span></span></div>
      <div class="paleta"><span class="nombre">Color secundario</span><span class="muestras"><span style="background:{{ colores.secundario }}"></span></span></div>
      <div class="paleta"><span class="nombre">Color de acento</span><span class="muestras"><span style="background:{{ colores.acento }}"></span></span></div>
    </div>
  </div>
  {% endif %}

  <div class="card">
    {{ banner('id', 'Mi perfil', 'Así aparece tu nombre en la aplicación', 'verde') }}
    <form class="reg verde" method="post" action="{{ url_for('actualizar_perfil') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_tok }}">
      <label class="campo"><span class="lbl"><i class="ti ti-id" aria-hidden="true"></i>Nombre completo</span>
        <span class="caja"><input type="text" name="nombre" value="{{ nombre }}" maxlength="60" required></span></label>
      <div class="accion"><button class="btn verde" type="submit"><i class="ti ti-device-floppy" aria-hidden="true"></i>Guardar perfil<i class="ti ti-chevron-right flecha" aria-hidden="true"></i></button></div>
    </form>
  </div>

  <div class="card">
    {{ banner('lock', 'Mi contraseña', 'Cambia la contraseña de tu cuenta. Debe tener al menos 10 caracteres.', 'gris') }}
    <form class="reg" method="post" action="{{ url_for('cambiar_clave') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_tok }}">
      <label class="campo"><span class="lbl"><i class="ti ti-key" aria-hidden="true"></i>Contraseña actual</span>
        <span class="caja"><input type="password" name="actual" autocomplete="current-password" required maxlength="200"></span></label>
      <label class="campo"><span class="lbl"><i class="ti ti-key" aria-hidden="true"></i>Nueva contraseña</span>
        <span class="caja"><input type="password" name="nueva" autocomplete="new-password" required minlength="10" maxlength="128"></span></label>
      <label class="campo"><span class="lbl"><i class="ti ti-key" aria-hidden="true"></i>Repite la nueva</span>
        <span class="caja"><input type="password" name="confirmar" autocomplete="new-password" required minlength="10" maxlength="128"></span></label>
      <div class="accion"><button class="btn gris" type="submit"><i class="ti ti-device-floppy" aria-hidden="true"></i>Cambiar contraseña<i class="ti ti-chevron-right flecha" aria-hidden="true"></i></button></div>
    </form>
  </div>
{% endif %}
</main>

<script>
(function () {
  var glow = document.getElementById('cursor-glow');
  var reducir = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!glow || reducir || !window.matchMedia('(pointer: fine)').matches) return;
  var mx = window.innerWidth / 2, my = window.innerHeight / 2, gx = mx, gy = my;
  window.addEventListener('pointermove', function (e) {
    mx = e.clientX; my = e.clientY; glow.style.opacity = '1';
  });
  (function bucle() {
    gx += (mx - gx) * 0.12;
    gy += (my - gy) * 0.12;
    glow.style.transform = 'translate(' + (gx - 160) + 'px,' + (gy - 160) + 'px)';
    requestAnimationFrame(bucle);
  })();
})();

document.addEventListener('pointerdown', function (e) {
  var b = e.target.closest('button, .btn, .btn-sec, nav a, .engranaje');
  if (!b || b.disabled) return;
  var r = document.createElement('span');
  var rect = b.getBoundingClientRect();
  var s = Math.max(rect.width, rect.height);
  r.className = 'ripple';
  r.style.width = r.style.height = s + 'px';
  r.style.left = (e.clientX - rect.left - s / 2) + 'px';
  r.style.top = (e.clientY - rect.top - s / 2) + 'px';
  b.appendChild(r);
  setTimeout(function () { r.remove(); }, 650);
});

(function () {
  var ids = ['c_principal', 'c_secundario', 'c_acento'];
  var pv = document.getElementById('preview');
  if (!pv) return;
  function actualizar() {
    var v = ids.map(function (id) { var el = document.getElementById(id); return el ? el.value : '#000000'; });
    pv.style.setProperty('--pv-p', v[0]);
    pv.style.setProperty('--pv-s', v[1]);
    pv.style.setProperty('--pv-a', v[2]);
  }
  ids.forEach(function (id) {
    var el = document.getElementById(id);
    if (el) el.addEventListener('input', actualizar);
  });
  window.aplicarPreset = function (pr) {
    document.getElementById('c_principal').value = pr.principal;
    document.getElementById('c_secundario').value = pr.secundario;
    document.getElementById('c_acento').value = pr.acento;
    actualizar();
  };
  actualizar();
})();
</script>
</body>
</html>
"""


# ---------- Acceso ----------
def render_auth(vista="login", tipo="usuario"):
    colores = obtener_colores()
    return render_template_string(AUTH_HTML, vista=vista, tema=tema_css(colores),
                                  csrf_tok=token_csrf(), logo_img=logo_archivo(),
                                  casa_svg=CASA_SVG, tipo=tipo,
                                  puestos_persona=PUESTOS_PERSONA)


@app.route("/login", methods=["GET", "POST"])
def login():
    token_csrf()
    tipo = request.form.get("tipo", "usuario") if request.method == "POST" else request.args.get("tipo", "usuario")
    if tipo not in ("usuario", "admin"):
        tipo = "usuario"

    if request.method == "POST":
        ip = ip_actual()
        if bloqueado(ip):
            flash("Demasiados intentos fallidos. Espera 15 minutos e inténtalo de nuevo.", "error")
            return render_auth("login", tipo)
        usuario = request.form.get("usuario", "").strip()[:50]
        clave = request.form.get("clave", "")[:200]
        fila = consultar("SELECT * FROM cuentas WHERE usuario=%s", (usuario,), uno=True)
        if fila and check_password_hash(fila["clave"], clave):
            if fila["activo"] != 1:
                flash("Esta cuenta está desactivada. Pide al administrador que la active.", "error")
                return render_auth("login", tipo)
            if tipo == "admin" and fila["rol"] != "admin":
                flash("Esta cuenta no es de administrador. Selecciona la pestaña Usuario.", "error")
                return render_auth("login", tipo)
            intentos.pop(ip, None)
            session.clear()
            session.permanent = True
            session["usuario"] = fila["usuario"]
            session["nombre"] = fila["nombre"] or fila["usuario"]
            session["rol"] = fila["rol"]
            session["puesto"] = fila["puesto"]
            token_csrf()
            return redirect(url_for("index"))
        registrar_fallo(ip)
        flash("Usuario o contraseña incorrectos.", "error")
    return render_auth("login", tipo)


@app.route("/registro", methods=["GET", "POST"])
def registro():
    token_csrf()
    if request.method == "POST":
        ip = ip_actual()
        if not registro_permitido(ip):
            flash("Se alcanzó el límite de cuentas creadas desde tu conexión. Inténtalo más tarde.", "error")
            return render_auth("registro")

        nombre = request.form.get("nombre", "").strip()
        puesto = request.form.get("puesto", "")
        usuario = request.form.get("usuario", "").strip()
        clave = request.form.get("clave", "")
        confirmar = request.form.get("confirmar", "")

        error = None
        if not 3 <= len(nombre) <= 60:
            error = "Escribe tu nombre completo (entre 3 y 60 caracteres)."
        elif puesto not in PUESTOS_PERSONA:
            error = "Selecciona tu puesto en el negocio."
        elif not re.fullmatch(r"[A-Za-z0-9_.]{3,30}", usuario):
            error = "El nombre de usuario debe tener de 3 a 30 letras, números, punto o guion bajo."
        elif len(clave) < 10 or len(clave) > 128:
            error = "La contraseña debe tener entre 10 y 128 caracteres."
        elif clave != confirmar:
            error = "La confirmación no coincide con la contraseña."

        if error:
            flash(error, "error")
            return render_auth("registro")

        try:
            # Las cuentas públicas siempre son de tipo Usuario y quedan activas
            ejecutar("""INSERT INTO cuentas(usuario, clave, rol, nombre, puesto, activo)
                        VALUES(%s,%s,'usuario',%s,%s,1)""",
                     (usuario, generate_password_hash(clave), nombre, puesto))
        except psycopg2.IntegrityError:
            flash("Ese nombre de usuario ya está en uso. Elige otro.", "error")
            return render_auth("registro")

        registros.setdefault(ip, []).append(time.time())
        flash("Cuenta creada. Ya puedes iniciar sesión.", "ok")
        return redirect(url_for("login"))
    return render_auth("registro")


@app.route("/salir", methods=["POST"])
def salir():
    session.clear()
    return redirect(url_for("login"))


@app.route("/cuenta/clave", methods=["POST"])
def cambiar_clave():
    actual = request.form.get("actual", "")
    nueva = request.form.get("nueva", "")
    confirmar = request.form.get("confirmar", "")
    if len(nueva) < 10 or len(nueva) > 128:
        flash("La nueva contraseña debe tener entre 10 y 128 caracteres.", "error")
    elif nueva != confirmar:
        flash("La confirmación no coincide con la nueva contraseña.", "error")
    else:
        fila = consultar("SELECT * FROM cuentas WHERE usuario=%s", (session["usuario"],), uno=True)
        if fila and check_password_hash(fila["clave"], actual):
            ejecutar("UPDATE cuentas SET clave=%s WHERE usuario=%s",
                     (generate_password_hash(nueva), session["usuario"]))
            flash("Contraseña actualizada.", "ok")
        else:
            flash("La contraseña actual no es correcta.", "error")
    return redirect(url_for("index", tab="configuracion"))


# ---------- Rutas ----------
PERIODOS_REPORTE = {
    "diario": ("Día", "fecha"),
    "semanal": ("Semana (desde el lunes)", "semana"),
    "mensual": ("Mes", "mes"),
}
EXPR_PERIODO = {
    "diario": "v.fecha",
    "semanal": "TO_CHAR(DATE_TRUNC('week', v.fecha::date), 'YYYY-MM-DD')",
    "mensual": "LEFT(v.fecha,7)",
}


def reporte_por_periodo(periodo):
    """Ingresos, costos y margen agrupados por día, semana o mes."""
    expr = EXPR_PERIODO[periodo]
    filas = consultar(f"""SELECT {expr} AS periodo, SUM(v.cantidad*p.precio) AS ing,
                          SUM(v.cantidad*p.costo) AS cv FROM ventas v
                          JOIN platos p ON p.nombre=v.plato
                          GROUP BY {expr} ORDER BY periodo""")
    reportes = []
    for r in filas:
        ing, cv = r["ing"] or 0, r["cv"] or 0
        m = ing - cv
        reportes.append({"mes": r["periodo"], "ing": ing, "cv": cv, "margen": m,
                         "pct": m / ing * 100 if ing else 0})
    return reportes


@app.route("/")
def index():
    tab = request.args.get("tab", "dashboard")
    # La sección Usuarios es solo para administradores
    if tab == "usuarios" and not es_admin():
        flash("La sección Usuarios solo la puede ver el administrador.", "error")
        return redirect(url_for("index", tab="dashboard"))

    # Solo se consulta lo que la sección abierta necesita (menos viajes a la base = más rápido)
    platos, ventas, mermas, mod, cif, personas, reportes = [], [], [], [], [], [], []
    ind = None
    if tab in ("dashboard", "fichas"):
        platos = [dict(p, margen=float(p["margen"])) for p in consultar("""
            SELECT nombre, costo, precio,
                   ROUND((((precio-costo)*100.0/precio))::numeric, 1) AS margen
            FROM platos ORDER BY margen DESC""")]
    if tab == "dashboard":
        ind = indicadores()
    if tab == "ventas":
        ventas = consultar("""SELECT v.*, COALESCE(v.cantidad*p.precio, 0) AS total
                              FROM ventas v LEFT JOIN platos p ON p.nombre=v.plato
                              ORDER BY v.id DESC""")
    if tab == "mermas":
        mermas = consultar("SELECT * FROM mermas ORDER BY id DESC")
    if tab == "costos":
        mod = consultar("SELECT * FROM planilla_mod ORDER BY id")
        cif = consultar("SELECT * FROM costos_cif ORDER BY id")
    if tab == "usuarios" and es_admin():
        personas = consultar("SELECT id, usuario, nombre, puesto, rol, activo FROM cuentas ORDER BY id")
    periodo = request.args.get("periodo", "mensual")
    if periodo not in PERIODOS_REPORTE:
        periodo = "mensual"
    if tab == "reportes":
        reportes = reporte_por_periodo(periodo)
    nombres = ([r["nombre"] for r in consultar("SELECT nombre FROM platos ORDER BY id")]
               if tab == "ventas" else [])

    total_mod = sum(r["sueldo"] for r in mod)
    total_cif = sum(r["monto"] for r in cif)
    unid = num_arg("unid", "1800")
    pro = (total_mod + total_cif) / unid if unid else None

    ficha = None
    vals = [num_arg(k, d) for k, d in [("f_mpd", "60"), ("f_mod", "8"), ("f_cif", "7"), ("f_precio", "115")]]
    if None not in vals:
        mpd, mo, cf, precio = vals
        costo = mpd + mo + cf
        ficha = {"costo": costo, "ganancia": precio - costo,
                 "margen": (precio - costo) / precio * 100 if precio else 0}

    eq = None
    vals = [num_arg(k, d) for k, d in [("e_fijos", "56000"), ("e_precio", "115"),
                                       ("e_var", "75"), ("e_meta", "1800")]]
    if None not in vals:
        fijos, precio, var, meta = vals
        mc = precio - var
        if mc <= 0:
            eq = {"error": "El precio debe ser mayor al costo variable."}
        else:
            pe = math.ceil(fijos / mc)
            eq = {"pe": pe, "pe_ventas": pe * precio, "mc": mc,
                  "holg": (meta - pe) / meta * 100 if meta else 0}

    precios_platos = ({r["nombre"]: float(r["precio"]) for r in consultar("SELECT nombre, precio FROM platos")}
                      if tab == "ventas" else {})
    auditoria = (consultar("SELECT * FROM auditoria ORDER BY id DESC LIMIT 50")
                 if (tab == "usuarios" and es_admin()) else [])
    colores = obtener_colores()
    rol = session.get("rol", "usuario")

    return render_template_string(
        PLANTILLA, tab=tab, hoy=date.today().isoformat(),
        secciones=[x for x in SECCIONES if x[0] != "usuarios" or es_admin()],
        logo_img=logo_archivo(), casa_svg=CASA_SVG, gallo_svg=GALLO_SVG,
        ola_svg=OLA_SVG, gallina_svg=GALLINA_SVG, iconos=ICONOS,
        ind=ind, platos=platos, ventas=ventas, mermas=mermas,
        mermas_total=sum(m["costo"] for m in mermas), mod=mod, cif=cif,
        total_mod=total_mod, total_cif=total_cif, unid=unid, pro=pro,
        personas=personas, auditoria=auditoria, puestos_persona=PUESTOS_PERSONA, reportes=reportes,
        periodo=periodo, etiqueta_periodo=PERIODOS_REPORTE[periodo][0], precios_platos=precios_platos,
        ficha=ficha, eq=eq, campos={t: campos_de(t, nombres) for t in CAMPOS},
        colores=colores, tema=tema_css(colores), presets=PRESETS,
        usuario=session.get("usuario", ""), nombre=session.get("nombre", ""),
        rol_txt=ROL_CUENTA.get(rol, "Usuario"), puesto=session.get("puesto", ""),
        es_admin=es_admin(), csrf_tok=token_csrf(),
    )


@app.route("/agregar/<tabla>", methods=["POST"])
def agregar(tabla):
    if tabla not in CAMPOS:
        abort(404)
    tab = request.form.get("tab", "dashboard")
    if not es_admin() and tabla not in REGISTRO_USUARIO:
        flash("Tu cuenta no puede registrar en esta sección.", "error")
        return redirect(url_for("index", tab=tab))
    try:
        datos = {n: valor_de(t, request.form.get(n), e) for n, e, t, _ in CAMPOS[tabla]}
    except ValueError as err:
        flash(str(err), "error")
        return redirect(url_for("index", tab=tab))
    if tabla in REGISTRO_USUARIO:
        datos["registrado_por"] = session["usuario"]
    cols = ", ".join(datos)
    marcas = ", ".join(["%s"] * len(datos))
    ejecutar(f"INSERT INTO {tabla} ({cols}) VALUES ({marcas})", list(datos.values()))
    return redirect(url_for("index", tab=tab))


@app.route("/eliminar/<tabla>/<int:id_>", methods=["POST"])
def eliminar(tabla, id_):
    if tabla not in CAMPOS:
        abort(404)
    tab = request.form.get("tab", "dashboard")
    if es_admin():
        ejecutar(f"DELETE FROM {tabla} WHERE id=%s", (id_,))
        auditar("eliminar", f"{tabla} #{id_}")
    elif tabla in REGISTRO_USUARIO:
        # El usuario solo puede borrar los registros que él hizo hoy
        filas = ejecutar(f"DELETE FROM {tabla} WHERE id=%s AND registrado_por=%s AND fecha=%s",
                         (id_, session["usuario"], date.today().isoformat()))
        if not filas:
            flash("Solo puedes eliminar los registros que hiciste hoy.", "error")
    else:
        flash("Esta acción solo la puede hacer el administrador.", "error")
    return redirect(url_for("index", tab=tab))


@app.route("/personas/crear", methods=["POST"])
def crear_persona():
    if solo_admin():
        return redirect(url_for("index", tab="usuarios"))
    nombre = request.form.get("nombre", "").strip()
    puesto = request.form.get("puesto", "")
    usuario = request.form.get("usuario", "").strip()
    clave = request.form.get("clave", "")
    rol = request.form.get("rol", "usuario")
    if not 3 <= len(nombre) <= 60:
        flash("El nombre debe tener entre 3 y 60 caracteres.", "error")
    elif puesto not in PUESTOS_PERSONA:
        flash("Puesto no válido.", "error")
    elif not re.fullmatch(r"[A-Za-z0-9_.]{3,30}", usuario):
        flash("El usuario debe tener de 3 a 30 letras, números, punto o guion bajo.", "error")
    elif len(clave) < 10 or len(clave) > 128:
        flash("La contraseña debe tener entre 10 y 128 caracteres.", "error")
    elif rol not in ROL_CUENTA:
        flash("Rol no válido.", "error")
    else:
        try:
            ejecutar("INSERT INTO cuentas(usuario, clave, rol, nombre, puesto, activo) VALUES(%s,%s,%s,%s,%s,1)",
                     (usuario, generate_password_hash(clave), rol, nombre, puesto))
            auditar("crear persona", f"{usuario} ({rol}) - {nombre}")
            flash(f"{nombre} fue agregado como {ROL_CUENTA[rol]}.", "ok")
        except psycopg2.IntegrityError:
            flash("Ese usuario ya existe.", "error")
    return redirect(url_for("index", tab="usuarios"))


@app.route("/personas/<int:id_>/rol", methods=["POST"])
def cambiar_rol(id_):
    if solo_admin():
        return redirect(url_for("index", tab="usuarios"))
    fila = consultar("SELECT usuario, rol, activo FROM cuentas WHERE id=%s", (id_,), uno=True)
    if not fila:
        flash("Persona no encontrada.", "error")
    elif fila["usuario"] == session["usuario"]:
        flash("No puedes cambiar tu propio rol.", "error")
    elif fila["rol"] == "admin" and quedaria_sin_admin(fila):
        flash("Debe quedar al menos un administrador activo.", "error")
    else:
        nuevo = "usuario" if fila["rol"] == "admin" else "admin"
        ejecutar("UPDATE cuentas SET rol=%s WHERE id=%s", (nuevo, id_))
        auditar("cambiar rol", f"{fila['usuario']} -> {nuevo}")
        flash(f"'{fila['usuario']}' ahora es {ROL_CUENTA[nuevo]}.", "ok")
    return redirect(url_for("index", tab="usuarios"))


@app.route("/personas/<int:id_>/estado", methods=["POST"])
def cambiar_estado(id_):
    if solo_admin():
        return redirect(url_for("index", tab="usuarios"))
    fila = consultar("SELECT usuario, rol, activo FROM cuentas WHERE id=%s", (id_,), uno=True)
    if not fila:
        flash("Persona no encontrada.", "error")
    elif fila["usuario"] == session["usuario"]:
        flash("No puedes desactivar tu propia cuenta.", "error")
    elif fila["activo"] == 1 and quedaria_sin_admin(fila):
        flash("Debe quedar al menos un administrador activo.", "error")
    else:
        nuevo = 0 if fila["activo"] == 1 else 1
        ejecutar("UPDATE cuentas SET activo=%s WHERE id=%s", (nuevo, id_))
        auditar("cambiar estado", f"{fila['usuario']} -> {'activo' if nuevo else 'inactivo'}")
        flash(f"'{fila['usuario']}' {'activado' if nuevo else 'desactivado'}.", "ok")
    return redirect(url_for("index", tab="usuarios"))


@app.route("/personas/<int:id_>/eliminar", methods=["POST"])
def eliminar_persona(id_):
    if solo_admin():
        return redirect(url_for("index", tab="usuarios"))
    fila = consultar("SELECT usuario, rol, activo FROM cuentas WHERE id=%s", (id_,), uno=True)
    if not fila:
        flash("Persona no encontrada.", "error")
    elif fila["usuario"] == session["usuario"]:
        flash("No puedes eliminar tu propia cuenta.", "error")
    elif quedaria_sin_admin(fila):
        flash("Debe quedar al menos un administrador activo.", "error")
    else:
        ejecutar("DELETE FROM cuentas WHERE id=%s", (id_,))
        auditar("eliminar persona", fila["usuario"])
        flash(f"'{fila['usuario']}' fue eliminado.", "ok")
    return redirect(url_for("index", tab="usuarios"))


@app.route("/personas/clave", methods=["POST"])
def restablecer_clave():
    if solo_admin():
        return redirect(url_for("index", tab="usuarios"))
    cuenta_id = request.form.get("cuenta_id", "")
    nueva = request.form.get("nueva", "")
    if not cuenta_id.isdigit():
        flash("Selecciona una persona válida.", "error")
    elif len(nueva) < 10 or len(nueva) > 128:
        flash("La contraseña debe tener entre 10 y 128 caracteres.", "error")
    else:
        afectadas = ejecutar("UPDATE cuentas SET clave=%s WHERE id=%s",
                             (generate_password_hash(nueva), int(cuenta_id)))
        if afectadas:
            auditar("restablecer clave", f"cuenta #{cuenta_id}")
        flash("Contraseña restablecida." if afectadas else "Persona no encontrada.",
              "ok" if afectadas else "error")
    return redirect(url_for("index", tab="usuarios"))


@app.route("/personas/editar", methods=["POST"])
def editar_persona():
    if solo_admin():
        return redirect(url_for("index", tab="usuarios"))
    cuenta_id = request.form.get("cuenta_id", "")
    nombre = request.form.get("nombre", "").strip()
    puesto = request.form.get("puesto", "")
    if not cuenta_id.isdigit():
        flash("Selecciona una persona válida.", "error")
    elif not 3 <= len(nombre) <= 60:
        flash("El nombre debe tener entre 3 y 60 caracteres.", "error")
    elif puesto not in PUESTOS_PERSONA:
        flash("Puesto no válido.", "error")
    else:
        afectadas = ejecutar("UPDATE cuentas SET nombre=%s, puesto=%s WHERE id=%s",
                             (nombre, puesto, int(cuenta_id)))
        if afectadas:
            auditar("editar persona", f"id {cuenta_id}: {nombre} / {puesto}")
            flash("Datos de la persona actualizados.", "ok")
        else:
            flash("Persona no encontrada.", "error")
    return redirect(url_for("index", tab="usuarios"))


@app.route("/cuenta/perfil", methods=["POST"])
def actualizar_perfil():
    nombre = request.form.get("nombre", "").strip()
    if not 3 <= len(nombre) <= 60:
        flash("El nombre debe tener entre 3 y 60 caracteres.", "error")
    else:
        ejecutar("UPDATE cuentas SET nombre=%s WHERE usuario=%s", (nombre, session["usuario"]))
        session["nombre"] = nombre
        flash("Tu perfil fue actualizado.", "ok")
    return redirect(url_for("index", tab="configuracion"))


@app.route("/configuracion/guardar", methods=["POST"])
def guardar_colores():
    if solo_admin():
        return redirect(url_for("index", tab="configuracion"))
    nuevos = {}
    for clave in COLORES_DEFECTO:
        valor = (request.form.get(clave) or "").strip().lower()
        if not re.fullmatch(r"#[0-9a-f]{6}", valor):
            flash("Los colores deben tener el formato #RRGGBB.", "error")
            return redirect(url_for("index", tab="configuracion"))
        nuevos[clave] = valor
    for clave, valor in nuevos.items():
        ejecutar("""INSERT INTO configuracion(clave, valor) VALUES(%s, %s)
                    ON CONFLICT(clave) DO UPDATE SET valor = EXCLUDED.valor""", (clave, valor))
    flash("Colores guardados. Se aplican a todas las secciones.", "ok")
    return redirect(url_for("index", tab="configuracion"))


@app.route("/configuracion/restaurar", methods=["POST"])
def restaurar_colores():
    if solo_admin():
        return redirect(url_for("index", tab="configuracion"))
    ejecutar("DELETE FROM configuracion")
    flash("Se restauraron los colores originales.", "ok")
    return redirect(url_for("index", tab="configuracion"))


@app.route("/exportar.csv")
def exportar():
    periodo = request.args.get("periodo", "mensual")
    if periodo not in PERIODOS_REPORTE:
        periodo = "mensual"
    salida = io.StringIO()
    w = csv.writer(salida)
    w.writerow([PERIODOS_REPORTE[periodo][0], "Ingresos", "Costo variable", "Margen bruto", "Margen %"])
    for r in reporte_por_periodo(periodo):
        w.writerow([r["mes"], round(r["ing"], 2), round(r["cv"], 2),
                    round(r["margen"], 2), round(r["pct"], 1)])
    return Response(salida.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=reporte_{periodo}.csv"})


if __name__ == "__main__":
    app.run(debug=False)