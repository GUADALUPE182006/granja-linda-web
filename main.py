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
    """Usa static/logo.png si existe; si no, se muestra un ícono de casa."""
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
:root{--naranja:#d9631e;--naranja-s:#c4561a;--naranja-t:#b34e12;--crema:#fdf7ef;--arena:#fbeedb;
  --texto:#2a1a0e;--muted:#7a6a5c;--borde:#eee3d4;--blanco:#fff;
  --ambar-f:#fdf0e0;--ambar-i:#f5e0c2;--azul-f:#eaf2fb;--azul-i:#d6e6f7;--azul-t:#2d5f9a;
  --verde-f:#eef7e6;--verde-i:#dcefc9;--verde-t:#3b7a1e;--rojo-f:#fdeeee;--rojo-i:#f9d7d7;--rojo-t:#a8322d;
  --barra-v:#6ab04c;--barra-r:#e5575a}
*{box-sizing:border-box}
body{margin:0;font-family:system-ui,"Segoe UI",Arial,sans-serif;background:var(--crema);color:var(--texto);font-size:14px;
  display:grid;grid-template-columns:240px 1fr;grid-template-rows:auto 1fr;min-height:100vh}
header{grid-column:1/-1;background:var(--naranja);color:#fff;padding:12px 24px;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.marca{display:flex;align-items:center;gap:12px}
.marca .icono-casa{width:48px;height:48px;background:#fff;color:var(--naranja);border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:28px}
.marca img{width:48px;height:48px;object-fit:contain;background:#fff;border-radius:10px;padding:3px}
.marca h1{margin:0;font-size:19px;font-weight:500}
.marca small{display:block;font-size:11px;letter-spacing:2px;color:#fbe3d2;text-transform:uppercase}
.derecha{display:flex;align-items:center;gap:14px;font-size:13px;color:#fbe3d2}
.avatar{width:38px;height:38px;border-radius:50%;background:#fff;color:var(--naranja);display:flex;align-items:center;justify-content:center;font-size:20px}
nav{background:var(--arena);padding:18px 12px;display:flex;flex-direction:column;gap:6px;border-right:0.5px solid var(--ambar-i)}
nav a{display:flex;align-items:center;gap:12px;padding:11px 14px;border-radius:10px;text-decoration:none;color:var(--naranja-t);font-size:14px}
nav a i{font-size:20px}
nav a:hover{background:#fff}
nav a.activo{background:var(--naranja);color:#fff;font-weight:500}
main{min-width:0;padding:22px 26px;max-width:1180px;width:100%}
.card{background:var(--blanco);border:0.5px solid var(--borde);border-radius:14px;padding:18px;margin-bottom:16px}
.cabecera{display:flex;align-items:center;gap:12px;margin-bottom:12px}
.circ{width:40px;height:40px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:20px;flex-shrink:0}
.circ.naranja{background:var(--ambar-i);color:var(--naranja-t)}
.circ.azul{background:var(--azul-i);color:var(--azul-t)}
.circ.verde{background:var(--verde-i);color:var(--verde-t)}
.circ.rojo{background:var(--rojo-i);color:var(--rojo-t)}
.cabecera h3{margin:0;font-size:17px;font-weight:500;color:var(--naranja-t)}
.cabecera .aux{margin-left:auto;font-size:12px;color:var(--muted)}
.hero{background:var(--blanco);border:0.5px solid var(--borde);border-radius:14px;padding:22px;display:flex;align-items:center;gap:18px;margin-bottom:14px}
.hero small{color:var(--muted);font-size:13px;display:block}
.hero b{display:block;font-size:38px;font-weight:500;color:var(--texto);margin:4px 0}
.hero .pie{font-size:13px;color:var(--muted)}
.bloques{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin-bottom:16px}
.bloque{border-radius:14px;padding:16px;position:relative}
.bloque .top{display:flex;justify-content:space-between;align-items:flex-start}
.bloque small{font-size:13px;display:block;margin-top:12px}
.bloque b{display:block;font-size:24px;font-weight:500;margin:4px 0 2px;color:var(--texto)}
.bloque span{font-size:12px;color:var(--muted)}
.bloque .tend{font-size:20px;opacity:.85}
.b-ambar{background:var(--ambar-f)}.b-ambar .circ{background:var(--ambar-i);color:var(--naranja-t)}
.b-azul{background:var(--azul-f)}.b-azul .circ{background:var(--azul-i);color:var(--azul-t)}
.b-verde{background:var(--verde-f)}.b-verde .circ{background:var(--verde-i);color:var(--verde-t)}
.b-rojo{background:var(--rojo-f)}.b-rojo .circ{background:var(--rojo-i);color:var(--rojo-t)}
.b-amarillo{background:#fff8e6}.b-amarillo .circ{background:#fbe9b5;color:#8a5a00}
.fila-plato{display:grid;grid-template-columns:150px 1fr 60px;align-items:center;gap:12px;font-size:14px;padding:9px 0}
.barra{height:10px;background:#f1ece5;border-radius:6px;overflow:hidden}
.barra div{height:100%;border-radius:6px}
.nota{color:var(--muted);font-size:12px;margin:10px 0 0}
.filas div{display:flex;justify-content:space-between;gap:12px;padding:12px 0;border-bottom:0.5px solid var(--borde);font-size:13px}
.filas div:last-child{border-bottom:none}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:800px){.grid2{grid-template-columns:1fr}body{grid-template-columns:1fr}nav{flex-direction:row;flex-wrap:wrap;border-right:none}nav a{padding:8px 10px}}
form.reg{display:flex;flex-wrap:wrap;gap:10px;align-items:flex-end;margin-bottom:14px}
form.reg label{font-size:12px;color:var(--muted);display:flex;flex-direction:column;gap:4px}
input,select{padding:9px 10px;border:0.5px solid #d8cbb9;border-radius:10px;font-size:14px;background:#fff;color:var(--texto)}
button{background:var(--naranja);color:#fff;border:none;border-radius:10px;padding:9px 16px;font-size:14px;cursor:pointer}
button:hover{background:var(--naranja-s)}
button.sec{background:#fff;color:var(--naranja-t);border:0.5px solid var(--naranja);padding:5px 10px;font-size:12px}
button.sec:hover{background:var(--ambar-f)}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-size:12px;color:var(--muted);font-weight:500;padding:9px 6px;border-bottom:0.5px solid var(--borde)}
td{padding:10px 6px;border-bottom:0.5px solid var(--borde)}
.flash{background:var(--rojo-f);color:var(--rojo-t);padding:11px 14px;border-radius:10px;margin-bottom:14px}
.res{background:var(--ambar-f);color:var(--naranja-t);border-radius:10px;padding:12px 14px;margin-top:12px;font-weight:500}
</style>
</head>
<body>

{% macro ico(nombre) %}<i class="ti ti-{{ nombre }}" aria-hidden="true"></i>{% endmacro %}

<header>
  <div class="marca">
    {% if logo_img %}<img src="{{ logo_img }}" alt="Logo La Granja Linda">
    {% else %}<div class="icono-casa">{{ ico('home') }}</div>{% endif %}
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
  <div class="hero">
    <div class="circ naranja" style="width:58px;height:58px;font-size:28px">{{ ico('chart-bar') }}</div>
    <div>
      <small>Utilidad estimada del periodo</small>
      <b>{{ ind.utilidad|soles }}</b>
      <span class="pie">Calculada con las ventas, mermas y costos fijos registrados</span>
    </div>
  </div>

  <div class="bloques">
    <div class="bloque b-ambar">
      <div class="top"><div class="circ">{{ ico('coins') }}</div>{{ ico('trending-up') }}</div>
      <small>Ventas totales</small><b>{{ ind.ingresos|soles }}</b><span>Ingresos registrados</span>
    </div>
    <div class="bloque b-azul">
      <div class="top"><div class="circ">{{ ico('target') }}</div>{{ ico('trending-up') }}</div>
      <small>Punto de equilibrio</small><b>{{ ind.pe|soles if ind.pe else '—' }}</b><span>En ventas mensuales</span>
    </div>
    <div class="bloque {{ 'b-rojo' if ind.pct_mermas > 10 else 'b-verde' }}">
      <div class="top"><div class="circ">{{ ico('leaf') }}</div>{{ ico('circle-check') }}</div>
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
        <p>Holgura sobre la meta: <b style="color:{{ 'var(--verde-t)' if eq.holg >= 0 else 'var(--rojo-t)' }}">{{ '%+.1f'|format(eq.holg) }}%</b></p>
      {% endif %}
    {% endif %}
    <p class="nota">Punto de equilibrio = Costos fijos / (Precio − Costo variable unitario)</p>
  </div>
{% endif %}

{% if tab == 'reportes' %}
  <div class="card">
    <div class="cabecera"><div class="circ naranja">{{ ico('report') }}</div><h3>Resultados mensuales</h3>
      <a href="{{ url_for('exportar') }}" class="aux" style="color:var(--naranja-t);text-decoration:none">{{ ico('download') }} Exportar a CSV</a></div>
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
        logo_img=logo_archivo(),
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