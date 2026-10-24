import csv
import io
import math
import os
import re
import sqlite3
from datetime import date

from flask import (Flask, Response, abort, flash, get_flashed_messages,
                   redirect, render_template_string, request, url_for)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "cambiar-esta-clave")
DB = os.environ.get("DB_PATH", "granja_linda.db")

COMISION_APPS = 0.18
CANALES = ["Salón", "Delivery propio", "Apps de delivery", "Llevar (take away)"]
PAGOS = ["Efectivo", "Yape", "Plin", "POS (tarjeta)"]
CAUSAS = ["Mano", "Razón", "Vencimiento", "Preparación"]
ROLES = ["Administrador", "Caja", "Cocina", "Compras"]
PUESTOS = ["Cocinero(a) principal", "Ayudante de cocina", "Cajero(a)", "Delivery", "Otro"]
CONCEPTOS_CIF = ["Servicios públicos", "Arrendamiento del local", "Mantenimiento de equipos",
                 "Arbitrios", "Otro"]

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
    "usuarios": [("nombre", "Nombre", "texto", None), ("rol", "Rol", "texto", ROLES)],
}

ICONOS = {"rol": "user", "sueldo": "coin", "concepto": "file-text", "monto": "coin",
          "fecha": "calendar", "plato": "tools-kitchen-2", "cantidad": "hash",
          "canal": "truck-delivery", "pago": "credit-card", "insumo": "carrot",
          "costo": "coin", "causa": "alert-triangle", "nombre": "user"}

SECCIONES = [("dashboard", "chart-bar", "Dashboard"), ("ventas", "shopping-cart", "Ventas"),
             ("mermas", "box", "Mermas"), ("costos", "calculator", "Costos MOD/CIF"),
             ("fichas", "file-text", "Fichas técnicas"), ("equilibrio", "scale", "Punto de equilibrio"),
             ("reportes", "report", "Reportes"), ("usuarios", "users", "Usuarios"),
             ("configuracion", "palette", "Configuración")]

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


# ---------- Base de datos ----------
def conectar():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = conectar()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS platos (id INTEGER PRIMARY KEY, nombre TEXT UNIQUE, costo REAL, precio REAL);
        CREATE TABLE IF NOT EXISTS ventas (id INTEGER PRIMARY KEY, fecha TEXT, plato TEXT, cantidad INTEGER, canal TEXT, pago TEXT);
        CREATE TABLE IF NOT EXISTS mermas (id INTEGER PRIMARY KEY, fecha TEXT, insumo TEXT, cantidad REAL, costo REAL, causa TEXT);
        CREATE TABLE IF NOT EXISTS planilla_mod (id INTEGER PRIMARY KEY, rol TEXT, sueldo REAL);
        CREATE TABLE IF NOT EXISTS costos_cif (id INTEGER PRIMARY KEY, concepto TEXT, monto REAL);
        CREATE TABLE IF NOT EXISTS usuarios (id INTEGER PRIMARY KEY, nombre TEXT, rol TEXT, activo INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS configuracion (clave TEXT PRIMARY KEY, valor TEXT);
    """)
    semillas = {
        "platos": ("INSERT INTO platos(nombre,costo,precio) VALUES(?,?,?)", [
            ("1/4 Pollo", 22, 38), ("Pollo entero", 75, 115), ("Parrilla familiar", 110, 180)]),
        "planilla_mod": ("INSERT INTO planilla_mod(rol,sueldo) VALUES(?,?)", [
            ("Cocinero(a) principal", 1200), ("Ayudante de cocina", 1025),
            ("Cajero(a)", 1025), ("Delivery", 1100)]),
        "costos_cif": ("INSERT INTO costos_cif(concepto,monto) VALUES(?,?)", [
            ("Servicios públicos", 850), ("Arrendamiento del local", 1500),
            ("Mantenimiento de equipos", 400), ("Arbitrios", 180)]),
        "usuarios": ("INSERT INTO usuarios(nombre,rol) VALUES(?,?)", [
            ("J. Castro", "Administrador"), ("M. Ramírez", "Caja"), ("L. Huamán", "Cocina")]),
    }
    for tabla, (sql, filas) in semillas.items():
        if con.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0] == 0:
            con.executemany(sql, filas)
    con.commit()
    con.close()


init_db()


def obtener_colores():
    colores = dict(COLORES_DEFECTO)
    con = conectar()
    filas = con.execute("SELECT clave, valor FROM configuracion").fetchall()
    con.close()
    for f in filas:
        if f["clave"] in colores and re.fullmatch(r"#[0-9a-f]{6}", f["valor"] or ""):
            colores[f["clave"]] = f["valor"]
    return colores


def indicadores():
    con = conectar()
    ing = con.execute("""SELECT COALESCE(SUM(v.cantidad*p.precio),0) FROM ventas v
                         JOIN platos p ON p.nombre=v.plato""").fetchone()[0]
    ing_apps = con.execute("""SELECT COALESCE(SUM(v.cantidad*p.precio),0) FROM ventas v
                              JOIN platos p ON p.nombre=v.plato
                              WHERE v.canal='Apps de delivery'""").fetchone()[0]
    cvar = con.execute("""SELECT COALESCE(SUM(v.cantidad*p.costo),0) FROM ventas v
                          JOIN platos p ON p.nombre=v.plato""").fetchone()[0]
    dig = con.execute("""SELECT COALESCE(SUM(v.cantidad*p.precio),0) FROM ventas v
                         JOIN platos p ON p.nombre=v.plato
                         WHERE v.pago<>'Efectivo'""").fetchone()[0]
    mermas = con.execute("SELECT COALESCE(SUM(costo),0) FROM mermas").fetchone()[0]
    mod = con.execute("SELECT COALESCE(SUM(sueldo),0) FROM planilla_mod").fetchone()[0]
    cif = con.execute("SELECT COALESCE(SUM(monto),0) FROM costos_cif").fetchone()[0]
    con.close()

    contrib = ing - ing_apps * COMISION_APPS - cvar
    fijos = mod + cif
    return {
        "ingresos": ing,
        "utilidad": contrib - mermas - fijos,
        "pct_mermas": mermas / cvar * 100 if cvar else 0,
        "pe": fijos * ing / contrib if ing and contrib > 0 else None,
        "pct_digital": dig / ing * 100 if ing else 0,
    }


def campos_de(tabla):
    con = conectar()
    nombres = [r[0] for r in con.execute("SELECT nombre FROM platos ORDER BY id")]
    con.close()
    return [(n, e, t, nombres if n == "plato" else o) for n, e, t, o in CAMPOS[tabla]]


def valor_de(tipo, texto, etiqueta):
    texto = (texto or "").strip()
    if tipo in ("texto", "fecha"):
        if not texto:
            raise ValueError(f"'{etiqueta}' no puede estar vacío.")
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


# ---------- Plantilla ----------
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

/* Animación de fondo */
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

/* Halo que sigue el cursor */
#cursor-glow{position:fixed;left:0;top:0;width:320px;height:320px;border-radius:50%;pointer-events:none;z-index:0;
  background:radial-gradient(circle,color-mix(in srgb,var(--p) 16%,transparent),transparent 70%);opacity:0;transition:opacity .4s;will-change:transform}

header{grid-column:1/-1;background:linear-gradient(90deg,var(--p),var(--p-osc));color:#fff;padding:12px 24px;
  display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;position:relative;z-index:2}
.marca{display:flex;align-items:center;gap:14px}
.marca .casa{width:52px;height:52px;border-radius:12px;background:rgba(255,255,255,.12);display:flex;align-items:center;justify-content:center;padding:6px}
.marca .casa svg{width:100%;height:100%}
.marca img{width:52px;height:52px;object-fit:contain;background:#fff;border-radius:12px;padding:4px}
.marca h1{margin:0;font-size:21px;font-weight:500}
.marca small{display:block;font-size:11px;letter-spacing:2.5px;color:#fff;opacity:.85;text-transform:uppercase;margin-top:2px}
.derecha{display:flex;align-items:center;gap:14px;font-size:13px;color:#fff}
.avatar{width:40px;height:40px;border-radius:50%;background:#fff;color:var(--p);display:flex;align-items:center;justify-content:center;font-size:20px}
nav{position:sticky;top:0;align-self:start;height:100vh;overflow-y:auto;z-index:2;background:linear-gradient(180deg,var(--p-cl),var(--p-cl2));padding:20px 14px;display:flex;flex-direction:column;gap:6px;border-right:1px solid var(--p-cl2)}
nav a{display:flex;align-items:center;gap:12px;padding:12px 14px;border-radius:12px;text-decoration:none;color:var(--p-osc);font-size:14px;transition:background .2s,transform .15s}
nav a i{font-size:20px}
nav a:hover{background:rgba(255,255,255,.7);transform:translateX(3px)}
nav a.activo{background:var(--p);color:#fff;font-weight:500}
.decor-menu{margin-top:auto;width:100%}
.decor-menu svg{width:100%;height:auto;display:block}
main{min-width:0;padding:22px 26px;max-width:1180px;width:100%;position:relative;z-index:1}

/* Entrada suave */
main > *{animation:sube .5s ease both}
main > *:nth-child(2){animation-delay:.05s}
main > *:nth-child(3){animation-delay:.1s}
@keyframes sube{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}

/* Tarjetas */
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

/* Panel principal */
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

/* Formularios */
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

/* Botones */
.btn,.btn-sec,button{position:relative;overflow:hidden}
.btn{height:48px;padding:0 18px;border-radius:12px;border:none;color:#fff;font-size:15px;font-weight:500;cursor:pointer;font-family:inherit;display:inline-flex;align-items:center;gap:10px;background:linear-gradient(90deg,var(--p),var(--p-osc));box-shadow:0 6px 14px color-mix(in srgb,var(--p-osc) 30%,transparent);transition:transform .15s,filter .2s,box-shadow .2s}
.btn:hover{filter:brightness(1.06);box-shadow:0 10px 20px color-mix(in srgb,var(--p-osc) 38%,transparent);transform:translateY(-1px)}
.btn:active,.btn-sec:active{transform:scale(.95)}
.btn.verde{background:linear-gradient(90deg,var(--s),var(--s-osc));box-shadow:0 6px 14px color-mix(in srgb,var(--s-osc) 30%,transparent)}
.btn.verde:hover{box-shadow:0 10px 20px color-mix(in srgb,var(--s-osc) 38%,transparent)}
.btn.azul{background:linear-gradient(90deg,var(--a),var(--a-osc));box-shadow:0 6px 14px color-mix(in srgb,var(--a-osc) 30%,transparent)}
.btn.rojo{background:linear-gradient(90deg,#ef7a6b,#c0392b);box-shadow:0 6px 14px rgba(192,57,43,.25)}
.btn i{font-size:18px}
.btn .flecha{font-size:16px;opacity:.9;transition:transform .25s}
.btn:hover .flecha{transform:translateX(4px)}
.btn-sec{height:auto;padding:7px 14px;border-radius:10px;background:#fff;color:var(--p-osc);border:1px solid var(--p-cl2);font-size:13px;font-weight:500;cursor:pointer;font-family:inherit;display:inline-flex;align-items:center;gap:6px;transition:transform .15s,background .2s;text-decoration:none}
.btn-sec:hover{background:var(--p-cl)}
.btn-sec.verde{color:var(--s-osc);border-color:var(--s-cl2)}
.btn-sec.verde:hover{background:var(--s-cl)}
.btn-sec.azul{color:var(--a-osc);border-color:var(--a-cl2)}
.btn-sec.azul:hover{background:var(--a-cl)}
.btn-sec i{font-size:16px}

/* Onda al presionar */
.ripple{position:absolute;border-radius:50%;background:rgba(255,255,255,.45);transform:scale(0);animation:onda .6s ease-out forwards;pointer-events:none}
.btn-sec .ripple{background:color-mix(in srgb,var(--p) 22%,transparent)}
@keyframes onda{to{transform:scale(1);opacity:0}}

/* Tablas */
.tabla-wrap{border:1px solid var(--ln);border-radius:14px;overflow:hidden;margin-top:16px}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;font-size:13px;color:var(--tx);font-weight:500;padding:12px 16px;background:var(--p-cl);border-bottom:1px solid var(--ln)}
th.verde-h{background:var(--s-cl);color:var(--s-osc)}
th.azul-h{background:var(--a-cl);color:var(--a-osc)}
td{padding:12px 16px;border-bottom:1px solid #f4ece3;transition:background .2s}
tbody tr:hover td,table tr:hover td{background:#fffaf5}
tr:last-child td{border-bottom:none}
td.acc{text-align:right;width:130px}
.res{background:var(--p-cl);color:var(--p-osc);border-radius:12px;padding:14px 16px;margin-top:14px;font-weight:500}
.total{display:flex;align-items:center;gap:16px;border-radius:16px;padding:18px 20px;margin-top:16px;color:#fff;position:relative;overflow:hidden}
.total.naranja{background:linear-gradient(90deg,var(--p),var(--p-osc))}
.total.verde{background:linear-gradient(90deg,var(--s),var(--s-osc))}
.total .circ{width:48px;height:48px;border-radius:50%;background:rgba(255,255,255,.25);display:flex;align-items:center;justify-content:center;font-size:24px}
.total small{font-size:13px;opacity:.9;display:block}
.total b{font-size:26px;font-weight:500}
.total .tend{position:absolute;right:22px;top:50%;transform:translateY(-50%);font-size:34px;opacity:.85}

/* Mensajes */
.flash{background:var(--rj-f);color:var(--rj-t);padding:12px 16px;border-radius:12px;margin-bottom:14px}
.flash.ok{background:var(--s-cl);color:var(--s-osc)}

/* Apariencia */
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
  <form class="reg {{ color }}" method="post" action="{{ url_for('agregar', tabla=tabla) }}">
    <input type="hidden" name="tab" value="{{ tab }}">
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
          <span class="caja"><input type="text" name="{{ n }}" placeholder="Ingresa {{ e|lower }}" required></span>
        {% else %}
          <span class="caja"><input type="number" name="{{ n }}" min="0" step="{{ '1' if t == 'entero' else 'any' }}" placeholder="Ingresa {{ e|lower }}" required></span>
        {% endif %}
      </label>
    {% endfor %}
    <div class="accion">
      <button class="btn {{ color }}" type="submit"><i class="ti ti-plus" aria-hidden="true"></i>{{ boton }}<i class="ti ti-chevron-right flecha" aria-hidden="true"></i></button>
    </div>
  </form>
  {% if columnas %}
  <div class="tabla-wrap">
  <table>
    <tr>{% for c, lbl in columnas %}<th class="{{ 'verde-h' if color == 'verde' }} {{ 'azul-h' if color == 'azul' }}">{{ lbl }}</th>{% endfor %}<th class="{{ 'verde-h' if color == 'verde' }} {{ 'azul-h' if color == 'azul' }}"></th></tr>
    {% for f in filas %}
    <tr>
      {% for c, lbl in columnas %}<td>{{ f[c] }}</td>{% endfor %}
      <td class="acc">
        <form method="post" action="{{ url_for('eliminar', tabla=tabla, id_=f['id']) }}" style="display:inline">
          <input type="hidden" name="tab" value="{{ tab }}">
          <button class="btn-sec {{ color }}" type="submit"><i class="ti ti-trash" aria-hidden="true"></i>Eliminar</button>
        </form>
      </td>
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
  <div class="derecha">
    <span>{{ ico('calendar') }} Octubre 2026 · Nuevo Chimbote</span>
    <div class="avatar">{{ ico('user') }}</div>
  </div>
</header>

<nav>
  {% for id, icono, nombre in secciones %}
    <a href="{{ url_for('index', tab=id) }}" class="{{ 'activo' if tab==id }}"><i class="ti ti-{{ icono }}" aria-hidden="true"></i>{{ nombre }}</a>
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
       [('fecha','Fecha'),('plato','Plato'),('cantidad','Cantidad'),('canal','Canal'),('pago','Pago')], ventas,
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
    {{ banner('report', 'Resultados mensuales', 'Ingresos, costos y margen de cada mes registrado', 'naranja') }}
    <div style="text-align:right;margin:-8px 0 8px">
      <a href="{{ url_for('exportar') }}" class="btn-sec"><i class="ti ti-download" aria-hidden="true"></i>Exportar a CSV</a>
    </div>
    <div class="tabla-wrap">
    <table>
      <tr><th>Mes</th><th>Ingresos</th><th>Costo variable</th><th>Margen bruto</th><th>Margen %</th></tr>
      {% for r in reportes %}
      <tr><td>{{ r.mes }}</td><td>{{ r.ing|soles }}</td><td>{{ r.cv|soles }}</td><td>{{ r.margen|soles }}</td><td>{{ '%.1f'|format(r.pct) }}%</td></tr>
      {% endfor %}
    </table>
    </div>
  </div>
{% endif %}

{% if tab == 'usuarios' %}
  <div class="card">
    {{ banner('users', 'Usuarios del sistema', 'Personas con acceso y su rol en el negocio', 'naranja') }}
    <div class="tabla-wrap">
    <table>
      <tr><th>Nombre</th><th>Rol</th><th>Estado</th><th></th></tr>
      {% for u in usuarios %}
      <tr>
        <td>{{ u['nombre'] }}</td><td>{{ u['rol'] }}</td>
        <td>{{ 'Activo' if u['activo'] else 'Inactivo' }}</td>
        <td class="acc">
          <form method="post" action="{{ url_for('toggle_usuario', id_=u['id']) }}" style="display:inline">
            <button class="btn-sec" type="submit">{{ 'Desactivar' if u['activo'] else 'Activar' }}</button>
          </form>
          <form method="post" action="{{ url_for('eliminar', tabla='usuarios', id_=u['id']) }}" style="display:inline">
            <input type="hidden" name="tab" value="usuarios">
            <button class="btn-sec" type="submit"><i class="ti ti-trash" aria-hidden="true"></i>Eliminar</button>
          </form>
        </td>
      </tr>
      {% endfor %}
    </table>
    </div>
  </div>
  <div class="card">
    {{ banner('user-plus', 'Agregar usuario', 'Registra una persona con acceso al sistema', 'verde') }}
    {{ registro('usuarios', campos.usuarios, [], [], 'verde', 'Agregar usuario') }}
  </div>
{% endif %}

{% if tab == 'configuracion' %}
  <div class="card">
    {{ banner('palette', 'Apariencia', 'Elige los colores de la aplicación. Se aplican a todas las secciones y a todos los usuarios.', 'naranja') }}
    <form class="reg" method="post" action="{{ url_for('guardar_colores') }}">
      <input type="hidden" name="tab" value="configuracion">
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
      <input type="hidden" name="tab" value="configuracion">
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
{% endif %}
</main>

<script>
// Halo que sigue el cursor (solo en computadora y si no se pide movimiento reducido)
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

// Onda al presionar botones y enlaces del menú
document.addEventListener('pointerdown', function (e) {
  var b = e.target.closest('button, .btn, .btn-sec, nav a');
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

// Vista previa de colores en vivo
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


# ---------- Rutas ----------
@app.route("/")
def index():
    tab = request.args.get("tab", "dashboard")
    con = conectar()
    platos = con.execute("""SELECT nombre, costo, precio,
                            ROUND((precio-costo)*100.0/precio,1) AS margen
                            FROM platos ORDER BY margen DESC""").fetchall()
    ventas = con.execute("SELECT * FROM ventas ORDER BY id DESC").fetchall()
    mermas = con.execute("SELECT * FROM mermas ORDER BY id DESC").fetchall()
    mod = con.execute("SELECT * FROM planilla_mod ORDER BY id").fetchall()
    cif = con.execute("SELECT * FROM costos_cif ORDER BY id").fetchall()
    usuarios = con.execute("SELECT * FROM usuarios ORDER BY id").fetchall()
    rows = con.execute("""SELECT substr(v.fecha,1,7) AS mes, SUM(v.cantidad*p.precio) AS ing,
                          SUM(v.cantidad*p.costo) AS cv FROM ventas v
                          JOIN platos p ON p.nombre=v.plato GROUP BY mes ORDER BY mes""").fetchall()
    con.close()

    reportes = []
    for r in rows:
        ing, cv = r["ing"] or 0, r["cv"] or 0
        m = ing - cv
        reportes.append({"mes": r["mes"], "ing": ing, "cv": cv, "margen": m,
                         "pct": m / ing * 100 if ing else 0})

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

    colores = obtener_colores()

    return render_template_string(
        PLANTILLA, tab=tab, secciones=SECCIONES, hoy=date.today().isoformat(),
        logo_img=logo_archivo(), casa_svg=CASA_SVG, gallo_svg=GALLO_SVG,
        ola_svg=OLA_SVG, gallina_svg=GALLINA_SVG, iconos=ICONOS,
        ind=indicadores(), platos=platos, ventas=ventas, mermas=mermas,
        mermas_total=sum(m["costo"] for m in mermas), mod=mod, cif=cif,
        total_mod=total_mod, total_cif=total_cif, unid=unid, pro=pro,
        usuarios=usuarios, reportes=reportes, ficha=ficha, eq=eq,
        campos={t: campos_de(t) for t in CAMPOS},
        colores=colores, tema=tema_css(colores), presets=PRESETS,
    )


@app.route("/agregar/<tabla>", methods=["POST"])
def agregar(tabla):
    if tabla not in CAMPOS:
        abort(404)
    tab = request.form.get("tab", "dashboard")
    try:
        datos = {n: valor_de(t, request.form.get(n), e) for n, e, t, _ in CAMPOS[tabla]}
    except ValueError as err:
        flash(str(err), "error")
        return redirect(url_for("index", tab=tab))
    cols = ", ".join(datos)
    marcas = ", ".join("?" * len(datos))
    con = conectar()
    con.execute(f"INSERT INTO {tabla} ({cols}) VALUES ({marcas})", list(datos.values()))
    con.commit()
    con.close()
    return redirect(url_for("index", tab=tab))


@app.route("/eliminar/<tabla>/<int:id_>", methods=["POST"])
def eliminar(tabla, id_):
    if tabla not in CAMPOS:
        abort(404)
    con = conectar()
    con.execute(f"DELETE FROM {tabla} WHERE id=?", (id_,))
    con.commit()
    con.close()
    return redirect(url_for("index", tab=request.form.get("tab", "dashboard")))


@app.route("/usuarios/<int:id_>/toggle", methods=["POST"])
def toggle_usuario(id_):
    con = conectar()
    con.execute("UPDATE usuarios SET activo = 1 - activo WHERE id=?", (id_,))
    con.commit()
    con.close()
    return redirect(url_for("index", tab="usuarios"))


@app.route("/configuracion/guardar", methods=["POST"])
def guardar_colores():
    nuevos = {}
    for clave in COLORES_DEFECTO:
        valor = (request.form.get(clave) or "").strip().lower()
        if not re.fullmatch(r"#[0-9a-f]{6}", valor):
            flash("Los colores deben tener el formato #RRGGBB.", "error")
            return redirect(url_for("index", tab="configuracion"))
        nuevos[clave] = valor
    con = conectar()
    con.executemany("""INSERT INTO configuracion(clave, valor) VALUES(?, ?)
                       ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor""",
                    list(nuevos.items()))
    con.commit()
    con.close()
    flash("Colores guardados. Se aplican a todas las secciones.", "ok")
    return redirect(url_for("index", tab="configuracion"))


@app.route("/configuracion/restaurar", methods=["POST"])
def restaurar_colores():
    con = conectar()
    con.execute("DELETE FROM configuracion")
    con.commit()
    con.close()
    flash("Se restauraron los colores originales.", "ok")
    return redirect(url_for("index", tab="configuracion"))


@app.route("/exportar.csv")
def exportar():
    con = conectar()
    rows = con.execute("""SELECT substr(v.fecha,1,7) AS mes, SUM(v.cantidad*p.precio) AS ing,
                          SUM(v.cantidad*p.costo) AS cv FROM ventas v
                          JOIN platos p ON p.nombre=v.plato GROUP BY mes ORDER BY mes""").fetchall()
    con.close()
    salida = io.StringIO()
    w = csv.writer(salida)
    w.writerow(["Mes", "Ingresos", "Costo variable", "Margen bruto", "Margen %"])
    for r in rows:
        ing, cv = r["ing"] or 0, r["cv"] or 0
        w.writerow([r["mes"], round(ing, 2), round(cv, 2), round(ing - cv, 2),
                    round((ing - cv) / ing * 100, 1) if ing else 0])
    return Response(salida.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=reporte_mensual.csv"})


if __name__ == "__main__":
    app.run(debug=True)