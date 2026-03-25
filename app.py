import streamlit as st
import pandas as pd
import sqlite3

# ---------------- CONFIG ----------------
st.set_page_config(page_title="Inventario Pro", layout="wide")

# ---------------- DB ----------------
conn = sqlite3.connect("inventario.db", check_same_thread=False)
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS productos (
    id TEXT PRIMARY KEY,
    nombre TEXT,
    cantidad INTEGER,
    precio REAL,
    ubicacion TEXT
)
""")
conn.commit()

# ---------------- FUNCIONES ----------------
def obtener_productos():
    return pd.read_sql("SELECT * FROM productos", conn)

def agregar_producto(id, nombre, cantidad, precio, ubicacion):
    c.execute("INSERT INTO productos VALUES (?, ?, ?, ?, ?)",
              (id, nombre, cantidad, precio, ubicacion))
    conn.commit()

def actualizar_stock(id, cantidad):
    c.execute("UPDATE productos SET cantidad = cantidad + ? WHERE id = ?",
              (cantidad, id))
    conn.commit()

def eliminar_producto(id):
    c.execute("DELETE FROM productos WHERE id = ?", (id,))
    conn.commit()

# ---------------- UI ----------------
st.title("📦 Inventario Profesional")

df = obtener_productos()

# ---------------- SIDEBAR ----------------
st.sidebar.title("⚙️ Menú")

opcion = st.sidebar.radio("Selecciona:", [
    "Dashboard",
    "Agregar producto",
    "Mover / actualizar",
    "Eliminar producto"
])

# ================= DASHBOARD =================
if opcion == "Dashboard":

    st.subheader("📊 Resumen general")

    if not df.empty:
        total_productos = len(df)
        stock_total = df["cantidad"].sum()
        valor_total = (df["cantidad"] * df["precio"]).sum()

        col1, col2, col3 = st.columns(3)

        col1.metric("Productos", total_productos)
        col2.metric("Stock total", stock_total)
        col3.metric("Valor total", f"${valor_total:,.0f}")

        st.divider()

        # -------- FILTRO POR UBICACIÓN --------
        col1, col2 = st.columns([1, 3])

        with col1:
            ubicaciones = df["ubicacion"].unique()
            ubicacion_sel = st.selectbox("📍 Ubicación", ubicaciones)

        with col2:
            df_filtrado = df[df["ubicacion"] == ubicacion_sel]
            st.dataframe(df_filtrado, use_container_width=True)

        # -------- ALERTA STOCK BAJO --------
        st.subheader("⚠️ Stock bajo")

        bajo_stock = df[df["cantidad"] < 5]

        if not bajo_stock.empty:
            st.dataframe(bajo_stock)
        else:
            st.success("Todo el stock está en niveles correctos")

    else:
        st.warning("No hay productos aún")

# ================= AGREGAR =================
elif opcion == "Agregar producto":

    st.subheader("➕ Nuevo producto")

    id = st.text_input("ID")
    nombre = st.text_input("Nombre")
    cantidad = st.number_input("Cantidad", min_value=0)
    precio = st.number_input("Precio", min_value=0.0)
    ubicacion = st.text_input("Ubicación")

    if st.button("Agregar"):
        agregar_producto(id, nombre, cantidad, precio, ubicacion)
        st.success("Producto agregado")
        st.rerun()

# ================= ACTUALIZAR =================
elif opcion == "Mover / actualizar":

    st.subheader("🔄 Actualizar producto")

    if not df.empty:

        producto_sel = st.selectbox(
            "Selecciona producto",
            df["id"]
        )

        cantidad = st.number_input("Cantidad (+/-)", value=0)

        ubicaciones = df["ubicacion"].unique()
        nueva_ubicacion = st.selectbox("Nueva ubicación", ubicaciones)

        if st.button("Actualizar"):

            actualizar_stock(producto_sel, cantidad)

            c.execute("UPDATE productos SET ubicacion = ? WHERE id = ?",
                      (nueva_ubicacion, producto_sel))
            conn.commit()

            st.success("Producto actualizado")
            st.rerun()

    else:
        st.warning("No hay productos")

# ================= ELIMINAR =================
elif opcion == "Eliminar producto":

    st.subheader("🗑️ Eliminar")

    if not df.empty:

        producto_sel = st.selectbox(
            "Selecciona producto",
            df["id"]
        )

        if st.button("Eliminar"):
            eliminar_producto(producto_sel)
            st.success("Producto eliminado")
            st.rerun()

    else:
        st.warning("No hay productos")