import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
import os
from io import BytesIO, StringIO
# import reportlab components - temporarily disabled for deployment
# from reportlab.lib.pagesizes import letter, A4
# from reportlab.lib import colors
# from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
# from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
# from reportlab.lib.units import inch
import hashlib

# ==================== CONFIG ====================
st.set_page_config(
    page_title="Inventario Pro",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={"About": "Sistema de Inventario Profesional v2.0"}
)

# ==================== DATABASE ====================
DB_PATH = "inventario.db"

@st.cache_resource
def get_db_connection():
    """Obtiene conexión a la BD con mejoras de performance"""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn

conn = get_db_connection()
c = conn.cursor()

# Crear tablas con índices
c.execute("""
CREATE TABLE IF NOT EXISTS productos (
    id TEXT PRIMARY KEY,
    nombre TEXT NOT NULL,
    cantidad INTEGER NOT NULL DEFAULT 0,
    precio REAL NOT NULL,
    ubicacion TEXT NOT NULL,
    creado_en TEXT NOT NULL,
    actualizado_en TEXT NOT NULL
)
""")

c.execute("CREATE INDEX IF NOT EXISTS idx_productos_ubicacion ON productos(ubicacion)")
c.execute("CREATE INDEX IF NOT EXISTS idx_productos_nombre ON productos(nombre)")

c.execute("""
CREATE TABLE IF NOT EXISTS movimientos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_id TEXT NOT NULL,
    tipo TEXT NOT NULL,
    cantidad INTEGER NOT NULL,
    fecha TEXT NOT NULL,
    descripcion TEXT,
    usuario TEXT,
    FOREIGN KEY(producto_id) REFERENCES productos(id) ON DELETE CASCADE
)
""")

c.execute("CREATE INDEX IF NOT EXISTS idx_movimientos_producto ON movimientos(producto_id)")
c.execute("CREATE INDEX IF NOT EXISTS idx_movimientos_fecha ON movimientos(fecha)")
c.execute("CREATE INDEX IF NOT EXISTS idx_movimientos_tipo ON movimientos(tipo)")

# Crear tabla de configuración con valores por defecto
c.execute("""
CREATE TABLE IF NOT EXISTS configuracion (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
)
""")

# Insertar configuraciones por defecto si no existen
defaults = {
    "stock_minimo": "5",
    "tema": "light",
    "moneda": "USD"
}

for k, v in defaults.items():
    c.execute("INSERT OR IGNORE INTO configuracion (clave, valor) VALUES (?, ?)", (k, v))

conn.commit()

# ==================== DATABASE MIGRATIONS ====================
def migrar_base_datos():
    """Migra la base de datos a la versión más reciente"""
    try:
        # Verificar y agregar columnas faltantes en productos
        c.execute("PRAGMA table_info(productos)")
        columns = [row[1] for row in c.fetchall()]
        
        if "creado_en" not in columns:
            c.execute("ALTER TABLE productos ADD COLUMN creado_en TEXT")
            st.info("✅ Migración: Agregada columna 'creado_en' a tabla productos")
        
        if "actualizado_en" not in columns:
            c.execute("ALTER TABLE productos ADD COLUMN actualizado_en TEXT")
            st.info("✅ Migración: Agregada columna 'actualizado_en' a tabla productos")
        
        # Verificar y agregar columnas faltantes en movimientos
        c.execute("PRAGMA table_info(movimientos)")
        columns = [row[1] for row in c.fetchall()]
        
        if "descripcion" not in columns:
            c.execute("ALTER TABLE movimientos ADD COLUMN descripcion TEXT")
            st.info("✅ Migración: Agregada columna 'descripcion' a tabla movimientos")
        
        if "usuario" not in columns:
            c.execute("ALTER TABLE movimientos ADD COLUMN usuario TEXT")
            st.info("✅ Migración: Agregada columna 'usuario' a tabla movimientos")
        
        conn.commit()
        
    except Exception as e:
        st.error(f"❌ Error en migración: {str(e)}")

# Ejecutar migraciones
migrar_base_datos()

# ==================== POBLAR DATOS FALTANTES ====================
def poblar_datos_faltantes():
    """Pobla datos faltantes en columnas existentes"""
    try:
        ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Actualizar productos sin fecha de creación
        c.execute(
            "UPDATE productos SET creado_en = ? WHERE creado_en IS NULL OR creado_en = ''",
            (ahora,)
        )
        
        # Actualizar productos sin fecha de actualización
        c.execute(
            "UPDATE productos SET actualizado_en = ? WHERE actualizado_en IS NULL OR actualizado_en = ''",
            (ahora,)
        )
        
        conn.commit()
        
        # Verificar si se actualizaron registros
        c.execute("SELECT COUNT(*) FROM productos WHERE creado_en = ?", (ahora,))
        actualizados = c.fetchone()[0]
        if actualizados > 0:
            st.info(f"✅ Migración: Actualizados {actualizados} productos con fechas faltantes")
            
    except Exception as e:
        st.warning(f"⚠️ Error al poblar datos faltantes: {str(e)}")

# Poblar datos faltantes
poblar_datos_faltantes()

# ==================== CACHED FUNCTIONS ====================

@st.cache_data(ttl=5)
def obtener_productos():
    """Obtiene productos con caché de 5 segundos"""
    return pd.read_sql("SELECT * FROM productos ORDER BY nombre", conn)

@st.cache_data(ttl=5)
def obtener_movimientos():
    """Obtiene movimientos con caché de 5 segundos"""
    return pd.read_sql(
        "SELECT * FROM movimientos ORDER BY fecha DESC",
        conn
    )

def obtener_producto_por_id(producto_id):
    """Obtiene detalles de un producto específico"""
    return pd.read_sql(
        "SELECT * FROM productos WHERE id = ?",
        conn,
        params=(producto_id,)
    )

@st.cache_data(ttl=5)
def get_estadisticas():
    """Calcula estadísticas del inventario"""
    df = obtener_productos()
    mov = obtener_movimientos()
    
    if df.empty:
        return {}
    
    stock_minimo = int(obtener_config("stock_minimo") or 5)
    
    df["valor"] = df["cantidad"] * df["precio"]
    
    stats = {
        "valor_total": df["valor"].sum(),
        "stock_total": df["cantidad"].sum(),
        "num_productos": len(df),
        "productos_bajo_stock": len(df[df["cantidad"] < stock_minimo]),
        "ubicaciones": df["ubicacion"].nunique(),
        "valor_promedio": df["precio"].mean()
    }
    
    if not mov.empty:
        stats["salidas_totales"] = mov[mov["tipo"] == "salida"]["cantidad"].sum()
        stats["entradas_totales"] = mov[mov["tipo"] == "entrada"]["cantidad"].sum()
    
    return stats

# ==================== FUNCIONES DE BD ====================

def agregar_producto(id, nombre, cantidad, precio, ubicacion):
    """Agrega un nuevo producto con validaciones"""
    try:
        ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute(
            """INSERT INTO productos 
               (id, nombre, cantidad, precio, ubicacion, creado_en, actualizado_en)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (id, nombre.strip(), int(cantidad), float(precio), ubicacion.strip(), ahora, ahora)
        )
        
        c.execute(
            """INSERT INTO movimientos 
               (producto_id, tipo, cantidad, fecha, descripcion)
               VALUES (?, ?, ?, ?, ?)""",
            (id, "entrada", int(cantidad), ahora, "Entrada inicial")
        )
        
        conn.commit()
        st.cache_data.clear()
        return True, "✅ Producto agregado exitosamente"
    
    except sqlite3.IntegrityError:
        return False, f"❌ El producto con ID '{id}' ya existe"
    except ValueError as e:
        return False, f"❌ Error en los valores ingresados: {str(e)}"
    except Exception as e:
        return False, f"❌ Error inesperado: {str(e)}"

def actualizar_stock(producto_id, cantidad, descripcion=""):
    """Actualiza el stock con validación de cantidad negativa"""
    try:
        df_producto = obtener_producto_por_id(producto_id)
        if df_producto.empty or len(df_producto) == 0:
            return False, "❌ Producto no encontrado"
        
        stock_actual = df_producto["cantidad"].iloc[0]
        nuevo_stock = stock_actual + cantidad
        
        if nuevo_stock < 0:
            return False, f"❌ Stock insuficiente. Stock actual: {stock_actual}, intenta restar {abs(cantidad)}"
        
        ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        c.execute(
            "UPDATE productos SET cantidad = ?, actualizado_en = ? WHERE id = ?",
            (nuevo_stock, ahora, producto_id)
        )
        
        tipo = "entrada" if cantidad > 0 else "salida"
        c.execute(
            """INSERT INTO movimientos 
               (producto_id, tipo, cantidad, fecha, descripcion)
               VALUES (?, ?, ?, ?, ?)""",
            (producto_id, tipo, abs(cantidad), ahora, descripcion)
        )
        
        conn.commit()
        st.cache_data.clear()
        return True, f"✅ Stock actualizado. Nuevo stock: {nuevo_stock}"
    
    except Exception as e:
        return False, f"❌ Error al actualizar stock: {str(e)}"

def actualizar_precio(producto_id, nuevo_precio):
    """Actualiza el precio de un producto"""
    try:
        ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute(
            "UPDATE productos SET precio = ?, actualizado_en = ? WHERE id = ?",
            (float(nuevo_precio), ahora, producto_id)
        )
        conn.commit()
        st.cache_data.clear()
        return True, f"✅ Precio actualizado a ${float(nuevo_precio):.2f}"
    except Exception as e:
        return False, f"❌ Error al actualizar precio: {str(e)}"

def actualizar_nombre(producto_id, nuevo_nombre):
    """Actualiza el nombre de un producto"""
    try:
        ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute(
            "UPDATE productos SET nombre = ?, actualizado_en = ? WHERE id = ?",
            (nuevo_nombre.strip(), ahora, producto_id)
        )
        conn.commit()
        st.cache_data.clear()
        return True, f"✅ Nombre actualizado a '{nuevo_nombre.strip()}'"
    except Exception as e:
        return False, f"❌ Error al actualizar nombre: {str(e)}"

def actualizar_ubicacion(producto_id, nueva_ubicacion):
    """Actualiza la ubicación de un producto"""
    try:
        ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute(
            "UPDATE productos SET ubicacion = ?, actualizado_en = ? WHERE id = ?",
            (nueva_ubicacion.strip(), ahora, producto_id)
        )
        conn.commit()
        st.cache_data.clear()
        return True, f"✅ Ubicación actualizada a '{nueva_ubicacion.strip()}'"
    except Exception as e:
        return False, f"❌ Error al actualizar ubicación: {str(e)}"

def obtener_movimientos_producto(producto_id):
    """Obtiene historial de movimientos de un producto"""
    return pd.read_sql(
        "SELECT * FROM movimientos WHERE producto_id = ? ORDER BY fecha DESC",
        conn,
        params=(producto_id,)
    )

def exportar_datos_csv():
    """Exporta datos a CSV"""
    df = obtener_productos()
    return df.to_csv(index=False).encode('utf-8')

# ==================== AUTENTICACIÓN ====================
def hash_password(password):
    """Hashea la contraseña"""
    return hashlib.sha256(password.encode()).hexdigest()

def verificar_login(usuario, password):
    """Verifica las credenciales - Credenciales por defecto: admin/admin123"""
    usuarios = {
        "admin": hash_password("admin123"),
        "almacen": hash_password("almacen123")
    }
    if usuario in usuarios and hash_password(password) == usuarios[usuario]:
        return True
    return False

# ==================== IMPORTACIÓN ====================
def importar_productos_csv(file):
    """Importa productos desde CSV"""
    try:
        df = pd.read_csv(file)
        requeridos = {"id", "nombre", "cantidad", "precio", "ubicacion"}
        if not requeridos.issubset(set(df.columns)):
            return False, f"❌ Columnas requeridas: {', '.join(requeridos)}"
        
        ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        insertados = 0
        duplicados = 0
        
        for _, row in df.iterrows():
            try:
                c.execute(
                    """INSERT INTO productos 
                       (id, nombre, cantidad, precio, ubicacion, creado_en, actualizado_en)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (str(row['id']).strip(), str(row['nombre']).strip(), int(row['cantidad']), 
                     float(row['precio']), str(row['ubicacion']).strip(), ahora, ahora)
                )
                insertados += 1
            except sqlite3.IntegrityError:
                duplicados += 1
        
        conn.commit()
        st.cache_data.clear()
        return True, f"✅ Importados {insertados} productos ({duplicados} duplicados)"
    except Exception as e:
        return False, f"❌ Error al importar: {str(e)}"

# ==================== ANÁLISIS ABC ====================
def calcular_analisis_abc():
    """Calcula el análisis ABC (Pareto) de productos"""
    df = obtener_productos()
    if df.empty:
        return pd.DataFrame()
    
    df_abc = df.copy()
    df_abc["valor"] = df_abc["cantidad"] * df_abc["precio"]
    df_abc = df_abc.sort_values("valor", ascending=False)
    df_abc["valor_acumulado"] = df_abc["valor"].cumsum()
    df_abc["valor_total"] = df_abc["valor"].sum()
    df_abc["porcentaje_acumulado"] = (df_abc["valor_acumulado"] / df_abc["valor_total"] * 100).round(2)
    
    # Clasificar A, B, C
    def clasificar(porcentaje):
        if porcentaje <= 80:
            return "A"
        elif porcentaje <= 95:
            return "B"
        else:
            return "C"
    
    df_abc["clase"] = df_abc["porcentaje_acumulado"].apply(clasificar)
    return df_abc[["id", "nombre", "cantidad", "valor", "porcentaje_acumulado", "clase"]]

# ==================== REPORTES PDF ====================
def generar_pdf_inventario():
    """Genera reporte de inventario en formato TXT (temporal)"""
    df = obtener_productos()
    stats = get_estadisticas()

    # Crear contenido del reporte
    reporte = f"""
REPORTE DE INVENTARIO - {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}
{'='*60}

RESUMEN EJECUTIVO:
- Valor Total Inventario: ${stats.get('valor_total', 0):,.2f}
- Stock Total: {stats.get('stock_total', 0):,} unidades
- Número de Productos: {stats.get('num_productos', 0)}
- Productos Bajo Stock: {stats.get('productos_bajo_stock', 0)}
- Ubicaciones: {stats.get('ubicaciones', 0)}

LISTADO DE PRODUCTOS:
{'-'*60}
"""

    if not df.empty:
        for _, row in df.iterrows():
            reporte += f"""
ID: {row['id']}
Nombre: {row['nombre']}
Cantidad: {int(row['cantidad'])} unid.
Precio: ${row['precio']:.2f}
Ubicación: {row['ubicacion']}
Valor Total: ${(row['cantidad'] * row['precio']):,.2f}
{'-'*30}
"""

    # Convertir a bytes
    return reporte.encode('utf-8')

# ==================== CONFIGURACIÓN ====================
def obtener_config(clave):
    """Obtiene valor de configuración"""
    resultado = c.execute("SELECT valor FROM configuracion WHERE clave = ?", (clave,)).fetchone()
    return resultado[0] if resultado else None

def actualizar_config(clave, valor):
    """Actualiza configuración"""
    c.execute("UPDATE configuracion SET valor = ? WHERE clave = ?", (valor, clave))
    conn.commit()
    st.cache_data.clear()

# ==================== INICIALIZAR SESSION STATE ====================
if "confirmacion_eliminar" not in st.session_state:
    st.session_state.confirmacion_eliminar = {}

if "autenticado" not in st.session_state:
    st.session_state.autenticado = False

if "usuario" not in st.session_state:
    st.session_state.usuario = None

# ==================== TEMA ====================
tema = obtener_config("tema")
if tema == "dark":
    st.markdown("""
    <style>
        body { background-color: #0e1117; color: #c9d1d9; }
    </style>
    """, unsafe_allow_html=True)

# ==================== ESTILOS ====================
st.markdown("""
<style>
    .metric-card {
        background-color: #f0f2f6;
        padding: 20px;
        border-radius: 10px;
        text-align: center;
    }
    .alert-critical {
        background-color: #ffcccc;
        padding: 10px;
        border-radius: 5px;
        border-left: 4px solid #ff0000;
    }
</style>
""", unsafe_allow_html=True)

# ==================== HEADER & AUTENTICACIÓN ====================
col1, col2, col3 = st.columns([0.7, 0.15, 0.15])

with col1:
    st.title("📦 Inventario Profesional")

with col2:
    if st.session_state.autenticado:
        st.info(f"👤 {st.session_state.usuario}")

with col3:
    if st.session_state.autenticado:
        if st.button("🚪 Salir", use_container_width=True):
            st.session_state.autenticado = False
            st.session_state.usuario = None
            st.rerun()

# Login
if not st.session_state.autenticado:
    st.divider()
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        st.subheader("🔐 Iniciar Sesión")
        usuario = st.text_input("Usuario", placeholder="admin")
        password = st.text_input("Contraseña", type="password", placeholder="••••••••")
        
        col_login, col_demo = st.columns(2)
        
        with col_login:
            if st.button("Acceder", use_container_width=True, type="primary"):
                if verificar_login(usuario, password):
                    st.session_state.autenticado = True
                    st.session_state.usuario = usuario
                    st.success(f"✅ Bienvenido {usuario}")
                    st.rerun()
                else:
                    st.error("❌ Usuario o contraseña incorrectos")
        
        with col_demo:
            st.info("Demo: admin/admin123")
    
    st.stop()

# Contenedor principal para mantener consistencia del DOM
main_container = st.container()

with main_container:
    st.divider()

    # ==================== SIDEBAR ====================
    with st.sidebar:
        st.title("⚙️ Navegación")
        
        pagina = st.radio(
            "Selecciona sección:",
            [
                "📊 Dashboard",
            "➕ Agregar producto",
            "🔄 Movimientos",
            "📜 Kardex",
            "🔍 Búsqueda",
            "⚙️ Gestión",
            "� Análisis ABC",
            "📥 Descargas",
            "⚙️ Configuración"
        ]
    )
    
    st.divider()
    
    # Filtro global
    st.subheader("🔎 Filtadores")
    filtro_ubicacion = st.multiselect(
        "Ubicaciones:",
        ["Todas"] + list(obtener_productos()["ubicacion"].unique() if not obtener_productos().empty else []),
        default=["Todas"]
    )

# ==================== DASHBOARD ====================
if pagina == "📊 Dashboard":
    st.subheader("📊 Dashboard en tiempo real")
    
    df = obtener_productos()
    mov = obtener_movimientos()
    stats = get_estadisticas()
    
    if not df.empty:
        # KPIs
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("💰 Valor Inventario", f"${stats.get('valor_total', 0):,.0f}")
        with col2:
            st.metric("📦 Stock Total", f"{stats.get('stock_total', 0):,} unidades")
        with col3:
            st.metric("🏢 Ubicaciones", stats.get('ubicaciones', 0))
        with col4:
            color = "🔴" if stats.get('productos_bajo_stock', 0) > 0 else "🟢"
            st.metric(f"{color} Bajo Stock", stats.get('productos_bajo_stock', 0))
        
        st.divider()
        
        # Tabs de análisis
        tab1, tab2, tab3 = st.tabs(["📊 Análisis", "⚠️ Alertas", "📈 Gráficos"])
        
        with tab1:
            col1, col2 = st.columns(2)
            
            with col1:
                st.info(f"**Entradas totales:** {stats.get('entradas_totales', 0):,} unidades")
                st.info(f"**Salidas totales:** {stats.get('salidas_totales', 0):,} unidades")
                st.info(f"**Precio promedio:** ${stats.get('valor_promedio', 0):.2f}")
            
            with col2:
                st.dataframe(
                    df[["id", "nombre", "cantidad", "precio", "ubicacion"]].sort_values("nombre"),
                    use_container_width=True,
                    hide_index=True
                )
        
        with tab2:
            stock_minimo = int(obtener_config("stock_minimo") or 5)
            bajo_stock = df[df["cantidad"] < stock_minimo].sort_values("cantidad")
            if not bajo_stock.empty:
                st.warning(f"⚠️ **{len(bajo_stock)} productos con stock bajo (< {stock_minimo} unid.)**")
                st.dataframe(
                    bajo_stock[["id", "nombre", "cantidad", "ubicacion"]],
                    use_container_width=True,
                    hide_index=True,
                    column_config={"cantidad": st.column_config.NumberColumn(format="%d unid.")}
                )
            else:
                st.success(f"✅ Todos los productos tienen stock adecuado (> {stock_minimo} unid.)")
        
        with tab3:
            col1, col2 = st.columns(2)
            
            with col1:
                st.write("**Distribución por ubicación (Cantidad)**")
                st.bar_chart(df.groupby("ubicacion")["cantidad"].sum())
            
            with col2:
                st.write("**Distribución por ubicación (Valor)**")
                df_temp = df.copy()
                df_temp["valor"] = df_temp["cantidad"] * df_temp["precio"]
                st.bar_chart(df_temp.groupby("ubicacion")["valor"].sum())
            
            # Top 10 productos
            st.write("**Top 10 productos por valor**")
            df_top = df.copy()
            df_top["valor"] = df_top["cantidad"] * df_top["precio"]
            df_top = df_top.nlargest(10, "valor")[["nombre", "cantidad", "precio", "valor"]]
            st.bar_chart(df_top.set_index("nombre")["valor"])
    else:
        st.warning("📭 No hay datos. ¡Comienza agregando un producto!")

# ==================== AGREGAR PRODUCTO ====================
elif pagina == "➕ Agregar producto":
    st.subheader("➕ Agregar nuevo producto")
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.write("**Información del producto**")
        id_producto = st.text_input("🆔 ID único", placeholder="PROD-001")
        nombre = st.text_input("📝 Nombre del producto", placeholder="Ej: Laptop Dell")
        precio = st.number_input("💵 Precio unitario", min_value=0.01, step=0.01)
    
    with col2:
        st.write("**Stock inicial**")
        cantidad = st.number_input("📦 Cantidad inicial", min_value=0, step=1)
        ubicacion = st.text_input("📍 Ubicación", placeholder="Ej: Almacén A")
    
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("✅ Agregar producto", use_container_width=True):
            if not id_producto or not nombre or not ubicacion:
                st.error("❌ Por favor completa todos los campos requeridos")
            elif precio <= 0:
                st.error("❌ El precio debe ser mayor a 0")
            else:
                exito, mensaje = agregar_producto(
                    id_producto, nombre, cantidad, precio, ubicacion
                )
                if exito:
                    st.success(mensaje)
                    st.info(f"Producto '{nombre}' creado exitosamente")
                else:
                    st.error(mensaje)
    
    with col2:
        if st.button("🔄 Limpiar formulario", use_container_width=True):
            st.rerun()

# ==================== MOVIMIENTOS ====================
elif pagina == "🔄 Movimientos":
    st.subheader("🔄 Registrar movimiento de inventario")
    
    df = obtener_productos()
    
    if df.empty:
        st.warning("📭 No hay productos. Crea uno primero.")
    else:
        col1, col2, col3 = st.columns([2, 1, 1])
        
        with col1:
            producto_id = st.selectbox(
                "🏷️ Selecciona producto",
                df["id"],
                format_func=lambda x: f"{x} - {df[df['id']==x]['nombre'].values[0]}"
            )
        
        with col2:
            # Validar que el producto existe
            producto_filtrado = df[df["id"] == producto_id]
            if producto_filtrado.empty:
                st.error(f"❌ Producto con ID '{producto_id}' no encontrado")
                st.stop()
            
            producto_actual = producto_filtrado.iloc[0]
            
            # Validar que cantidad sea un valor numérico válido
            try:
                cantidad_actual = int(producto_actual['cantidad'])
                st.metric("Stock actual", f"{cantidad_actual} unid.")
            except (ValueError, TypeError):
                st.metric("Stock actual", "N/A")
                st.warning("⚠️ Cantidad inválida en base de datos")
        
        with col3:
            try:
                precio_actual = float(producto_actual['precio'])
                st.metric("Precio", f"${precio_actual:.2f}")
            except (ValueError, TypeError):
                st.metric("Precio", "N/A")
                st.warning("⚠️ Precio inválido en base de datos")
        
        st.divider()
        
        col1, col2 = st.columns(2)
        
        with col1:
            tipo_mov = st.radio("Tipo de movimiento:", ["📥 Entrada", "📤 Salida"], horizontal=True)
            cantidad = st.number_input(
                "Cantidad",
                min_value=1,
                step=1,
                help="Ingresa la cantidad sin signo"
            )
        
        with col2:
            descripcion = st.text_area(
                "📝 Descripción (opcional)",
                placeholder="Ej: Compra a proveedor X, Venta a cliente Y, etc.",
                height=100
            )
        
        col1, col2 = st.columns(2)
        
        with col1:
            if st.button("✅ Registrar movimiento", use_container_width=True):
                cantidad_actualizar = cantidad if tipo_mov == "📥 Entrada" else -cantidad
                exito, mensaje = actualizar_stock(producto_id, cantidad_actualizar, descripcion)
                
                if exito:
                    st.success(mensaje)
                else:
                    st.error(mensaje)
        
        with col2:
            if st.button("🔄 Limpiar", use_container_width=True):
                st.rerun()

# ==================== KARDEX ====================
elif pagina == "📜 Kardex":
    st.subheader("📜 Historial de movimientos (Kardex)")
    
    df = obtener_productos()
    mov = obtener_movimientos()
    
    if mov.empty:
        st.warning("📭 No hay movimientos registrados")
    else:
        col1, col2, col3 = st.columns([2, 1, 1])
        
        with col1:
            producto_id = st.selectbox(
                "🔎 Filtrar por producto",
                ["TODOS"] + list(mov["producto_id"].unique()),
                key="kardex_producto"
            )
        
        with col2:
            tipo_filtro = st.selectbox(
                "Tipo de movimiento",
                ["TODOS", "entrada", "salida"],
                key="kardex_tipo"
            )
        
        # Filtrar datos
        df_filtrado = mov.copy()
        
        if producto_id != "TODOS":
            df_filtrado = df_filtrado[df_filtrado["producto_id"] == producto_id]
        
        if tipo_filtro != "TODOS":
            df_filtrado = df_filtrado[df_filtrado["tipo"] == tipo_filtro]
        
        # Mostrar tabla
        st.dataframe(
            df_filtrado[[
               "producto_id", "tipo", "cantidad", "fecha", "descripcion"
            ]].sort_values("fecha", ascending=False),
            use_container_width=True,
            hide_index=True,
            column_config={
                "producto_id": "Producto",
                "tipo": "Tipo",
                "cantidad": st.column_config.NumberColumn(format="%d unid."),
                "fecha": "Fecha",
                "descripcion": "Descripción"
            }
        )
        
        # Gráfico si es un producto específico
        if producto_id != "TODOS":
            st.divider()
            st.write(f"**📈 Flujo acumulado de {producto_id}**")
            
            df_grafico = df_filtrado.copy()
            df_grafico["fecha"] = pd.to_datetime(df_grafico["fecha"])
            df_grafico = df_grafico.sort_values("fecha")
            df_grafico["acumulado"] = df_grafico["cantidad"].where(
                df_grafico["tipo"] == "entrada", -df_grafico["cantidad"]
            ).cumsum()
            
            st.line_chart(df_grafico.set_index("fecha")["acumulado"], use_container_width=True)

# ==================== BÚSQUEDA ====================
elif pagina == "🔍 Búsqueda":
    st.subheader("🔍 Búsqueda y filtrado avanzado")
    
    df = obtener_productos()
    
    if df.empty:
        st.warning("📭 No hay productos")
    else:
        # Filtros
        col1, col2, col3 = st.columns(3)
        
        with col1:
            buscar = st.text_input("🔎 Buscar por nombre o ID").lower()
        
        with col2:
            ubicaciones = st.multiselect(
                "📍 Ubicaciones",
                df["ubicacion"].unique(),
                default=df["ubicacion"].unique()
            )
        
        with col3:
            rango_precio = st.slider(
                "Rango de precio",
                float(df["precio"].min()),
                float(df["precio"].max()),
                (float(df["precio"].min()), float(df["precio"].max()))
            )
        
        # Aplicar filtros
        df_resultado = df[
            (df["nombre"].str.lower().str.contains(buscar) | df["id"].str.lower().str.contains(buscar)) &
            (df["ubicacion"].isin(ubicaciones)) &
            (df["precio"] >= rango_precio[0]) &
            (df["precio"] <= rango_precio[1])
        ].sort_values("nombre")
        
        st.write(f"**Resultados: {len(df_resultado)} producto(s)**")
        
        st.dataframe(
            df_resultado[[
                "id", "nombre", "cantidad", "precio", "ubicacion"
            ]],
            use_container_width=True,
            hide_index=True,
            column_config={
                "id": "ID",
                "nombre": "Nombre",
                "cantidad": st.column_config.NumberColumn(format="%d unid."),
                "precio": st.column_config.NumberColumn(format="$%.2f"),
                "ubicacion": "Ubicación"
            }
        )

# ==================== GESTIÓN ====================
elif pagina == "⚙️ Gestión":
    st.subheader("⚙️ Gestión de productos")
    
    df = obtener_productos()
    
    if df.empty:
        st.warning("📭 No hay productos")
    else:
        tab1, tab2 = st.tabs(["✏️ Editar", "🗑️ Eliminar"])
        
        with tab1:
            st.info("💡 Selecciona un producto para editar sus propiedades")
            
            producto_id = st.selectbox(
                "Producto",
                df["id"],
                format_func=lambda x: f"{x} - {df[df['id']==x]['nombre'].values[0]}",
                key="gestion_producto"
            )
            
            # Validar que el producto existe
            producto_filtrado = df[df["id"] == producto_id]
            if producto_filtrado.empty:
                st.error(f"❌ Producto con ID '{producto_id}' no encontrado")
                st.stop()
            
            producto = producto_filtrado.iloc[0]
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.write("**Información actual del producto**")
                st.write(f"🆔 **ID:** {producto['id']}")
                st.write(f"📝 **Nombre:** {producto['nombre']}")
                st.write(f"💵 **Precio:** ${producto['precio']:.2f}")
                st.write(f"📍 **Ubicación:** {producto['ubicacion']}")
                st.write(f"📦 **Stock actual:** {int(producto['cantidad'])} unidades")
            
            with col2:
                st.write("**✏️ Editar propiedades**")
                
                # Editar nombre
                nuevo_nombre = st.text_input(
                    "Nuevo nombre",
                    value=producto['nombre'],
                    key="nuevo_nombre",
                    help="Deja vacío para mantener el nombre actual"
                )
                
                # Editar precio
                nuevo_precio = st.number_input(
                    "Nuevo precio ($)",
                    min_value=0.01,
                    value=float(producto['precio']),
                    step=0.01,
                    format="%.2f",
                    key="nuevo_precio"
                )
                
                # Editar ubicación
                nueva_ubicacion = st.text_input(
                    "Nueva ubicación",
                    value=producto['ubicacion'],
                    key="nueva_ubicacion",
                    help="Deja vacío para mantener la ubicación actual"
                )
                
                # Botones de actualización
                col_btn1, col_btn2, col_btn3 = st.columns(3)
                
                cambios_realizados = False
                
                with col_btn1:
                    if st.button("📝 Nombre", use_container_width=True):
                        if nuevo_nombre and nuevo_nombre != producto['nombre']:
                            exito, mensaje = actualizar_nombre(producto_id, nuevo_nombre)
                            if exito:
                                st.success(mensaje)
                                cambios_realizados = True
                            else:
                                st.error(mensaje)
                        else:
                            st.warning("⚠️ Nombre no válido o sin cambios")
                
                with col_btn2:
                    if st.button("💵 Precio", use_container_width=True):
                        if nuevo_precio != producto['precio']:
                            exito, mensaje = actualizar_precio(producto_id, nuevo_precio)
                            if exito:
                                st.success(mensaje)
                                cambios_realizados = True
                            else:
                                st.error(mensaje)
                        else:
                            st.warning("⚠️ Precio sin cambios")
                
                with col_btn3:
                    if st.button("📍 Ubicación", use_container_width=True):
                        if nueva_ubicacion and nueva_ubicacion != producto['ubicacion']:
                            exito, mensaje = actualizar_ubicacion(producto_id, nueva_ubicacion)
                            if exito:
                                st.success(mensaje)
                                cambios_realizados = True
                            else:
                                st.error(mensaje)
                        else:
                            st.warning("⚠️ Ubicación no válida o sin cambios")
                
                if cambios_realizados:
                    st.info("🔄 Recargando datos...")
                    st.rerun()
                
                st.write("**Historial reciente**")
                mov_producto = obtener_movimientos_producto(producto_id)
                
                if not mov_producto.empty:
                    st.dataframe(
                        mov_producto.head(3)[[
                            "tipo", "cantidad", "fecha"
                        ]],
                        use_container_width=True,
                        hide_index=True
                    )
                else:
                    st.info("Sin movimientos")
        
        with tab2:
            st.warning("⚠️ **Esta acción es irreversible**")
            
            producto_eliminar = st.selectbox(
                "Selecciona producto a eliminar",
                df["id"],
                format_func=lambda x: f"{x} - {df[df['id']==x]['nombre'].values[0]}",
                key="eliminar_producto"
            )
            
            if st.checkbox(f"Confirmo que quiero eliminar '{producto_eliminar}'"):
                col1, col2 = st.columns(2)
                
                with col1:
                    if st.button("🗑️ ELIMINAR PRODUCTO", use_container_width=True, type="primary"):
                        exito, mensaje = eliminar_producto(producto_eliminar)
                        if exito:
                            st.success(mensaje)
                            st.balloons()
                        else:
                            st.error(mensaje)

# ==================== ANÁLISIS ABC ====================
elif pagina == "📈 Análisis ABC":
    st.subheader("📈 Análisis ABC (Pareto) - Clasificación de Productos")
    
    df_abc = calcular_analisis_abc()
    
    if df_abc.empty:
        st.warning("📭 No hay datos para analizar")
    else:
        st.info("💡 El análisis ABC clasifica productos por valor. Los productos A son prioritarios (80% del valor)")
        
        # Estadísticas por clase
        col1, col2, col3 = st.columns(3)
        
        clase_a = df_abc[df_abc["clase"] == "A"]
        clase_b = df_abc[df_abc["clase"] == "B"]
        clase_c = df_abc[df_abc["clase"] == "C"]
        
        with col1:
            st.metric("🔴 Clase A (Críticos)", f"{len(clase_a)} productos")
            st.caption(f"Valor: ${clase_a['valor'].sum():,.0f}")
        
        with col2:
            st.metric("🟡 Clase B (Importantes)", f"{len(clase_b)} productos")
            st.caption(f"Valor: ${clase_b['valor'].sum():,.0f}")
        
        with col3:
            st.metric("🟢 Clase C (Otros)", f"{len(clase_c)} productos")
            st.caption(f"Valor: ${clase_c['valor'].sum():,.0f}")
        
        st.divider()
        
        # Tabla
        st.write("**Clasificación detallada**")
        
        df_mostrar = df_abc[["id", "nombre", "cantidad", "valor", "porcentaje_acumulado", "clase"]].copy()
        df_mostrar["valor"] = df_mostrar["valor"].apply(lambda x: f"${x:,.0f}")
        
        # Color por clase
        def colorear_clase(val):
            if val == "A":
                return "background-color: #ffcccc"
            elif val == "B":
                return "background-color: #ffffcc"
            else:
                return "background-color: #ccffcc"
        
        st.dataframe(
            df_mostrar,
            use_container_width=True,
            hide_index=True
        )
        
        # Gráfico
        st.write("**Distribución de valor (Pareto)**")
        st.bar_chart(df_abc.set_index("nombre")["valor"])

# ==================== DESCARGAS ====================
elif pagina == "📥 Descargas":
    st.subheader("📥 Exportar datos")
    
    df = obtener_productos()
    mov = obtener_movimientos()
    
    if df.empty and mov.empty:
        st.warning("📭 No hay datos para descargar")
    else:
        tab1, tab2, tab3, tab4 = st.tabs(["📦 Productos", "📜 Movimientos", "📊 Análisis", "📄 PDF"])
        
        with tab1:
            if not df.empty:
                csv_productos = df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="⬇️ Descargar CSV",
                    data=csv_productos,
                    file_name=f"productos_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime="text/csv",
                    use_container_width=True
                )
        
        with tab2:
            if not mov.empty:
                csv_mov = mov.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="⬇️ Descargar CSV",
                    data=csv_mov,
                    file_name=f"movimientos_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime="text/csv",
                    use_container_width=True
                )
        
        with tab3:
            if not df.empty:
                # Crear reporte
                df_reporte = df.copy()
                df_reporte["valor"] = df_reporte["cantidad"] * df_reporte["precio"]
                
                stats = get_estadisticas()
                
                resumen = f"""
REPORTE DE INVENTARIO
====================
Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

RESUMEN:
- Valor total inventario: ${stats.get('valor_total', 0):,.2f}
- Stock total: {stats.get('stock_total', 0):,} unidades
- Número de productos: {stats.get('num_productos', 0)}
- Productos en bajo stock: {stats.get('productos_bajo_stock', 0)}

DETALLES POR UBICACIÓN:
{df_reporte.groupby('ubicacion').agg({
    'cantidad': 'sum',
    'valor': 'sum'
}).to_string()}
"""
                
                st.download_button(
                    label="⬇️ Descargar TXT",
                    data=resumen,
                    file_name=f"reporte_{datetime.now().strftime('%Y%m%d')}.txt",
                    mime="text/plain",
                    use_container_width=True
                )
        
        with tab4:
            if not df.empty:
                st.write("Generar reporte en TXT (PDF temporalmente no disponible)")
                if st.button("📄 Descargar TXT", use_container_width=True):
                    txt = generar_pdf_inventario()
                    st.download_button(
                        label="⬇️ Descargar TXT",
                        data=txt,
                        file_name=f"reporte_{datetime.now().strftime('%Y%m%d')}.txt",
                        mime="text/plain",
                        use_container_width=True
                    )

# ==================== CONFIGURACIÓN ====================
elif pagina == "⚙️ Configuración":
    st.subheader("⚙️ Configuración del sistema")
    
    tab1, tab2 = st.tabs(["🔧 Configuración", "📥 Importar"])
    
    with tab1:
        col1, col2 = st.columns(2)
        
        with col1:
            st.write("**Stock mínimo** (para alertas)")
            stock_min = st.number_input(
                "Cantidad mínima",
                value=int(obtener_config("stock_minimo") or 5),
                min_value=1,
                step=1
            )
            if st.button("💾 Guardar stock mínimo", use_container_width=True):
                actualizar_config("stock_minimo", str(stock_min))
                st.success("✅ Stock mínimo actualizado")
        
        with col2:
            st.write("**Tema**")
            tema_actual = obtener_config("tema")
            tema_nuevo = st.selectbox(
                "Selecciona tema",
                ["light", "dark"],
                index=0 if tema_actual == "light" else 1
            )
            if st.button("💾 Guardar tema", use_container_width=True):
                actualizar_config("tema", tema_nuevo)
                st.success("✅ Tema actualizado")
        
        st.divider()
        
        st.write("**Información del sistema**")
        col1, col2, col3 = st.columns(3)
        
        stats = get_estadisticas()
        with col1:
            st.metric("💾 Tamaño BD", f"{os.path.getsize('inventario.db') / 1024:.1f} KB")
        with col2:
            st.metric("📦 Productos", stats.get('num_productos', 0))
        with col3:
            st.metric("📊 Movimientos", len(obtener_movimientos()) if not obtener_movimientos().empty else 0)
    
    with tab2:
        st.write("**Importar productos desde CSV**")
        st.info("Formato requerido: id, nombre, cantidad, precio, ubicacion")
        
        archivo = st.file_uploader("Selecciona archivo CSV", type="csv")
        
        if archivo:
            if st.button("📥 Importar", use_container_width=True):
                exito, mensaje = importar_productos_csv(archivo)
                if exito:
                    st.success(mensaje)
                else:
                    st.error(mensaje)

# ==================== FOOTER ====================
st.divider()
col1, col2, col3 = st.columns(3)
with col1:
    st.caption("📦 Inventario Profesional v2.0")
with col2:
    st.caption(f"Última actualización: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
with col3:
    st.caption("✅ Base de datos sincronizada")