import io
import altair as alt
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
from pathlib import Path

st.set_page_config(
    page_title="Tarjetas de Crédito",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="expanded",
)

BASE = Path(__file__).parent
LOGO_PATH = BASE / "imagenes" / "ancora_blanco.png"

# Hoja compartida como "Cualquiera con el link" (solo lectura) -- mismo patron
# que Facturacion: se descarga el libro completo via export?format=xlsx, sin
# API key ni cuenta de servicio.
SHEET_ID = "1vdKEg8gKXEddi3ucx_vPPuI4KDmhoZWlks9KWZZtelY"
SHEET_EXPORT_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=xlsx"

PERSONAS = {"JSC": "Jorge Salas Cacho", "JSB": "Jorge Salas Benito"}
C_JSC = "#1d7b8a"   # teal (color de marca Ancora)
C_JSB = "#ffd43b"   # amarillo, para distinguir de JSC
C_NEG = "#ff6b6b"
PALETA_CAT = ["#1d7b8a", "#ffd43b", "#51cf66", "#ff6b6b", "#8c7ae6", "#ff9f43",
              "#54a0ff", "#c7d2e0", "#ee5a6f", "#1dd1a1", "#feca57", "#576574"]

def color_for(i):
    return PALETA_CAT[i % len(PALETA_CAT)]

# ── Helpers de formato ──────────────────────────────────────────────────────────
def fmt(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"-${-v:,.0f}" if v < 0 else f"${v:,.0f}"

def fmt_m(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    signo, a = ("-" if v < 0 else ""), abs(v)
    if a >= 1e6:
        return f"{signo}${a/1e6:,.1f}M"
    if a >= 1e3:
        return f"{signo}${a/1e3:,.0f}k"
    return f"{signo}${a:,.0f}"

def pct(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{'+' if v >= 0 else ''}{v*100:.1f}%"

def norm_txt(v):
    if not isinstance(v, str):
        return None
    v = v.strip()
    return v if v else None

def coalesce_col(df, *names):
    for n in names:
        if n in df.columns:
            return df[n]
    return pd.Series([None] * len(df), index=df.index)

def norm_oficina(v):
    """'España' y 'ESPAÑA' son la misma oficina capturada con mayusculas
    distintas en el origen -- se normaliza a Title Case para no duplicarla
    en las graficas (no intenta unificar idiomas, 'Londres' y 'London' se
    quedan separadas, son palabras distintas, no una diferencia de mayusculas)."""
    s = norm_txt(v)
    return s.title() if s else None

# ── Altair: tema oscuro (mismo patron que los otros 3 tableros Ancora) ─────────
_AX_LABEL = "#c7d2e0"
_AX_GRID = "#2e4d70"
_AX_DOMAIN = "#3a5578"

def style_chart(chart):
    """Aplica fondo transparente y colores claros (ejes/leyenda/titulo) para el tema oscuro."""
    return (
        chart.properties(background="transparent")
        .configure_view(strokeOpacity=0)
        .configure_axis(labelColor=_AX_LABEL, titleColor=_AX_LABEL,
                        gridColor=_AX_GRID, domainColor=_AX_DOMAIN)
        .configure_legend(labelColor=_AX_LABEL, titleColor=_AX_LABEL)
        .configure_title(color="#F6F6F6")
    )

def barra_h(serie, color, height=None, formateador=fmt_m, top_n=None):
    """Barra horizontal generica (serie ya ordenada descendente)."""
    s = serie if top_n is None else serie.head(top_n)
    data = pd.DataFrame({"categoria": s.index.astype(str), "valor": s.values})
    data["etiqueta"] = data["valor"].map(formateador)
    h = height or max(220, 28 * len(data))
    chart = alt.Chart(data).mark_bar(color=color, cornerRadiusEnd=3).encode(
        y=alt.Y("categoria:N", sort=data["categoria"].tolist(), title=None,
               axis=alt.Axis(labelLimit=260)),
        x=alt.X("valor:Q", title=None, axis=None),
        tooltip=[alt.Tooltip("categoria:N", title="Concepto"),
                alt.Tooltip("etiqueta:N", title="Monto")],
    ).properties(height=h, width="container")
    texto = alt.Chart(data).mark_text(align="left", dx=4, color="#F6F6F6", fontSize=11).encode(
        y=alt.Y("categoria:N", sort=data["categoria"].tolist()),
        x="valor:Q", text="etiqueta:N",
    )
    return style_chart(chart + texto)

def _worst_aspecto(fila, lado):
    """Peor relacion de aspecto (ancho/alto maximo) que resultaria si 'fila'
    (areas ya escaladas) se acomoda en una franja de grosor fijo 'lado'."""
    s = sum(fila)
    if s == 0 or lado == 0:
        return float("inf")
    return max((lado ** 2) * max(fila) / (s ** 2), (s ** 2) / ((lado ** 2) * min(fila)))

def _squarify(areas, x, y, w, h):
    """Layout 'squarified treemap' (Bruls/Huizing/van Wijk): reparte 'areas'
    (ya escaladas para que sumen w*h) en rectangulos dentro de x,y,w,h,
    agrupando en franjas que mantienen los rectangulos lo mas cuadrados
    posible en vez de tiras largas y angostas."""
    restantes = [a for a in areas if a > 0]
    rects = []
    while restantes:
        lado = min(w, h)
        fila = [restantes[0]]
        i = 1
        while i < len(restantes):
            probar = fila + [restantes[i]]
            if _worst_aspecto(probar, lado) <= _worst_aspecto(fila, lado):
                fila = probar
                i += 1
            else:
                break
        area_fila = sum(fila)
        if w >= h:
            ancho_fila = area_fila / h if h else 0
            cy = y
            for a in fila:
                alto = a / ancho_fila if ancho_fila else 0
                rects.append((x, cy, ancho_fila, alto))
                cy += alto
            x, w = x + ancho_fila, w - ancho_fila
        else:
            alto_fila = area_fila / w if w else 0
            cx = x
            for a in fila:
                ancho = a / alto_fila if alto_fila else 0
                rects.append((cx, y, ancho, alto_fila))
                cx += ancho
            y, h = y + alto_fila, h - alto_fila
        restantes = restantes[len(fila):]
    return rects

def treemap_generico(serie, titulo_campo="categoria", height=460, top_n=12, min_pct=0.0, click_param=None):
    """Mapa de arbol generico: cada rectangulo es proporcional al monto,
    coloreado con la misma paleta que el resto del tablero. Ademas del tope
    'top_n', cualquier categoria por debajo de 'min_pct' del total tambien se
    agrupa en "Otros" -- sin esto, un desglose con muchos conceptos chicos
    (ej. "UBER TRIP $16k") deja varias cajas minusculas e ilegibles aunque
    entren dentro del top_n."""
    s = serie.sort_values(ascending=False)
    if min_pct > 0:
        total_bruto = s.sum() or 1.0
        n_califican = int((s / total_bruto >= min_pct).sum())
        top_n = min(top_n, max(1, n_califican))
    if len(s) > top_n:
        s = pd.concat([s.head(top_n), pd.Series({"Otros": s.iloc[top_n:].sum()})])
    total = s.sum() or 1.0
    ANCHO_L, ALTO_L = 1200, height
    areas = [v / total * ANCHO_L * ALTO_L for v in s.values]
    rects = _squarify(areas, 0, 0, ANCHO_L, ALTO_L)

    data = pd.DataFrame(rects, columns=["x0", "y0", "w", "h"])
    data["x1"] = data["x0"] + data["w"]
    data["y1"] = data["y0"] + data["h"]
    data["categoria"] = s.index.astype(str)
    data["valor"] = s.values
    data["valor_fmt"] = data["valor"].map(fmt_m)
    data["pct_val"] = data["valor"] / total
    # Cada caja SIEMPRE muestra su nombre: tamaño de letra proporcional a la
    # caja (igual que el treemap de Facturacion) y, si ni asi cabe, se trunca
    # con "…" en vez de ocultarse -- ~0.56 unidades logicas de ancho por
    # caracter y por punto de fontSize (ANCHO_L se eligio cercano al ancho
    # real en px, asi que logico ~= pixeles).
    PX_CHAR_POR_FS = 0.56
    def _fila(r):
        fs = max(7, min(12, r["h"] / 4, r["w"] / 6))
        px_char = fs * PX_CHAR_POR_FS
        max_chars = max(1, int((r["w"] - 6) / px_char))
        nombre = r["categoria"]
        if len(nombre) > max_chars:
            nombre = (nombre[:max(1, max_chars - 1)] + "…") if max_chars > 1 else nombre[:1]
        ancho_valor = len(r["valor_fmt"]) * px_char
        dos_lineas = r["h"] >= 2.4 * fs + 10 and r["w"] >= ancho_valor + 6
        etiqueta = (nombre + "\n" + r["valor_fmt"]) if dos_lineas else nombre
        return pd.Series({"etiqueta": etiqueta, "fs": fs})
    data[["etiqueta", "fs"]] = data.apply(_fila, axis=1)
    data["color"] = [color_for(i) for i in range(len(data))]

    base = alt.Chart(data).encode(
        x=alt.X("x0:Q", axis=None, scale=alt.Scale(domain=[0, ANCHO_L])),
        x2="x1:Q",
        y=alt.Y("y0:Q", axis=None, scale=alt.Scale(domain=[0, ALTO_L], reverse=True)),
        y2="y1:Q",
    )
    cajas = base.mark_rect(stroke="#0f2137", strokeWidth=2, cornerRadius=3).encode(
        color=alt.Color("categoria:N", sort=data["categoria"].tolist(),
                        scale=alt.Scale(domain=data["categoria"].tolist(), range=data["color"].tolist()),
                        legend=None),
        tooltip=[alt.Tooltip("categoria:N", title=titulo_campo),
                alt.Tooltip("valor_fmt:N", title="Monto"),
                alt.Tooltip("pct_val:Q", title="% del total", format=".1%")],
    )
    if click_param:
        # Clic en una caja = seleccion de esa categoria (la caja activa queda
        # opaca, el resto se atenua) -- Streamlit lee esto via on_select="rerun"
        # y el param 'click_param' en el evento devuelto.
        click = alt.selection_point(fields=["categoria"], name=click_param)
        cajas = cajas.add_params(click).encode(
            opacity=alt.condition(click, alt.value(1.0), alt.value(0.55)),
        )
    texto = alt.Chart(data).mark_text(color="#0f2137", fontWeight=600, lineBreak="\n").encode(
        x=alt.X("xc:Q", axis=None, scale=alt.Scale(domain=[0, ANCHO_L])),
        y=alt.Y("yc:Q", axis=None, scale=alt.Scale(domain=[0, ALTO_L], reverse=True)),
        text="etiqueta:N",
        size=alt.Size("fs:Q", legend=None, scale=None),
    ).transform_calculate(xc="(datum.x0 + datum.x1) / 2", yc="(datum.y0 + datum.y1) / 2")
    return style_chart((cajas + texto).properties(width="container", height=height))

def barras_anuales(df, campo, height=360):
    """Barras agrupadas por año y 'campo' (persona, o cualquier categoria), para
    el comparativo 2024-2026."""
    data = df.groupby(["anio", campo])["monto"].sum().reset_index()
    data["etiqueta"] = data["monto"].map(fmt_m)
    chart = alt.Chart(data).mark_bar().encode(
        x=alt.X("anio:O", title=None),
        xOffset=f"{campo}:N",
        y=alt.Y("monto:Q", title=None, axis=alt.Axis(labelExpr=(
            "abs(datum.value) >= 1000000 ? '$' + format(datum.value/1000000, ',.1f') + 'M' : "
            "abs(datum.value) >= 1000 ? '$' + format(datum.value/1000, ',.0f') + 'k' : "
            "'$' + format(datum.value, ',.0f')"))),
        color=alt.Color(f"{campo}:N", scale=alt.Scale(range=PALETA_CAT),
                        legend=alt.Legend(title=None, orient="top")),
        tooltip=[alt.Tooltip("anio:O", title="Año"), alt.Tooltip(f"{campo}:N"),
                alt.Tooltip("etiqueta:N", title="Monto")],
    ).properties(height=height, width="container")
    return style_chart(chart)

# ── Carga de datos ───────────────────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner="Descargando datos de Google Sheets...")
def _descargar_libro():
    resp = requests.get(SHEET_EXPORT_URL, timeout=30)
    resp.raise_for_status()
    return resp.content

# La columna FACTURA (solo JSC) trae el UUID real del CFDI cuando ya se
# concilio la factura, o un texto fijo cuando no -- "ND" (no disponible),
# "ND/FAMILIA" (gasto familiar sin factura individual) o "Ticket" (pagado con
# ticket, no factura). Cualquier otra cosa (un UUID) cuenta como facturado.
FACTURA_SIN_UUID = {"ND", "ND/FAMILIA", "TICKET"}

def _es_facturado(v):
    if not isinstance(v, str) or not v.strip():
        return False
    return v.strip().upper() not in FACTURA_SIN_UUID

@st.cache_data(show_spinner=False)
def load_jsc():
    """JSC no trae una hoja ya consolidada (a diferencia de JSB) -- se arma
    uniendo las 3 hojas anuales ('2024-JSC'/'2025-JSC'/'2026-JSC'), cuyos
    nombres de columna varian un poco de un año a otro (Montos/Importe,
    Area/AREA) -- se unifican con coalesce_col()."""
    bio = io.BytesIO(_descargar_libro())
    partes = []
    for anio, hoja in [(2024, "2024-JSC"), (2025, "2025-JSC"), (2026, "2026-JSC")]:
        df = pd.read_excel(bio, sheet_name=hoja, header=0)
        df.columns = [str(c).strip() for c in df.columns]
        out = pd.DataFrame(index=df.index)
        out["fecha"] = pd.to_datetime(coalesce_col(df, "Fecha de Compra"), errors="coerce")
        out["descripcion"] = coalesce_col(df, "Descripción").map(norm_txt)
        out["monto"] = pd.to_numeric(coalesce_col(df, "Montos", "Importe"), errors="coerce").fillna(0.0)
        out["cc"] = coalesce_col(df, "CC").map(lambda v: str(v).strip() if pd.notna(v) else None)
        out["area"] = coalesce_col(df, "Area", "AREA").map(norm_txt)
        out["factura"] = coalesce_col(df, "FACTURA")
        out["producto"] = coalesce_col(df, "Producto").map(norm_txt).fillna("SIN CATEGORIZAR")
        out["oficina"] = coalesce_col(df, "Oficina").map(norm_oficina).fillna("CDMX")
        out["anio"] = anio
        partes.append(out)
    df = pd.concat(partes, ignore_index=True)
    df["facturado"] = df["factura"].map(_es_facturado)
    df["persona"] = "JSC"
    return df

@st.cache_data(show_spinner=False)
def load_jsb():
    """JSB ya trae una hoja consolidada ('RESUMEN JSB') con los 3 años juntos."""
    bio = io.BytesIO(_descargar_libro())
    df = pd.read_excel(bio, sheet_name="RESUMEN JSB", header=0)
    df.columns = [str(c).strip() for c in df.columns]
    out = pd.DataFrame(index=df.index)
    out["anio"] = pd.to_numeric(coalesce_col(df, "Año"), errors="coerce")
    out = out[out["anio"].notna()].copy()
    out["anio"] = out["anio"].astype(int)
    out["cc"] = coalesce_col(df, "CC").loc[out.index].map(lambda v: str(v).strip() if pd.notna(v) else None)
    out["producto"] = coalesce_col(df, "Producto").loc[out.index].map(norm_txt).fillna("SIN CATEGORIZAR")
    out["oficina"] = coalesce_col(df, "Oficina").loc[out.index].map(norm_oficina).fillna("CDMX")
    out["monto"] = pd.to_numeric(coalesce_col(df, "Importe").loc[out.index], errors="coerce").fillna(0.0)
    out["persona"] = "JSB"
    return out

try:
    jsc_df = load_jsc()
    jsb_df = load_jsb()
except Exception as e:
    st.error(f"Error al descargar/leer los datos de Google Sheets:\n\n`{SHEET_EXPORT_URL}`\n\n{e}")
    st.stop()

# Mapeo CC -> departamento: solo JSC trae la columna 'Area' con el nombre del
# departamento: se usa el valor mas frecuente por CC y se aplica a AMBAS
# personas (los codigos de CC son el mismo esquema compartido). Se descartan
# errores de formula de Excel que se colaron como si fueran nombre de area
# (ej. CC "PENDIENTE" -> "#VALUE!" en el origen) -- 'area_limpia' nulea esos
# antes de usarla en cualquier lado (mapeo Y respaldo).
jsc_df["area_limpia"] = jsc_df["area"].where(~jsc_df["area"].fillna("").str.startswith("#"))
_cc_depto = (jsc_df.dropna(subset=["cc", "area_limpia"])
             .groupby("cc")["area_limpia"]
             .agg(lambda s: s.value_counts().idxmax()))
CC_A_DEPTO = _cc_depto.to_dict()
jsc_df["departamento"] = jsc_df["cc"].map(CC_A_DEPTO).fillna(jsc_df["area_limpia"]).fillna("SIN DEPARTAMENTO")
jsb_df["departamento"] = jsb_df["cc"].map(CC_A_DEPTO).fillna("SIN DEPARTAMENTO")

COLS_COMUNES = ["anio", "cc", "departamento", "producto", "oficina", "monto", "persona"]
mov_df = pd.concat([jsc_df[COLS_COMUNES], jsb_df[COLS_COMUNES]], ignore_index=True)

# ── Estilos ────────────────────────────────────────────────────────────────────
components.html("""
<script>
window.parent.document.documentElement.setAttribute('translate','no');
window.parent.document.documentElement.classList.add('notranslate');
</script>
""", height=0)

st.markdown("""
<style>
section[data-testid="stSidebar"] img { pointer-events: none; }
section[data-testid="stSidebar"] [data-testid="StyledFullScreenButton"] { display: none; }
section[data-testid="stSidebar"] > div:first-child { padding-top: 0 !important; }
section[data-testid="stSidebar"] .block-container { padding-top: 0 !important; }
div[data-testid="stRadio"] > div { gap: 2px !important; }
div[data-testid="stRadio"] label {
    display: flex !important; align-items: center !important;
    padding: 10px 14px !important; border-radius: 8px !important;
    cursor: pointer !important; font-size: 0.9rem !important;
    margin-bottom: 2px !important; transition: background 0.15s !important; border: none !important;
}
div[data-testid="stRadio"] label:hover { background: rgba(255,255,255,0.08) !important; }
div[data-testid="stRadio"] label:has(input:checked) {
    background: rgba(255,255,255,0.13) !important; font-weight: 600 !important;
}
div[data-testid="stRadio"] label > div:first-child { display: none !important; }
div[data-testid="stRadio"] { border: none !important; }
</style>
""", unsafe_allow_html=True)

with st.sidebar:
    if LOGO_PATH.exists():
        st.image(str(LOGO_PATH), use_container_width=True)
    st.markdown("---")
    st.caption("FILTROS")
    anios_sel = st.multiselect("Año", [2024, 2025, 2026], default=[2024, 2025, 2026])
    persona_sel = st.radio("Persona", ["Ambos", "JSC", "JSB"], key="persona_filtro")
    st.markdown("---")
    st.caption("Tarjetas de Crédito · Ancora")

dff = mov_df[mov_df["anio"].isin(anios_sel)] if anios_sel else mov_df.iloc[0:0]
if persona_sel != "Ambos":
    dff = dff[dff["persona"] == persona_sel]

etiqueta_personas = (PERSONAS.get(persona_sel, persona_sel) if persona_sel != "Ambos"
                     else "Jorge Salas Cacho y Jorge Salas Benito")

if dff.empty:
    st.info("Sin movimientos para los filtros seleccionados.")
    st.stop()

TOT = dff["monto"].sum()
N_MOV = len(dff)

# Resumen y Detalle de Movimientos van en pestañas (st.tabs) dentro de esta
# misma página -- el Detalle es una tabla de miles de filas y el usuario pidió
# que no viviera pegada al resumen inicial.
st.title("Tarjetas de Crédito")
st.caption(f"{etiqueta_personas} · {', '.join(str(a) for a in sorted(anios_sel))}")

c1, c2, c3 = st.columns(3)
c1.metric("Total gastado acumulado a septiembre del 2026", fmt_m(TOT))
c2.metric("Movimientos", f"{N_MOV:,}")
c3.metric("Centros de costo distintos", f"{dff['cc'].nunique():,}")

tab_resumen, tab_detalle = st.tabs(["Resumen", "Detalle de Movimientos"])

with tab_resumen:
    st.subheader("Gasto por año" + (" y persona" if persona_sel == "Ambos" else ""))
    if persona_sel == "Ambos":
        st.altair_chart(barras_anuales(dff, "persona"), use_container_width=True)
    else:
        por_anio = dff.groupby("anio")["monto"].sum()
        st.altair_chart(barra_h(por_anio.sort_index(ascending=False), C_JSC if persona_sel == "JSC" else C_JSB,
                                height=220), use_container_width=True)

    resumen = dff.groupby("anio")["monto"].sum().sort_index()
    if len(resumen) > 1:
        st.subheader("Variación año contra año")
        cols = st.columns(len(resumen))
        anios_ordenados = resumen.index.tolist()
        for i, anio in enumerate(anios_ordenados):
            delta = None
            if i > 0 and resumen.iloc[i - 1]:
                delta = pct((resumen.iloc[i] - resumen.iloc[i - 1]) / resumen.iloc[i - 1])
            # 2026 viene incompleto (datos acumulados solo a septiembre) -- se
            # etiqueta distinto para no dar a entender que es el año completo.
            etiqueta_anio = "Ene-Sep 2026" if anio == 2026 else str(anio)
            cols[i].metric(etiqueta_anio, fmt_m(resumen.iloc[i]), delta=delta)

    st.divider()
    st.subheader("Resumen Consolidado por Producto")
    colf1, colf2 = st.columns(2)
    with colf1:
        anio_tabla = st.selectbox("Año", [2024, 2025, 2026], index=2, key="anio_tabla_prod")
    with colf2:
        tarjeta_tabla = st.selectbox("Tarjeta", ["Ambos", "JSC", "JSB"], key="tarjeta_tabla_prod")

    base_tabla = mov_df[mov_df["anio"] == anio_tabla]
    if tarjeta_tabla != "Ambos":
        base_tabla = base_tabla[base_tabla["persona"] == tarjeta_tabla]

    por_prod_tabla = base_tabla.groupby("producto")["monto"].sum().sort_values(ascending=False)
    if por_prod_tabla.empty:
        st.info("Sin movimientos para ese año/tarjeta.")
    else:
        tot_tabla = por_prod_tabla.sum() or 1.0
        # El 80/20 real se lee en el % ACUMULADO (no en el % individual de cada
        # fila): baja por la lista ya ordenada de mayor a menor hasta juntar el
        # 80% del gasto total -- esos productos se quedan uno por uno; todo lo
        # que cae DESPUES de ese corte (la cola larga del 20% restante) se
        # consolida en una sola fila "Otros", a pedido del usuario.
        acumulado = por_prod_tabla.cumsum() / tot_tabla
        es_80 = acumulado <= 0.8
        principales = por_prod_tabla[es_80]
        otros_monto = float(por_prod_tabla[~es_80].sum())

        filas = principales.reset_index()
        filas.columns = ["Producto", "Total"]
        filas["pct_acum"] = (principales.cumsum() / tot_tabla).values
        if otros_monto > 0:
            filas = pd.concat([filas, pd.DataFrame(
                [{"Producto": "Otros", "Total": otros_monto, "pct_acum": 1.0}])], ignore_index=True)

        filas["% del total"] = (filas["Total"] / tot_tabla).map(pct)
        filas["Grupo"] = ["80%"] * len(principales) + (["20%"] if otros_monto > 0 else [])
        filas["Total"] = filas["Total"].map(fmt)
        filas = filas.drop(columns=["pct_acum"])

        fila_total = pd.DataFrame([{"Producto": "TOTAL", "Total": fmt(tot_tabla), "% del total": "+100.0%",
                                   "Grupo": "—"}])
        tabla_resumen = pd.concat([filas, fila_total], ignore_index=True)
        st.dataframe(tabla_resumen, use_container_width=True, hide_index=True,
                    height=min(38 * len(tabla_resumen) + 38, 600))

    st.divider()
    st.subheader("Gasto por Centro de Costo")
    por_cc = dff.groupby(["cc", "departamento"])["monto"].sum().sort_values(ascending=False)
    etiquetas_cc = pd.Series(
        por_cc.values,
        index=[f"{cc}  ·  {depto}" for cc, depto in por_cc.index],
    )
    st.altair_chart(barra_h(etiquetas_cc, C_JSC, height=max(300, 26 * min(10, len(etiquetas_cc))), top_n=10),
                    use_container_width=True)
    tabla_cc = pd.DataFrame({
        "Centro de Costo": [cc for cc, _ in por_cc.index],
        "Departamento": [d for _, d in por_cc.index],
        "Gasto": por_cc.values,
        "% del total": por_cc.values / (TOT or 1),
    })
    tabla_cc["Gasto"] = tabla_cc["Gasto"].map(fmt)
    tabla_cc["% del total"] = tabla_cc["% del total"].map(pct)
    st.dataframe(tabla_cc, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Gasto por Oficina")

    if "oficina_drill" not in st.session_state:
        st.session_state["oficina_drill"] = None

    if st.session_state["oficina_drill"] is None:
        st.caption("Los movimientos sin oficina especificada en el origen se asignan a CDMX. "
                  "Da clic en una oficina del mapa para ver su desglose por producto.")
        por_oficina = dff.groupby("oficina")["monto"].sum().sort_values(ascending=False)
        evento_of = st.altair_chart(
            treemap_generico(por_oficina, titulo_campo="Oficina", height=420, top_n=10, click_param="click_oficina"),
            use_container_width=True, on_select="rerun", key="treemap_oficina",
        )
        tabla_of = por_oficina.reset_index()
        tabla_of.columns = ["Oficina", "Gasto"]
        tabla_of["% del total"] = (tabla_of["Gasto"] / (TOT or 1)).map(pct)
        tabla_of["Gasto"] = tabla_of["Gasto"].map(fmt)
        st.dataframe(tabla_of, use_container_width=True, hide_index=True)

        puntos_of = (evento_of.selection.get("click_oficina") or []) if evento_of else []
        oficina_click = puntos_of[0].get("categoria") if puntos_of else None
        if oficina_click == "Otros":
            st.session_state["oficina_drill"] = "__OTROS__"
            st.rerun()
        elif oficina_click:
            st.session_state["oficina_drill"] = oficina_click
            st.rerun()
    elif st.session_state["oficina_drill"] == "__OTROS__":
        if st.button("← Regresar a oficinas"):
            st.session_state["oficina_drill"] = None
            st.rerun()
        st.caption("Oficinas agrupadas en \"Otros\" (fuera del top 10).")
        por_oficina = dff.groupby("oficina")["monto"].sum().sort_values(ascending=False)
        cola_oficinas = por_oficina.iloc[10:]
        if cola_oficinas.empty:
            st.info("No hay oficinas adicionales.")
        else:
            st.altair_chart(treemap_generico(cola_oficinas, titulo_campo="Oficina", height=420, top_n=len(cola_oficinas)),
                            use_container_width=True)
    else:
        oficina_sel = st.session_state["oficina_drill"]
        if st.button("← Regresar a oficinas"):
            st.session_state["oficina_drill"] = None
            st.rerun()
        st.caption(f"Desglose de **{oficina_sel}** por producto.")
        base_of = dff[dff["oficina"] == oficina_sel]
        por_prod_of = base_of.groupby("producto")["monto"].sum().sort_values(ascending=False)
        if por_prod_of.empty:
            st.info("Sin movimientos para esta oficina con los filtros actuales.")
        else:
            st.altair_chart(treemap_generico(por_prod_of, titulo_campo="Producto", height=420, top_n=10, min_pct=0.03),
                            use_container_width=True)

with tab_detalle:
    st.caption("Todos los movimientos individuales, filtrables por año, concepto y tarjeta.")

    colf3, colf4, colf5 = st.columns(3)
    with colf3:
        anios_det = st.multiselect("Año", [2024, 2025, 2026], default=[2024, 2025, 2026], key="anios_detalle")
    with colf4:
        conceptos_todos = sorted(mov_df["producto"].dropna().unique().tolist())
        conceptos_det = st.multiselect("Concepto", conceptos_todos, default=[], key="conceptos_detalle",
                                       placeholder="Todos los conceptos")
    with colf5:
        tarjeta_det = st.selectbox("Tarjeta", ["Ambos", "JSC", "JSB"], key="tarjeta_detalle")

    detalle = mov_df[mov_df["anio"].isin(anios_det)] if anios_det else mov_df.iloc[0:0]
    if conceptos_det:
        detalle = detalle[detalle["producto"].isin(conceptos_det)]
    if tarjeta_det != "Ambos":
        detalle = detalle[detalle["persona"] == tarjeta_det]

    st.caption(f"{len(detalle):,} movimientos · {fmt(detalle['monto'].sum())} en total")
    tabla_detalle = detalle.sort_values("monto", ascending=False).reset_index(drop=True)
    tabla_detalle = pd.DataFrame({
        "Año": tabla_detalle["anio"],
        "Tarjeta": tabla_detalle["persona"],
        "Concepto": tabla_detalle["producto"],
        "Centro de Costo": tabla_detalle["cc"].fillna("—"),
        "Departamento": tabla_detalle["departamento"],
        "Oficina": tabla_detalle["oficina"],
        "Monto": tabla_detalle["monto"].map(fmt),
    })
    st.dataframe(tabla_detalle, use_container_width=True, hide_index=True, height=480)
