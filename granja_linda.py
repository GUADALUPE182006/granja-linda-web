from estilos import aplicar_estilos, configurar_tags
"""
La Granja Linda - Sistema de gestión de costos, mermas y presupuestos.
Aplicación de escritorio: Python + Tkinter + SQLite.
Ejecutar:  python granja_linda.py
"""
import csv
import math
import sqlite3
import tkinter as tk
from datetime import date
from tkinter import ttk, messagebox

DB = "granja_linda.db"
COMISION_APPS = 0.18
CANALES = ["Salón", "Delivery propio", "Apps de delivery", "Llevar (take away)"]
PAGOS = ["Efectivo", "Yape", "Plin", "POS (tarjeta)"]
CAUSAS = ["Mano", "Razón", "Vencimiento", "Preparación"]
ROLES = ["Administrador", "Caja", "Cocina", "Compras"]


# ---------- Base de datos ----------
def conectar():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def crear_base():
    con = conectar()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS platos (id INTEGER PRIMARY KEY, nombre TEXT UNIQUE, costo REAL, precio REAL);
        CREATE TABLE IF NOT EXISTS ventas (id INTEGER PRIMARY KEY, fecha TEXT, plato TEXT, cantidad INTEGER, canal TEXT, pago TEXT);
        CREATE TABLE IF NOT EXISTS mermas (id INTEGER PRIMARY KEY, fecha TEXT, insumo TEXT, cantidad REAL, costo REAL, causa TEXT);
        CREATE TABLE IF NOT EXISTS planilla_mod (id INTEGER PRIMARY KEY, rol TEXT, sueldo REAL);
        CREATE TABLE IF NOT EXISTS costos_cif (id INTEGER PRIMARY KEY, concepto TEXT, monto REAL);
        CREATE TABLE IF NOT EXISTS usuarios (id INTEGER PRIMARY KEY, nombre TEXT, rol TEXT, activo INTEGER DEFAULT 1);
    """)
    # Datos iniciales de ejemplo (solo si la tabla está vacía)
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


def S(n):
    return f"S/ {n:,.2f}"


def _num(texto, campo):
    try:
        valor = float(texto.replace(",", "."))
    except ValueError:
        raise ValueError(f"El campo '{campo}' debe ser un número.")
    if valor < 0:
        raise ValueError(f"El campo '{campo}' no puede ser negativo.")
    return valor


# ---------- Indicadores (cálculos del dashboard) ----------
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

    comisiones = ing_apps * COMISION_APPS
    fijos = mod + cif
    contrib = ing - comisiones - cvar           # margen de contribución total
    return {
        "ingresos": ing,
        "utilidad": contrib - mermas - fijos,
        "pct_mermas": mermas / cvar * 100 if cvar else 0,
        "pe": fijos * ing / contrib if ing and contrib > 0 else None,
        "pct_digital": dig / ing * 100 if ing else 0,
    }


# ---------- Widgets reutilizables ----------
def tabla(padre, columnas):
    t = ttk.Treeview(padre, columns=[c for c, _ in columnas], show="headings", height=8)
    for col, titulo in columnas:
        t.heading(col, text=titulo)
        t.column(col, width=150, anchor="w")
    t.pack(fill="both", expand=True)
    return t


class Seccion:
    """Formulario para agregar + tabla para ver/eliminar registros de una tabla SQLite."""

    def __init__(self, padre, app, tabla_sql, campos, columnas, select=None):
        self.app = app
        self.tabla = tabla_sql
        self.columnas = columnas
        self.select = select or ", ".join(c for c, _ in columnas)
        app.secciones.append(self)

        frm = ttk.LabelFrame(padre, text="Registrar", padding=8)
        frm.pack(fill="x", pady=(0, 8))
        self.entradas = []
        for i, (etiqueta, col, tipo, valor) in enumerate(campos):
            ttk.Label(frm, text=etiqueta).grid(row=0, column=i, sticky="w", padx=4)
            if isinstance(tipo, list):
                w = ttk.Combobox(frm, values=tipo, state="readonly", width=18)
                w.set(valor or tipo[0])
            else:
                w = ttk.Entry(frm, width=20)
                if valor:
                    w.insert(0, valor)
            w.grid(row=1, column=i, padx=4, pady=2)
            self.entradas.append((col, tipo, valor, w))
        ttk.Button(frm, text="Agregar", command=self.agregar).grid(row=1, column=len(campos), padx=6)

        self.tree = ttk.Treeview(padre, columns=[c for c, _ in columnas], show="headings", height=8)
        for col, titulo in columnas:
            self.tree.heading(col, text=titulo)
            self.tree.column(col, width=60 if col == "id" else 140, anchor="w")
        self.tree.pack(fill="both", expand=True)

        self.botones = ttk.Frame(padre)
        self.botones.pack(fill="x", pady=6)
        ttk.Button(self.botones, text="Eliminar seleccionado", command=self.eliminar).pack(side="left")

    def valores(self):
        datos = {}
        for col, tipo, _, w in self.entradas:
            v = w.get().strip()
            if tipo == "numero":
                v = _num(v, col)
            elif tipo == "entero":
                v = int(_num(v, col))
            elif tipo == "texto" and not v:
                raise ValueError(f"El campo '{col}' no puede estar vacío.")
            datos[col] = v
        return datos

    def agregar(self):
        try:
            datos = self.valores()
        except ValueError as e:
            messagebox.showerror("Error", str(e))
            return
        con = conectar()
        con.execute(f"INSERT INTO {self.tabla}({', '.join(datos)}) VALUES({', '.join('?' * len(datos))})",
                    list(datos.values()))
        con.commit()
        con.close()
        for _, tipo, valor, w in self.entradas:
            if not isinstance(tipo, list):
                w.delete(0, "end")
                if valor:
                    w.insert(0, valor)
        self.app.refrescar()

    def eliminar(self):
        sel = self.tree.selection()
        if not sel:
            return
        id_ = self.tree.item(sel[0])["values"][0]
        if messagebox.askyesno("Confirmar", "¿Eliminar el registro seleccionado?"):
            con = conectar()
            con.execute(f"DELETE FROM {self.tabla} WHERE id=?", (id_,))
            con.commit()
            con.close()
            self.app.refrescar()

    def cargar(self):
        con = conectar()
        filas = con.execute(f"SELECT {self.select} FROM {self.tabla} ORDER BY id DESC").fetchall()
        con.close()
        self.tree.delete(*self.tree.get_children())
        for f in filas:
            self.tree.insert("", "end", values=tuple(f))


# ---------- Aplicación principal ----------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("La Granja Linda - Gestión de costos")
        self.geometry("1100x680")
        ttk.Style(self).theme_use("clam")

        self.kpi = {}
        self.secciones = []
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=10)
        pestañas = [
            ("Dashboard", self.tab_dashboard), ("Ventas", self.tab_ventas),
            ("Mermas", self.tab_mermas), ("Costos MOD/CIF", self.tab_costos),
            ("Fichas técnicas", self.tab_fichas), ("Punto de equilibrio", self.tab_equilibrio),
            ("Reportes", self.tab_reportes), ("Usuarios", self.tab_usuarios),
        ]
        for titulo, metodo in pestañas:
            f = ttk.Frame(nb, padding=10)
            nb.add(f, text=titulo)
            metodo(f)
        self.refrescar()

    def refrescar(self):
        for s in self.secciones:
            s.cargar()
        ind = indicadores()
        self.kpi["ingresos"].set(S(ind["ingresos"]))
        self.kpi["utilidad"].set(S(ind["utilidad"]))
        self.kpi["mermas"].set(f"{ind['pct_mermas']:.1f}%")
        self.kpi["pe"].set("—" if ind["pe"] is None else f"{ind['pe']:,.0f} S/ en ventas")
        self.kpi["digital"].set(f"{ind['pct_digital']:.1f}%")
        self.cargar_platos()
        self.cargar_reportes()
        self.calcular_prorrateo()
        con = conectar()
        total = con.execute("SELECT COALESCE(SUM(costo),0) FROM mermas").fetchone()[0]
        con.close()
        self.lbl_mermas.config(text=f"Costo total de mermas registradas: {S(total)}")

    def _nombres_platos(self):
        con = conectar()
        nombres = [r[0] for r in con.execute("SELECT nombre FROM platos ORDER BY id")]
        con.close()
        return nombres

    def cargar_platos(self):
        con = conectar()
        filas = con.execute("""SELECT nombre, costo, precio,
                               ROUND((precio-costo)*100.0/precio,1) AS margen
                               FROM platos ORDER BY margen DESC""").fetchall()
        con.close()
        for t in (self.t_top, self.t_platos):
            t.delete(*t.get_children())
            for r in filas:
                t.insert("", "end", values=(r["nombre"], S(r["costo"]), S(r["precio"]), f"{r['margen']}%"))

    # --- Dashboard ---
    def tab_dashboard(self, f):
        fila = ttk.Frame(f)
        fila.pack(fill="x")
        tarjetas = [("ingresos", "Ingresos totales"), ("utilidad", "Utilidad estimada"),
                    ("mermas", "% mermas sobre insumos"), ("pe", "Punto de equilibrio"),
                    ("digital", "Pagos digitales")]
        for i, (clave, titulo) in enumerate(tarjetas):
            fila.columnconfigure(i, weight=1)
            caja = ttk.LabelFrame(fila, text=titulo, padding=10)
            caja.grid(row=0, column=i, padx=6, sticky="nsew")
            var = tk.StringVar(value="—")
            self.kpi[clave] = var
            ttk.Label(caja, textvariable=var, font=("Segoe UI", 13, "bold")).pack()
        ttk.Label(f, text="Top platos por rentabilidad", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(16, 4))
        self.t_top = tabla(f, [("plato", "Plato"), ("costo", "Costo"), ("precio", "Precio"), ("margen", "Margen")])
        ttk.Label(f, text="Los indicadores usan todas las ventas registradas y los costos fijos mensuales de la pestaña Costos.",
                  foreground="gray").pack(anchor="w", pady=8)

    # --- Ventas ---
    def tab_ventas(self, f):
        Seccion(f, self, "ventas",
                campos=[("Fecha", "fecha", "texto", date.today().isoformat()),
                        ("Plato", "plato", self._nombres_platos(), None),
                        ("Cantidad", "cantidad", "entero", None),
                        ("Canal", "canal", CANALES, None),
                        ("Pago", "pago", PAGOS, None)],
                columnas=[("id", "ID"), ("fecha", "Fecha"), ("plato", "Plato"),
                          ("cantidad", "Cantidad"), ("canal", "Canal"), ("pago", "Pago")])

    # --- Mermas ---
    def tab_mermas(self, f):
        Seccion(f, self, "mermas",
                campos=[("Fecha", "fecha", "texto", date.today().isoformat()),
                        ("Insumo", "insumo", "texto", None),
                        ("Cantidad", "cantidad", "numero", None),
                        ("Costo (S/)", "costo", "numero", None),
                        ("Causa", "causa", CAUSAS, None)],
                columnas=[("id", "ID"), ("fecha", "Fecha"), ("insumo", "Insumo"),
                          ("cantidad", "Cantidad"), ("costo", "Costo"), ("causa", "Causa")])
        self.lbl_mermas = ttk.Label(f, font=("Segoe UI", 11, "bold"))
        self.lbl_mermas.pack(anchor="w", pady=4)

    # --- Costos MOD / CIF ---
    def tab_costos(self, f):
        cont = ttk.Frame(f)
        cont.pack(fill="both", expand=True)
        izq = ttk.Frame(cont)
        izq.pack(side="left", fill="both", expand=True, padx=(0, 8))
        der = ttk.Frame(cont)
        der.pack(side="left", fill="both", expand=True, padx=(8, 0))
        ttk.Label(izq, text="Mano de Obra Directa (MOD)", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        Seccion(izq, self, "planilla_mod",
                campos=[("Puesto", "rol", "texto", None), ("Sueldo mensual (S/)", "sueldo", "numero", None)],
                columnas=[("id", "ID"), ("rol", "Puesto"), ("sueldo", "Sueldo")])
        ttk.Label(der, text="Costos Indirectos (CIF)", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        Seccion(der, self, "costos_cif",
                campos=[("Concepto", "concepto", "texto", None), ("Monto mensual (S/)", "monto", "numero", None)],
                columnas=[("id", "ID"), ("concepto", "Concepto"), ("monto", "Monto")])

        pro = ttk.LabelFrame(f, text="Prorrateo por plato", padding=10)
        pro.pack(fill="x", pady=8)
        ttk.Label(pro, text="Unidades vendidas al mes:").pack(side="left")
        self.ent_unid = ttk.Entry(pro, width=10)
        self.ent_unid.insert(0, "1800")
        self.ent_unid.pack(side="left", padx=6)
        self.ent_unid.bind("<KeyRelease>", lambda e: self.calcular_prorrateo())
        self.lbl_prorrateo = ttk.Label(pro, font=("Segoe UI", 10, "bold"))
        self.lbl_prorrateo.pack(side="left", padx=12)

    def calcular_prorrateo(self):
        con = conectar()
        total = (con.execute("SELECT COALESCE(SUM(sueldo),0) FROM planilla_mod").fetchone()[0]
                 + con.execute("SELECT COALESCE(SUM(monto),0) FROM costos_cif").fetchone()[0])
        con.close()
        try:
            u = float(self.ent_unid.get())
        except ValueError:
            u = 0
        texto = f"MOD + CIF: {S(total)}   →   {S(total / u)} por plato" if u > 0 else f"MOD + CIF: {S(total)}"
        self.lbl_prorrateo.config(text=texto)

    # --- Fichas técnicas ---
    def tab_fichas(self, f):
        calc = ttk.LabelFrame(f, text="Ficha técnica de costo unitario", padding=10)
        calc.pack(fill="x")
        self.f_ent = {}
        for i, (nombre, valor) in enumerate([("MPD (S/)", "60"), ("MOD (S/)", "8"),
                                             ("CIF (S/)", "7"), ("Precio de venta (S/)", "115")]):
            ttk.Label(calc, text=nombre).grid(row=0, column=i, sticky="w", padx=4)
            e = ttk.Entry(calc, width=16)
            e.insert(0, valor)
            e.grid(row=1, column=i, padx=4)
            self.f_ent[nombre] = e
        ttk.Button(calc, text="Calcular", command=self.calcular_ficha).grid(row=1, column=4, padx=6)
        self.lbl_ficha = ttk.Label(calc, font=("Segoe UI", 10, "bold"))
        self.lbl_ficha.grid(row=2, column=0, columnspan=5, sticky="w", pady=8)
        ttk.Label(f, text="Platos del menú", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(14, 4))
        self.t_platos = tabla(f, [("plato", "Plato"), ("costo", "Costo"), ("precio", "Precio"), ("margen", "Margen")])

    def calcular_ficha(self):
        try:
            mpd, mod, cif, precio = (float(self.f_ent[k].get()) for k in
                                     ["MPD (S/)", "MOD (S/)", "CIF (S/)", "Precio de venta (S/)"])
        except ValueError:
            messagebox.showerror("Error", "Ingrese solo números.")
            return
        costo = mpd + mod + cif
        self.lbl_ficha.config(text=f"Costo total: {S(costo)}   |   Ganancia: {S(precio - costo)}   |   "
                                   f"Margen: {(precio - costo) / precio * 100:.1f}%" if precio else "Precio inválido")

    # --- Punto de equilibrio ---
    def tab_equilibrio(self, f):
        caja = ttk.LabelFrame(f, text="Cálculo del punto de equilibrio", padding=10)
        caja.pack(fill="x")
        self.e_ent = {}
        for i, (nombre, valor) in enumerate([("Costos fijos (S/)", "56000"), ("Precio (S/)", "115"),
                                             ("Costo variable (S/)", "75"), ("Meta (unidades)", "1800")]):
            ttk.Label(caja, text=nombre).grid(row=0, column=i, sticky="w", padx=4)
            e = ttk.Entry(caja, width=16)
            e.insert(0, valor)
            e.grid(row=1, column=i, padx=4)
            self.e_ent[nombre] = e
        ttk.Button(caja, text="Calcular", command=self.calcular_pe).grid(row=1, column=4, padx=6)
        self.res_pe = ttk.Label(f, font=("Segoe UI", 13, "bold"))
        self.res_pe.pack(anchor="w", pady=(18, 6))
        self.res_mc = ttk.Label(f, font=("Segoe UI", 10))
        self.res_mc.pack(anchor="w")
        self.res_holg = ttk.Label(f, font=("Segoe UI", 10, "bold"))
        self.res_holg.pack(anchor="w", pady=4)
        ttk.Label(f, text="Punto de equilibrio = Costos fijos / (Precio − Costo variable unitario)",
                  foreground="gray").pack(anchor="w", pady=10)

    def calcular_pe(self):
        try:
            fijos, precio, var, meta = (float(self.e_ent[k].get()) for k in
                                        ["Costos fijos (S/)", "Precio (S/)", "Costo variable (S/)", "Meta (unidades)"])
        except ValueError:
            messagebox.showerror("Error", "Ingrese solo números.")
            return
        mc = precio - var
        if mc <= 0:
            self.res_pe.config(text="El precio debe ser mayor al costo variable.")
            return
        pe = math.ceil(fijos / mc)
        self.res_pe.config(text=f"{pe:,} unidades al mes  ({S(pe * precio)} en ventas)")
        self.res_mc.config(text=f"Margen de contribución: {S(mc)} por unidad")
        holg = (meta - pe) / meta * 100 if meta else 0
        self.res_holg.config(text=f"Holgura sobre la meta: {holg:+.1f}%",
                             foreground="green" if holg >= 0 else "red")

    # --- Reportes ---
    def tab_reportes(self, f):
        fila = ttk.Frame(f)
        fila.pack(fill="x", pady=(0, 8))
        ttk.Label(fila, text="Resultados mensuales según las ventas registradas",
                  font=("Segoe UI", 11, "bold")).pack(side="left")
        ttk.Button(fila, text="Exportar a CSV", command=self.exportar_csv).pack(side="right")
        self.t_rep = tabla(f, [("mes", "Mes"), ("ingresos", "Ingresos"), ("cv", "Costo variable"),
                               ("margen", "Margen bruto"), ("pct", "Margen %")])
        self.filas_rep = []

    def cargar_reportes(self):
        con = conectar()
        rows = con.execute("""SELECT substr(v.fecha,1,7) AS mes,
                              SUM(v.cantidad*p.precio) AS ing, SUM(v.cantidad*p.costo) AS cv
                              FROM ventas v JOIN platos p ON p.nombre=v.plato
                              GROUP BY mes ORDER BY mes""").fetchall()
        con.close()
        self.filas_rep = []
        self.t_rep.delete(*self.t_rep.get_children())
        for r in rows:
            margen = r["ing"] - r["cv"]
            pct = margen / r["ing"] * 100 if r["ing"] else 0
            fila = (r["mes"], round(r["ing"], 2), round(r["cv"], 2), round(margen, 2), round(pct, 1))
            self.filas_rep.append(fila)
            self.t_rep.insert("", "end", values=(fila[0], S(fila[1]), S(fila[2]), S(fila[3]), f"{fila[4]}%"))

    def exportar_csv(self):
        with open("reporte_mensual.csv", "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["Mes", "Ingresos", "Costo variable", "Margen bruto", "Margen %"])
            w.writerows(self.filas_rep)
        messagebox.showinfo("Listo", "Archivo guardado: reporte_mensual.csv")

    # --- Usuarios ---
    def tab_usuarios(self, f):
        sec = Seccion(f, self, "usuarios",
                      campos=[("Nombre", "nombre", "texto", None), ("Rol", "rol", ROLES, None)],
                      columnas=[("id", "ID"), ("nombre", "Nombre"), ("rol", "Rol"), ("activo", "Activo")],
                      select="id, nombre, rol, CASE activo WHEN 1 THEN 'Sí' ELSE 'No' END AS activo")
        ttk.Button(sec.botones, text="Activar / desactivar", command=lambda: self.toggle_usuario(sec)).pack(side="left", padx=8)

    def toggle_usuario(self, sec):
        sel = sec.tree.selection()
        if not sel:
            return
        id_ = sec.tree.item(sel[0])["values"][0]
        con = conectar()
        con.execute("UPDATE usuarios SET activo = 1 - activo WHERE id=?", (id_,))
        con.commit()
        con.close()
        self.refrescar()


if __name__ == "__main__":
    crear_base()
    App().mainloop()