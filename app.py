import csv
import io
import math
import os
import sqlite3
from datetime import date

from flask import (Flask, Response, abort, flash, get_flashed_messages,
                   redirect, render_template, request, url_for)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "cambiar-esta-clave")
DB = os.environ.get("DB_PATH", "granja_linda.db")

COMISION_APPS = 0.18
CANALES = ["Salón", "Delivery propio", "Apps de delivery", "Llevar (take away)"]
PAGOS = ["Efectivo", "Yape", "Plin", "POS (tarjeta)"]
CAUSAS = ["Mano", "Razón", "Vencimiento", "Preparación"]
ROLES = ["Administrador", "Caja", "Cocina", "Compras"]

# (columna, etiqueta, tipo, opciones)
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


init_db()  # se crea la base al iniciar, también con gunicorn en Render


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
    """Devuelve los campos de una tabla, con los nombres de platos en el selector de ventas."""
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


# ---------- Vistas ----------
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

    # Prorrateo
    total_mod = sum(r["sueldo"] for r in mod)
    total_cif = sum(r["monto"] for r in cif)
    unid = num_arg("unid", "1800")
    pro = (total_mod + total_cif) / unid if unid else None

    # Ficha técnica
    ficha = None
    vals = [num_arg(k, d) for k, d in [("f_mpd", "60"), ("f_mod", "8"), ("f_cif", "7"), ("f_precio", "115")]]
    if None not in vals:
        mpd, mo, cf, precio = vals
        costo = mpd + mo + cf
        ficha = {"costo": costo, "ganancia": precio - costo,
                 "margen": (precio - costo) / precio * 100 if precio else 0}

    # Punto de equilibrio
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

    return render_template(
        "index.html", tab=tab, hoy=date.today().isoformat(),
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