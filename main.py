import csv
import io
import math
import os
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

CAMPOS = {
    "ventas": [("fecha", "Fecha", "fecha", None), ("plato", "Plato", "texto", None),
               ("cantidad", "Cantidad", "entero", None), ("canal", "Canal", "texto", CANALES),
               ("pago", "Pago", "texto", PAGOS)],
    "mermas": [("fecha", "Fecha", "fecha", None), ("insumo", "Insumo", "texto", None),
               ("cantidad", "Cantidad", "decimal", None), ("costo", "Costo (S/)", "decimal", None),
               ("causa", "Causa", "texto", CAUSAS)],
    "planilla_mod": [("rol", "Puesto", "texto", None), ("sueldo", "Sueldo mensual (S/)", "decimal", None)],
    "costos_cif": [("concepto", "Concepto", "texto", None), ("monto", "Monto mensual (S/)", "decimal", None)],
    "usuarios": [("nombre", "Nombre", "texto", None), ("rol", "Rol", "texto", ROLES)],
}

# (id, ícono Tabler, nombre)
SECCIONES = [("dashboard", "chart-bar", "Dashboard"), ("ventas", "shopping-cart", "Ventas"),
             ("mermas", "box", "Mermas"), ("costos", "calculator", "Costos MOD/CIF"),
             ("fichas", "file-text", "Fichas técnicas"), ("equilibrio", "scale", "Punto de equilibrio"),
             ("reportes", "report", "Reportes"), ("usuarios", "users", "Usuarios")]

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


@app.template_filter("soles")
def soles(n):
    return f"S/ {n:,.2f}"


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
:root{--naranja:#dd6a23;--naranja-osc:#b9531a;--arena:#fbf0e0;--crema:#fbf7f1;--texto:#2a1a0e;--muted:#7d6b5d;--borde:#f0e4d4;
  --amb-f:#fdf1e3;--amb-i:#f8e0c0;--amb-t:#b9531a;--az-f:#ebf3fc;--az-i:#d4e6f8;--az-t:#2e6aa8;
  --vd-f:#eff8e8;--vd-i:#d7eec4;--vd-t:#3f7d21;--am-f:#fdf9e6;--am-i:#f8ebb8;--am-t:#8a6200;
  --rj-f:#fdeeee;--rj-i:#f8d2d2;--rj-t:#a8322d;--barra-v:#7cc05a;--barra-r:#ee6e6e}
*{box-sizing:border-box}
body{margin:0;font-family:system-ui,"Segoe UI",Arial,sans-serif;background:var(--crema);color:var(--texto);font-size:14px;
  display:grid;grid-template-columns:240px 1fr;grid-template-rows:auto 1fr;min-height:100vh}
header{grid-column:1/-1;background:linear-gradient(90deg,#e6772b,#d25f1c);color:#fff;padding:12px 24px;
  display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.marca{display:flex;align-items:center;gap:14px}
.marca .casa{width:52px;height:52px;border-radius:12px;background:rgba(255,255,255,.12);display:flex;align-items:center;justify-content:center;padding:6px}
.marca .casa svg{width:100%;height:100%}
.marca img{width:52px;height:52px;object-fit:contain;background:#fff;border-radius:12px;padding:4px}
.marca h1{margin:0;font-size:21px;font-weight:500}
.marca small{display:block;font-size:11px;letter-spacing:2.5px;color:#fde3cc;text-transform:uppercase;margin-top:2px}
.derecha{display:flex;align-items:center;gap:14px;font-size:13px;color:#fde3cc}
.avatar{width:40px;height:40px;border-radius:50%;background:#fff;color:var(--naranja);display:flex;align-items:center;justify-content:center;font-size:20px}
nav{background:linear-gradient(180deg,#fcf1e2,#f8e4c8);padding:20px 14px;display:flex;flex-direction:column;gap:6px;border-right:1px solid #f1d7b3}
nav a{display:flex;align-items:center;gap:12px;padding:12px 14px;border-radius:12px;text-decoration:none;color:var(--naranja-osc);font-size:14px}
nav a i{font-size:20px}
nav a:hover{background:rgba(255,255,255,.7)}
nav a.activo{background:var(--naranja);color:#fff;font-weight:500}
.decor-menu{margin-top:auto;width:100%;opacity:.95}
.decor-menu svg{width:100%;height:auto;display:block}
main{min-width:0;padding:22px 26px;max-width:1180px;width:100%}
.card{background:#fff;border:1px solid var(--borde);border-radius:16px;padding:18px;margin-bottom:16px}
.cabecera{display:flex;align-items:center;gap:12px;margin-bottom:14px}
.circ{width:42px;height:42px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:20px;flex-shrink:0}
.circ.naranja{background:var(--amb-i);color:var(--naranja-osc)}
.circ.azul{background:var(--az-i);color:var(--az-t)}
.circ.verde{background:var(--vd-i);color:var(--vd-t)}
.circ.rojo{background:var(--rj-i);color:var(--rj-t)}
.cabecera h3{margin:0;font-size:18px;font-weight:500;color:var(--naranja-osc)}
.cabecera .aux{margin-left:auto;font-size:13px;color:var(--naranja-osc);text-decoration:none}
.hero{position:relative;overflow:hidden;background:#fff;border:1px solid var(--borde);border-radius:16px;padding:24px 26px;
  display:flex;align-items:center;gap:18px;margin-bottom:16px;min-height:170px}
.hero .txt{position:relative;z-index:2;display:flex;align-items:center;gap:18px}
.hero small{color:var(--muted);font-size:14px;display:block}
.hero b{display:block;font-size:40px;font-weight:500;color:var(--texto);margin:4px 0}
.hero .pie{font-size:13px;color:var(--muted)}
.hero .ola{position:absolute;left:0;right:0;bottom:0;height:110px;z-index:0}
.hero .ola svg{width:100%;height:100%}
.hero .gallo{position:absolute;right:20px;top:4px;width:230px;height:200px;z-index:1}
.hero .gallo svg{width:100%;height:100%}
.bloques{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin-bottom:16px}
.bloque{border-radius:16px;padding:18px;border:1px solid transparent}
.bloque .top{display:flex;justify-content:space-between;align-items:flex-start}
.bloque .circ{width:44px;height:44px}
.bloque .tend{font-size:20px;opacity:.9}
.bloque small{font-size:14px;display:block;margin-top:14px}
.bloque b{display:block;font-size:26px;font-weight:500;margin:4px 0 2px;color:var(--texto)}
.bloque span{font-size:12px;color:var(--muted)}
.b-ambar{background:var(--amb-f);border-color:#f7e2c6}.b-ambar .tend{color:var(--naranja)}
.b-azul{background:var(--az-f);border-color:#d6e6f7}.b-azul .tend{color:var(--az-t)}
.b-verde{background:var(--vd-f);border-color:#dcefc9}.b-verde .tend{color:var(--vd-t)}
.b-rojo{background:var(--rj-f);border-color:#f7d3d3}.b-rojo .tend{color:var(--rj-t)}
.b-amarillo{background:var(--am-f);border-color:#f5e8b5}.b-amarillo .circ{background:var(--am-i);color:var(--am-t)}
.fila-plato{display:grid;grid-template-columns:150px 1fr 60px;align-items:center;gap:14px;font-size:14px;padding:10px 0}
.barra{height:12px;background:#f3eee8;border-radius:8px;overflow:hidden}
.barra div{height:100%;border-radius:8px}
.nota{color:var(--muted);font-size:12px;margin:10px 0 0}
.filas div{display:flex;justify-content:space-between;gap:12px;padding:12px 0;border-bottom:1px solid var(--borde);font-size:13px}
.filas div:last-child{border-bottom:none}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:800px){.grid2{grid-template-columns:1fr}body{grid-template-columns:1fr}nav{flex-direction:row;flex-wrap:wrap;border-right:none}nav a{padding:8px 10px}.decor-menu{display:none}.hero .gallo{display:none}}
form.reg{display:flex;flex-wrap:wrap;gap:10px;align-items:flex-end;margin-bottom:14px}
form.reg label{font-size:12px;color:var(--muted);display:flex;flex-direction:column;gap:4px}
input,select{padding:9px 10px;border:1px solid #e3d3be;border-radius:10px;font-size:14px;background:#fff;color:var(--texto)}
button{background:var(--naranja);color:#fff;border:none;border-radius:10px;padding:9px 16px;font-size:14px;cursor:pointer}
button:hover{background:var(--naranja-osc)}
button.sec{background:#fff;color:var(--naranja-osc);border:1px solid var(--naranja);padding:5px 10px;font-size:12px}
button.sec:hover{background:var(--amb-f)}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-size:12px;color:var(--muted);font-weight:500;padding:9px 6px;border-bottom:1px solid var(--borde)}
td{padding:10px 6px;border-bottom:1px solid var(--borde)}
.flash{background:var(--rj-f);color:var(--rj-t);padding:11px 14px;border-radius:10px;margin-bottom:14px}
.res{background:var(--amb-f);color:var(--naranja-osc);border-radius:10px;padding:12px 14px;margin-top:12px;font-weight:500}
</style>
</head>
<body>

{% macro ico(nombre) %}<i class="ti ti-{{ nombre }}" aria-hidden="true"></i>{% endmacro %}

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
{% for m in get_flashed_messages() %}<div class="flash">{{ m }}</div>{% endfor %}

{% macro registro(tabla, campos, columnas, filas) %}
  <form class="reg" method="post" action="{{ url_for('agregar', tabla=tabla) }}">
    <input type="hidden" name="tab" value="{{ tab }}">
    {% for n, e, t, opciones in campos %}
      <label>{{ e }}
        {% if opciones %}
          <select name="{{ n }}">{% for o in opciones %}<option>{{ o }}</option>{% endfor %}</select>
        {% elif t == 'fecha' %}
          <input type="date" name="{{ n }}" value="{{ hoy }}" required>
        {% elif t == 'texto' %}
          <input type="text" name="{{ n }}" required>
        {% else %}
          <input type="number" name="{{ n }}" min="0" step="{{ '1' if t == 'entero' else 'any' }}" required>
        {% endif %}
      </label>
    {% endfor %}
    <button>Agregar</button>
  </form>
  <table>
    <tr>{% for c, lbl in columnas %}<th>{{ lbl }}</th>{% endfor %}<th></th></tr>
    {% for f in filas %}
    <tr>
      {% for c, lbl in columnas %}<td>{{ f[c] }}</td>{% endfor %}
      <td>
        <form method="post" action="{{ url_for('eliminar', tabla=tabla, id_=f['id']) }}">
          <input type="hidden" name="tab" value="{{ tab }}">
          <button class="sec">Eliminar</button>
        </form>
      </td>
    </tr>
    {% endfor %}
  </table>
{% endmacro %}

{% if tab == 'dashboard' %}
  <section class="hero">
    <div class="txt">
      <div class="circ naranja" style="width:60px;height:60px;font-size:28px">{{ ico('chart-bar') }}</div>
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
      <div class="top"><div class="circ naranja">{{ ico('coins') }}</div><span class="tend">{{ ico('trending-up') }}</span></div>
      <small>Ventas totales</small><b>{{ ind.ingresos|soles }}</b><span>Ingresos registrados</span>
    </div>
    <div class="bloque b-azul">
      <div class="top"><div class="circ azul">{{ ico('target') }}</div><span class="tend">{{ ico('trending-up') }}</span></div>
      <small>Punto de equilibrio</small><b>{{ ind.pe|soles if ind.pe else '—' }}</b><span>En ventas mensuales</span>
    </div>
    <div class="bloque {{ 'b-rojo' if ind.pct_mermas > 10 else 'b-verde' }}">
      <div class="top"><div class="circ verde">{{ ico('leaf') }}</div><span class="tend">{{ ico('circle-check') }}</span></div>
      <small>Mermas sobre insumos</small><b>{{ '%.1f'|format(ind.pct_mermas) }}%</b><span>Objetivo menor a 10%</span>
    </div>
    <div class="bloque b-amarillo">
      <div class="top"><div class="circ">{{ ico('wallet') }}</div></div>
      <small>Pagos digitales</small><b>{{ '%.1f'|format(ind.pct_digital) }}%</b><span>Yape, Plin y POS</span>
    </div>
  </div>

  <div class="card">
    <div class="cabecera"><div class="circ naranja">{{ ico('chart-bar') }}</div><h3>Platos por margen</h3></div>
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
    <div class="cabecera"><div class="circ naranja">{{ ico('file-text') }}</div><h3>Detalle por plato</h3></div>
    {% for p in platos %}
    <div><span>{{ p['nombre'] }}</span><span>Costo {{ p['costo']|soles }} · Precio {{ p['precio']|soles }} · Ganancia {{ (p['precio']-p['costo'])|soles }}</span></div>
    {% endfor %}
  </div>
{% endif %}

{% if tab == 'ventas' %}
  <div class="card">
    <div class="cabecera"><div class="circ naranja">{{ ico('shopping-cart') }}</div><h3>Registrar venta</h3></div>
    {{ registro('ventas', campos.ventas,
       [('fecha','Fecha'),('plato','Plato'),('cantidad','Cantidad'),('canal','Canal'),('pago','Pago')], ventas) }}
  </div>
{% endif %}

{% if tab == 'mermas' %}
  <div class="card">
    <div class="cabecera"><div class="circ rojo">{{ ico('box') }}</div><h3>Registrar merma</h3></div>
    {{ registro('mermas', campos.mermas,
       [('fecha','Fecha'),('insumo','Insumo'),('cantidad','Cantidad'),('costo','Costo'),('causa','Causa')], mermas) }}
    <div class="res">Costo total de mermas registradas: {{ mermas_total|soles }}</div>
  </div>
{% endif %}

{% if tab == 'costos' %}
  <div class="grid2">
    <div class="card">
      <div class="cabecera"><div class="circ naranja">{{ ico('users') }}</div><h3>Mano de Obra Directa (MOD)</h3></div>
      {{ registro('planilla_mod', campos.planilla_mod, [('rol','Puesto'),('sueldo','Sueldo')], mod) }}
      <div class="res">Total MOD: {{ total_mod|soles }}</div>
    </div>
    <div class="card">
      <div class="cabecera"><div class="circ naranja">{{ ico('building-store') }}</div><h3>Costos Indirectos (CIF)</h3></div>
      {{ registro('costos_cif', campos.costos_cif, [('concepto','Concepto'),('monto','Monto')], cif) }}
      <div class="res">Total CIF: {{ total_cif|soles }}</div>
    </div>
  </div>
  <div class="card">
    <div class="cabecera"><div class="circ naranja">{{ ico('calculator') }}</div><h3>Prorrateo por plato</h3></div>
    <form class="reg" method="get">
      <input type="hidden" name="tab" value="costos">
      <label>Unidades vendidas al mes<input type="number" name="unid" value="{{ unid|int }}" min="1"></label>
      <button>Calcular</button>
    </form>
    <div class="res">MOD + CIF: {{ (total_mod+total_cif)|soles }}
      {% if pro %} → {{ pro|soles }} por plato{% endif %}</div>
  </div>
{% endif %}

{% if tab == 'fichas' %}
  <div class="card">
    <div class="cabecera"><div class="circ naranja">{{ ico('file-text') }}</div><h3>Ficha técnica de costo unitario</h3></div>
    <form class="reg" method="get">
      <input type="hidden" name="tab" value="fichas">
      <label>MPD (S/)<input type="number" step="any" name="f_mpd" value="60"></label>
      <label>MOD (S/)<input type="number" step="any" name="f_mod" value="8"></label>
      <label>CIF (S/)<input type="number" step="any" name="f_cif" value="7"></label>
      <label>Precio de venta (S/)<input type="number" step="any" name="f_precio" value="115"></label>
      <button>Calcular</button>
    </form>
    {% if ficha %}
    <div class="res">Costo total: {{ ficha.costo|soles }} · Ganancia: {{ ficha.ganancia|soles }} · Margen: {{ '%.1f'|format(ficha.margen) }}%</div>
    {% endif %}
  </div>
  <div class="card">
    <div class="cabecera"><div class="circ naranja">{{ ico('tools-kitchen-2') }}</div><h3>Platos del menú</h3></div>
    <table>
      <tr><th>Plato</th><th>Costo</th><th>Precio</th><th>Margen</th></tr>
      {% for p in platos %}
      <tr><td>{{ p['nombre'] }}</td><td>{{ p['costo']|soles }}</td><td>{{ p['precio']|soles }}</td><td>{{ p['margen'] }}%</td></tr>
      {% endfor %}
    </table>
  </div>
{% endif %}

{% if tab == 'equilibrio' %}
  <div class="card">
    <div class="cabecera"><div class="circ azul">{{ ico('scale') }}</div><h3>Cálculo del punto de equilibrio</h3></div>
    <form class="reg" method="get">
      <input type="hidden" name="tab" value="equilibrio">
      <label>Costos fijos (S/)<input type="number" step="any" name="e_fijos" value="56000"></label>
      <label>Precio (S/)<input type="number" step="any" name="e_precio" value="115"></label>
      <label>Costo variable (S/)<input type="number" step="any" name="e_var" value="75"></label>
      <label>Meta (unidades)<input type="number" name="e_meta" value="1800"></label>
      <button>Calcular</button>
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
    <div class="cabecera"><div class="circ naranja">{{ ico('report') }}</div><h3>Resultados mensuales</h3>
      <a href="{{ url_for('exportar') }}" class="aux">{{ ico('download') }} Exportar a CSV</a></div>
    <table>
      <tr><th>Mes</th><th>Ingresos</th><th>Costo variable</th><th>Margen bruto</th><th>Margen %</th></tr>
      {% for r in reportes %}
      <tr><td>{{ r.mes }}</td><td>{{ r.ing|soles }}</td><td>{{ r.cv|soles }}</td><td>{{ r.margen|soles }}</td><td>{{ '%.1f'|format(r.pct) }}%</td></tr>
      {% endfor %}
    </table>
  </div>
{% endif %}

{% if tab == 'usuarios' %}
  <div class="card">
    <div class="cabecera"><div class="circ naranja">{{ ico('users') }}</div><h3>Usuarios del sistema</h3></div>
    <table>
      <tr><th>Nombre</th><th>Rol</th><th>Estado</th><th></th></tr>
      {% for u in usuarios %}
      <tr>
        <td>{{ u['nombre'] }}</td><td>{{ u['rol'] }}</td>
        <td>{{ 'Activo' if u['activo'] else 'Inactivo' }}</td>
        <td>
          <form method="post" action="{{ url_for('toggle_usuario', id_=u['id']) }}" style="display:inline">
            <button class="sec">{{ 'Desactivar' if u['activo'] else 'Activar' }}</button>
          </form>
          <form method="post" action="{{ url_for('eliminar', tabla='usuarios', id_=u['id']) }}" style="display:inline">
            <input type="hidden" name="tab" value="usuarios">
            <button class="sec">Eliminar</button>
          </form>
        </td>
      </tr>
      {% endfor %}
    </table>
  </div>
  <div class="card">
    <div class="cabecera"><div class="circ naranja">{{ ico('user-plus') }}</div><h3>Agregar usuario</h3></div>
    {{ registro('usuarios', campos.usuarios, [], []) }}
  </div>
{% endif %}
</main>
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

    return render_template_string(
        PLANTILLA, tab=tab, secciones=SECCIONES, hoy=date.today().isoformat(),
        logo_img=logo_archivo(), casa_svg=CASA_SVG, gallo_svg=GALLO_SVG,
        ola_svg=OLA_SVG, gallina_svg=GALLINA_SVG,
        ind=indicadores(), platos=platos, ventas=ventas, mermas=mermas,
        mermas_total=sum(m["costo"] for m in mermas), mod=mod, cif=cif,
        total_mod=total_mod, total_cif=total_cif, unid=unid, pro=pro,
        usuarios=usuarios, reportes=reportes, ficha=ficha, eq=eq,
        campos={t: campos_de(t) for t in CAMPOS},
    )


@app.route("/agregar/<tabla>", methods=["POST"])
def agregar(tabla):
    if tabla not in CAMPOS:
        abort(404)
    tab = request.form.get("tab", "dashboard")
    try:
        datos = {n: valor_de(t, request.form.get(n), e) for n, e, t, _ in CAMPOS[tabla]}
    except ValueError as err:
        flash(str(err))
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