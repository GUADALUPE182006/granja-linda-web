"""
Estilos visuales de La Granja Linda (paleta tomada del panel del informe).
"""
from tkinter import ttk

COLORES = {
    "naranja": "#d9631e", "naranja_osc": "#b34e12",
    "madera": "#f3dcb5", "madera_osc": "#c9a06a",
    "verde": "#1e7a46", "verde_claro": "#d6f0df",
    "rojo": "#c0392b", "rojo_claro": "#fde2e0",
    "fondo": "#f4f5f7", "blanco": "#ffffff",
    "texto": "#222222", "gris": "#6b7280", "borde": "#dddddd",
}
FUENTE = "Segoe UI"  # en Linux o Mac, Tkinter usa una fuente similar si no existe


def aplicar_estilos(root):
    """Aplica la paleta a toda la aplicación. Llamar una sola vez al iniciar."""
    c = COLORES
    style = ttk.Style(root)
    style.theme_use("clam")  # el tema 'clam' permite cambiar colores de botones en todos los sistemas
    root.configure(background=c["fondo"])

    style.configure(".", background=c["fondo"], foreground=c["texto"], font=(FUENTE, 10))
    style.configure("TFrame", background=c["fondo"])
    style.configure("TLabel", background=c["fondo"], foreground=c["texto"])

    # Tarjetas y grupos
    style.configure("TLabelframe", background=c["blanco"], bordercolor=c["borde"])
    style.configure("TLabelframe.Label", background=c["blanco"], foreground=c["naranja_osc"],
                    font=(FUENTE, 10, "bold"))

    # Botones: naranja de marca
    style.configure("TButton", background=c["naranja"], foreground=c["blanco"],
                    font=(FUENTE, 10, "bold"), padding=(10, 6), borderwidth=0)
    style.map("TButton",
              background=[("active", c["naranja_osc"]), ("pressed", c["naranja_osc"])])

    # Campos de texto
    style.configure("TEntry", fieldbackground=c["blanco"], bordercolor=c["borde"], padding=4)
    style.configure("TCombobox", fieldbackground=c["blanco"], bordercolor=c["borde"], padding=4)

    # Tablas
    style.configure("Treeview", background=c["blanco"], fieldbackground=c["blanco"],
                    foreground=c["texto"], rowheight=26, bordercolor=c["borde"])
    style.configure("Treeview.Heading", background=c["madera"], foreground=c["naranja_osc"],
                    font=(FUENTE, 10, "bold"), relief="flat")
    style.map("Treeview", background=[("selected", c["naranja"])],
              foreground=[("selected", c["blanco"])])
    style.map("Treeview.Heading", background=[("active", c["madera_osc"])])

    # Pestañas
    style.configure("TNotebook", background=c["fondo"], borderwidth=0)
    style.configure("TNotebook.Tab", background=c["madera"], foreground=c["texto"],
                    padding=(12, 6), font=(FUENTE, 10, "bold"))
    style.map("TNotebook.Tab", background=[("selected", c["naranja"])],
              foreground=[("selected", c["blanco"])])

    # Estilos especiales (se usan con style="...")
    style.configure("Titulo.TLabel", font=(FUENTE, 12, "bold"), foreground=c["naranja_osc"])
    style.configure("KPI.TLabel", font=(FUENTE, 15, "bold"), foreground=c["naranja_osc"],
                    background=c["blanco"])
    style.configure("Nota.TLabel", foreground=c["gris"], font=(FUENTE, 9))
    style.configure("Peligro.TButton", background=c["rojo"])
    style.map("Peligro.TButton", background=[("active", "#962d22")])
    return style


def configurar_tags(tree):
    """Colores de fila para márgenes: verde si es sano, rojo si es bajo."""
    tree.tag_configure("bueno", background=COLORES["verde_claro"])
    tree.tag_configure("malo", background=COLORES["rojo_claro"])