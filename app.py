"""
Dashboard estadístico de pacientes – Genética (sisa-igehm)
Lee las columnas ID, Edad, Diagnostico, Medica y SISA, en este orden de prioridad:
  1. Google Sheets, si en secrets.toml hay un sheet_id.
  2. Un CSV local en data/pacientes.csv (útil al desarrollar en tu PC).
  3. Un CSV que se sube desde la app (deploy: los datos quedan solo en memoria).
"""
import hmac
import io
import re
import unicodedata
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="Estadísticas Genética", page_icon="🧬", layout="wide")

COLUMNAS = ["ID", "Edad", "Diagnostico", "Medica", "SISA"]
CSV_POR_DEFECTO = Path(__file__).parent / "data" / "pacientes.csv"

# Variantes / errores de tipeo -> nombre final.
# La clave va normalizada: minúsculas, sin tildes, espacios simples.
# Editá este diccionario a medida que aparezcan variantes nuevas.
CORRECCIONES = {
    "enfermedad de huntingtong": "Enfermedad de Huntington",
    "rothmund tomsom": "Síndrome de Rothmund-Thomson",
    "distrofia miotonica steiner": "Distrofia miotónica de Steinert",
    "microdelecion 22q11.2": "Síndrome de deleción 22q11.2",
    "sindrome de delecion 22q11.2": "Síndrome de deleción 22q11.2",
    "peutz jeghers": "Síndrome de Peutz-Jeghers",
    "sindrome microduplicacion15q11q13": "Síndrome de microduplicación 15q11q13",
    "tetrasomia12p": "Tetrasomía 12p",
    "anemia fanconi": "Anemia de Fanconi",
}

GRUPOS_EDAD = {
    "bins": [-1, 1, 5, 12, 17, 39, 59, 200],
    "labels": ["0–1", "2–5", "6–12", "13–17", "18–39", "40–59", "60+"],
}


# ---------------------------------------------------------------- utilidades
def clave(texto) -> str:
    """Texto normalizado para comparar: sin tildes, minúsculas, espacios simples."""
    t = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", t).strip().lower()


def normalizar_diagnosticos(serie: pd.Series) -> pd.Series:
    originales = serie.fillna("").astype(str).str.strip()
    claves = originales.map(clave)
    claves = claves.map(lambda k: clave(CORRECCIONES[k]) if k in CORRECCIONES else k)
    fijas = {clave(v): v for v in CORRECCIONES.values()}

    etiquetas = {}
    for k, grupo in originales.groupby(claves):
        if not k:
            continue
        if k in fijas:
            etiquetas[k] = fijas[k]
        else:  # la variante más usada, con mayúscula inicial
            v = grupo.value_counts().index[0]
            etiquetas[k] = v[0].upper() + v[1:]
    return claves.map(etiquetas).fillna("Sin dato")


# ---------------------------------------------------------------- datos
def usar_sheets() -> bool:
    return "sheet_id" in st.secrets


def ruta_csv() -> Path:
    return Path(st.secrets.get("csv_path", CSV_POR_DEFECTO))


def seleccionar_columnas(df: pd.DataFrame) -> pd.DataFrame:
    # Acepta encabezados con o sin tildes ("Diagnóstico", "Médica"…)
    df.columns = [str(c).strip() for c in df.columns]
    mapa = {clave(c): c for c in df.columns}
    faltan = [c for c in COLUMNAS if clave(c) not in mapa]
    if faltan:
        raise ValueError(f"Faltan columnas: {', '.join(faltan)}")
    df = df[[mapa[clave(c)] for c in COLUMNAS]]
    df.columns = COLUMNAS
    return df


def leer_csv(datos: bytes) -> pd.DataFrame:
    """Lee un CSV con coma o punto y coma (Excel en español) y UTF-8 o Latin-1."""
    for codificacion in ("utf-8-sig", "latin-1"):
        try:
            texto = datos.decode(codificacion)
            break
        except UnicodeDecodeError:
            continue
    primera = texto.splitlines()[0] if texto else ""
    sep = ";" if primera.count(";") > primera.count(",") else ","
    df = pd.read_csv(io.StringIO(texto), sep=sep, dtype=str, keep_default_na=False)
    return seleccionar_columnas(df)


@st.cache_data(show_spinner="Leyendo CSV…")
def cargar_csv(ruta: str, modificado: float) -> pd.DataFrame:
    # "modificado" (fecha del archivo) entra en la clave de la caché:
    # si editás el CSV, se relee solo. (Ojo: un parámetro con _ adelante NO contaría.)
    with open(ruta, "rb") as fh:
        return leer_csv(fh.read())


@st.cache_data(ttl=600, show_spinner="Leyendo Google Sheet…")
def cargar_sheets() -> pd.DataFrame:
    import gspread  # solo hace falta si se usa Google Sheets

    gc = gspread.service_account_from_dict(dict(st.secrets["gcp_service_account"]))
    sh = gc.open_by_key(st.secrets["sheet_id"])
    ws = sh.worksheet(st.secrets["worksheet"]) if "worksheet" in st.secrets else sh.sheet1
    filas = ws.get_all_values()
    return seleccionar_columnas(pd.DataFrame(filas[1:], columns=filas[0]))


def cargar() -> tuple[pd.DataFrame, str]:
    if usar_sheets():
        return cargar_sheets(), "Google Sheets · se actualiza cada 10 min"

    ruta = ruta_csv()
    if ruta.exists():
        return cargar_csv(str(ruta), ruta.stat().st_mtime), f"CSV local ({ruta.name})"

    # Sin Sheet ni CSV local (ej. en el deploy): se sube el archivo desde la app.
    # No se cachea ni se guarda en disco: vive solo en la memoria de esta sesión.
    archivo = st.sidebar.file_uploader("📄 CSV de pacientes", type=["csv"], key="csv_subido")
    if archivo is None:
        st.title("🧬 Estadísticas de pacientes")
        st.info(
            "Subí el CSV de pacientes desde la barra lateral para ver las estadísticas.\n\n"
            "Columnas necesarias: **ID, Edad, Diagnostico, Medica, SISA**. "
            "El archivo no se guarda: queda solo mientras tengas esta pestaña abierta."
        )
        st.stop()
    return leer_csv(archivo.getvalue()), f"CSV subido ({archivo.name})"


def limpiar(df: pd.DataFrame) -> pd.DataFrame:
    df = df.replace("", pd.NA).dropna(how="all").copy()
    df["ID"] = df["ID"].astype("string").str.strip()
    df["Edad"] = pd.to_numeric(df["Edad"], errors="coerce")
    df["Diagnostico"] = normalizar_diagnosticos(df["Diagnostico"])
    df["Medica"] = df["Medica"].astype("string").str.strip().str.title().fillna("Sin dato")
    df["SISA"] = (
        df["SISA"].astype("string").str.strip().str.upper()
        .map({"SI": "Sí", "SÍ": "Sí", "NO": "No"})
        .fillna("Sin dato")
    )
    df["Grupo etario"] = pd.cut(df["Edad"], **GRUPOS_EDAD)
    df["Población"] = "Sin dato"
    df.loc[df["Edad"] < 18, "Población"] = "Pediátrica"
    df.loc[df["Edad"] >= 18, "Población"] = "Adulta"
    return df


# ---------------------------------------------------------------- acceso
def autenticado() -> bool:
    if st.session_state.get("ok"):
        return True
    st.title("🧬 Estadísticas Genética")
    pw = st.text_input("Contraseña", type="password")
    if pw:
        if hmac.compare_digest(pw, st.secrets["app_password"]):
            st.session_state["ok"] = True
            st.rerun()
        st.error("Contraseña incorrecta")
    return False


if not autenticado():
    st.stop()

try:
    datos, fuente = cargar()
    df = limpiar(datos)
except Exception as e:  # noqa: BLE001
    st.error(f"No pude leer los datos: {e}")
    st.stop()

# ---------------------------------------------------------------- filtros
with st.sidebar:
    st.header("Filtros")
    if st.button("🔄 Actualizar datos", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    if st.button("🔒 Cerrar sesión", width="stretch"):
        st.session_state.clear()
        st.rerun()

    medicas_all = sorted(df["Medica"].unique())
    medicas = st.multiselect("Médica", medicas_all, default=medicas_all)
    sisa = st.multiselect("Cargado al SISA", ["Sí", "No", "Sin dato"], default=["Sí", "No", "Sin dato"])
    poblacion = st.radio("Población", ["Todas", "Pediátrica", "Adulta"], horizontal=True)
    edad_max = int(df["Edad"].max()) if df["Edad"].notna().any() else 100
    rango = st.slider("Edad", 0, edad_max, (0, edad_max))
    top_n = st.slider("Diagnósticos a mostrar", 5, 30, 15)

rango_completo = rango == (0, edad_max)
mask = (
    df["Medica"].isin(medicas)
    & df["SISA"].isin(sisa)
    & (df["Edad"].between(*rango) | (df["Edad"].isna() & rango_completo))
)
if poblacion != "Todas":
    mask &= df["Población"] == poblacion
f = df[mask]

# ---------------------------------------------------------------- KPIs
st.title("🧬 Estadísticas de pacientes")
st.caption(f"{len(f)} de {len(df)} registros con los filtros actuales · Fuente: {fuente}")

if f.empty:
    st.info("No hay registros con esos filtros.")
    st.stop()

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Pacientes", len(f))
k2.metric("Diagnósticos distintos", f.loc[f["Diagnostico"] != "Sin dato", "Diagnostico"].nunique())
k3.metric("Cargados al SISA", f"{(f['SISA'] == 'Sí').mean():.0%}")
k4.metric("Edad mediana", f"{f['Edad'].median():.0f}" if f["Edad"].notna().any() else "–")
k5.metric("Pediátricos", f"{(f['Población'] == 'Pediátrica').mean():.0%}")

# ---------------------------------------------------------------- gráficos
st.subheader("Diagnósticos más frecuentes")
vc = f["Diagnostico"].value_counts().head(top_n).sort_values()
fig = px.bar(
    x=vc.values, y=vc.index, orientation="h", text=vc.values,
    labels={"x": "Pacientes", "y": ""},
)
fig.update_traces(textposition="outside", textangle=0, cliponaxis=False)
fig.update_layout(height=max(350, 28 * len(vc)), margin=dict(l=10, r=10, t=10, b=10))
st.plotly_chart(fig, width="stretch")

c1, c2 = st.columns(2)
with c1:
    st.subheader("Grupos etarios")
    g = f.groupby(["Grupo etario", "Medica"], observed=False).size().reset_index(name="Pacientes")
    fig = px.bar(g, x="Grupo etario", y="Pacientes", color="Medica", barmode="stack")
    fig.update_layout(margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, width="stretch")
with c2:
    st.subheader("Carga al SISA por médica")
    g = f.groupby(["Medica", "SISA"]).size().reset_index(name="Pacientes")
    fig = px.bar(
        g, x="Medica", y="Pacientes", color="SISA", barmode="stack",
        category_orders={"SISA": ["Sí", "No", "Sin dato"]},
    )
    fig.update_layout(margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, width="stretch")

# ---------------------------------------------------------------- tablas
st.subheader("Resumen por diagnóstico")
resumen = (
    f.groupby("Diagnostico")
    .agg(
        Pacientes=("ID", "size"),
        Edad_mediana=("Edad", "median"),
        Edad_min=("Edad", "min"),
        Edad_max=("Edad", "max"),
        Pct_SISA=("SISA", lambda s: (s == "Sí").mean() * 100),
        Medicas=("Medica", lambda s: ", ".join(sorted(s.unique()))),
    )
    .sort_values("Pacientes", ascending=False)
    .reset_index()
)
st.dataframe(
    resumen,
    hide_index=True,
    width="stretch",
    column_config={
        "Edad_mediana": st.column_config.NumberColumn("Edad mediana", format="%.0f"),
        "Edad_min": st.column_config.NumberColumn("Edad mín.", format="%.0f"),
        "Edad_max": st.column_config.NumberColumn("Edad máx.", format="%.0f"),
        "Pct_SISA": st.column_config.ProgressColumn("% SISA", format="%.0f%%", min_value=0, max_value=100),
        "Medicas": "Médica(s)",
    },
)
st.download_button(
    "⬇️ Descargar resumen (CSV)",
    resumen.to_csv(index=False).encode("utf-8"),
    "resumen_diagnosticos.csv",
    "text/csv",
)

with st.expander(f"Pendientes de carga al SISA ({(f['SISA'] != 'Sí').sum()})"):
    st.dataframe(
        f.loc[f["SISA"] != "Sí", COLUMNAS].sort_values(["Medica", "Diagnostico"]),
        hide_index=True, width="stretch",
    )

with st.expander("Calidad de datos"):
    sin_id = df["ID"].isna().sum()
    dup = df["ID"].dropna().duplicated(keep=False)
    q1, q2, q3, q4 = st.columns(4)
    q1.metric("Sin ID", sin_id)
    q2.metric("IDs duplicados", int(dup.sum()))
    q3.metric("Sin edad", int(df["Edad"].isna().sum()))
    q4.metric("SISA sin dato", int((df["SISA"] == "Sin dato").sum()))
    if dup.any():
        st.write("Registros con ID repetido:")
        st.dataframe(df[df["ID"].isin(df["ID"].dropna()[dup])][COLUMNAS], hide_index=True)