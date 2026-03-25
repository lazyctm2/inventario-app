import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
import os
from io import BytesIO, StringIO
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.units import inch
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

conn.commit()

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
        if df_producto.empty:
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

def eliminar_producto(producto_id):
    """Elimina un producto y todos sus movimientos"""
    try:
        c.execute("DELETE FROM productos WHERE id = ?", (producto_id,))
        conn.commit()
        st.cache_data.clear()
        return True, "✅ Producto eliminado exitosamente"
    except Exception as e:
        return False, f"❌ Error al eliminar: {str(e)}"

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
    """Genera PDF con reporte de inventario"""
    df = obtener_productos()
    stats = get_estadisticas()
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.5*inch)
    style = getSampleStyleSheet()
    story = []
    
    # Título
    titulo = Paragraph("<b>REPORTE DE INVENTARIO</b>", style['Title'])
    story.append(titulo)
    story.append(Spacer(1, 0.2*inch))
    
    # Fecha
    fecha = Paragraph(f"<b>Fecha:</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}", style['Normal'])
    story.append(fecha)
    story.append(Spacer(1, 0.3*inch))
    
    # Resumen
    story.append(Paragraph("<b>RESUMEN EJECUTIVO</b>", style['Heading2']))
    datos_resumen = [
        ["Valor Total Inventario", f"${stats.get('valor_total', 0):,.2f}"],
        ["Stock Total", f"{stats.get('stock_total', 0):,} unidades"],
        ["Número de Productos", f"{stats.get('num_productos', 0)}"],
        ["Productos Bajo Stock", f"{stats.get('productos_bajo_stock', 0)}"],
        ["Ubicaciones", f"{stats.get('ubicaciones', 0)}"]
    ]
    
    tabla_resumen = Table(datos_resumen, colWidths=[3*inch, 2*inch])
    tabla_resumen.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.lightgrey),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 1, colors.grey)
    ]))
    
    story.append(tabla_resumen)
    story.append(Spacer(1, 0.3*inch))
    
    # Productos
    if not df.empty:
        story.append(Paragraph("<b>LISTADO DE PRODUCTOS</b>", style['Heading2']))
        
        df_tabla = df[["id", "nombre", "cantidad", "precio", "ubicacion"]].copy()
        df_tabla = df_tabla.astype(str)
        
        datos = [["ID", "Nombre", "Cantidad", "Precio", "Ubicación"]]
        for _, row in df_tabla.iterrows():
            datos.append(list(row))
        
        tabla = Table(datos, colWidths=[1*inch, 2*inch, 1*inch, 1*inch, 1.5*inch])
        tabla.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey)
        ]))
        
        story.append(tabla)
    
    doc.build(story)
    buffer.seek(0)
    return buffer

# ==================== CONFIGURACIÓN ====================
def crear_tabla_configuracion():
    """Crea tabla de configuración"""
    c.execute("""
    CREATE TABLE IF NOT EXISTS configuracion (
        clave TEXT PRIMARY KEY,
        valor TEXT NOT NULL
    )
    """)
    
    # Configuraciones por defecto
    defaults = {
        "stock_minimo": "5",
        "tema": "light",
        "moneda": "USD"
    }
    
    for k, v in defaults.items():
        try:
            c.execute("INSERT INTO configuracion (clave, valor) VALUES (?, ?)", (k, v))
        except sqlite3.IntegrityError:
            pass
    
    conn.commit()

crear_tabla_configuracion()

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
        st.empty()
    
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
            producto_actual = df[df["id"] == producto_id].iloc[0]
            st.metric("Stock actual", f"{int(producto_actual['cantidad'])} unid.")
        
        with col3:
            st.metric("Precio", f"${producto_actual['precio']:.2f}")
        
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
        
        with col3:
            st.empty()
        
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
            st.info("💡 Selecciona un producto para ver su historial")
            
            producto_id = st.selectbox(
                "Producto",
                df["id"],
                format_func=lambda x: f"{x} - {df[df['id']==x]['nombre'].values[0]}",
                key="gestion_producto"
            )
            
            producto = df[df["id"] == producto_id].iloc[0]
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.write("**Información del producto**")
                st.write(f"🆔 **ID:** {producto['id']}")
                st.write(f"📝 **Nombre:** {producto['nombre']}")
                st.write(f"💵 **Precio:** ${producto['precio']:.2f}")
                st.write(f"📍 **Ubicación:** {producto['ubicacion']}")
                st.write(f"📦 **Stock actual:** {int(producto['cantidad'])} unidades")
            
            with col2:
                st.write("**Historial reciente**")
                mov_producto = obtener_movimientos_producto(producto_id)
                
                if not mov_producto.empty:
                    st.dataframe(
                        mov_producto.head(5)[[
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
                st.write("Generar reporte en PDF")
                if st.button("📄 Descargar PDF", use_container_width=True):
                    pdf = generar_pdf_inventario()
                    st.download_button(
                        label="⬇️ Descargar PDF",
                        data=pdf,
                        file_name=f"reporte_{datetime.now().strftime('%Y%m%d')}.pdf",
                        mime="application/pdf",
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