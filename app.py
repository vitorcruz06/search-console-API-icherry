from concurrent.futures import ThreadPoolExecutor
import datetime
import io
import json
import os
import secrets
from urllib.parse import quote_plus
import pandas as pd
import plotly.express as px
import streamlit as st
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

# Configuração da página Streamlit
st.set_page_config(
    page_title="Google Search Console API | i-Cherry",
    page_icon="🍒",
    layout="wide",
)

os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

# URL FIXA DA SUA APLICAÇÃO NA NUVEM
REDIRECT_URI = "https://search-console-icherry.streamlit.app/"

# --- APLICAÇÃO DA PALETA DE CORES I-CHERRY ---
NAVY = "#000050"
CORNFLOWER = "#5465FF"
PANTONE_629 = "#93DFE3"
PERIWINKLE = "#788BFF"
LIME_GREEN = "#B0F467"

st.markdown(
    f"""
    <style>
    /* Estilização Customizada i-Cherry */
    h1, h2, h3, .stAppHeader {{
        color: {NAVY} !important;
    }}
    
    div.stButton > button {{
        background-color: {NAVY} !important;
        color: #FFFFFF !important;
        border-radius: 8px !important;
        font-weight: bold !important;
        border: none !important;
        padding: 0.5rem 1rem !important;
        transition: all 0.3s ease !important;
    }}
    
    div.stButton > button:hover {{
        background-color: {CORNFLOWER} !important;
        color: #FFFFFF !important;
        box-shadow: 0 4px 12px rgba(0,0,80,0.2) !important;
    }}

    [data-testid="stMetricValue"] {{
        color: {NAVY} !important;
        font-weight: bold !important;
    }}
    [data-testid="stMetricLabel"] {{
        color: #4A4A4A !important;
        font-weight: 500 !important;
    }}
    
    a {{
        color: {NAVY} !important;
        text-decoration: none !important;
    }}
    a:hover {{
        color: {CORNFLOWER} !important;
    }}

    div[role="radiogroup"] label[data-baseweb="radio"] div:first-child {{
        background-color: {NAVY} !important;
    }}
    </style>
""",
    unsafe_allow_html=True,
)

OPERATORS_MAP = {
    "Contém": "CONTAINS",
    "Exatamente igual": "EQUALS",
    "Não contém": "NOT_CONTAINS",
    "Regex (Incluir)": "INCLUDING_REGEX",
    "Regex (Excluir)": "EXCLUDING_REGEX",
}

SEARCH_TYPES_MAP = {
    "web": "WEB",
    "discover": "DISCOVER",
    "image": "IMAGE",
    "news": "NEWS",
    "video": "VIDEO",
    "googleNews": "GOOGLE_NEWS",
}

# --- CARREGAMENTO DE CREDENCIAIS ---
def get_client_config():
    """Lê do Secrets do Streamlit Cloud ou de arquivo local."""
    if "client_secret_json" in st.secrets:
        return json.loads(st.secrets["client_secret_json"])
    elif "client_secret" in st.secrets:
        return json.loads(st.secrets["client_secret"])
    elif os.path.exists("client_secret.json"):
        with open("client_secret.json", "r") as f:
            return json.load(f)
    else:
        st.error(
            "Credenciais do Google não encontradas. Configure 'client_secret_json' nos Secrets do Streamlit Cloud."
        )
        st.stop()

def get_auth_flow():
    client_config = get_client_config()
    return Flow.from_client_config(
        client_config, scopes=SCOPES, redirect_uri=REDIRECT_URI
    )

def login_user():
    flow = get_auth_flow()
    code_verifier = secrets.token_urlsafe(32)
    flow.code_verifier = code_verifier
    auth_url, _ = flow.authorization_url(prompt="consent", state=code_verifier)

    st.sidebar.markdown(
        f'<a href="{auth_url}" target="_self" style="text-decoration: none; font-weight: bold; color: {NAVY}; font-size: 16px;">🔗 Fazer Login com o Google</a>',
        unsafe_allow_html=True,
    )

def callback_auth():
    query_params = st.query_params
    if "code" in query_params and "credentials" not in st.session_state:
        try:
            flow = get_auth_flow()
            if "state" in query_params:
                flow.code_verifier = query_params["state"]

            flow.fetch_token(code=query_params["code"])
            creds = flow.credentials

            st.session_state["credentials"] = {
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": creds.scopes,
            }
            st.query_params.clear()
            st.rerun()
        except Exception as e:
            st.error(f"Erro ao autenticar: {e}")
            st.query_params.clear()

callback_auth()

if "credentials" not in st.session_state:
    if os.path.exists("Logo_Azul_Horizontal.png"):
        st.image("Logo_Azul_Horizontal.png", width=250)
    st.title("Google Search Console API")
    st.info(
        "Por favor, faça login com sua conta do Google na barra lateral para acessar seus sites do Search Console."
    )
    login_user()
    st.stop()

# --- CONSULTAS API COM CACHE ---
@st.cache_data(ttl=600)
def list_user_sites(creds_data):
    """Lista domínios da conta logada."""
    try:
        creds = Credentials(**creds_data)
        sess = AuthorizedSession(creds)
        resp = sess.get(
            "https://www.googleapis.com/webmasters/v3/sites", timeout=12
        )
        if resp.status_code != 200:
            st.sidebar.error(f"Erro ao carregar sites: {resp.text}")
            return []
        sites = resp.json().get("siteEntry", [])
        return [
            s["siteUrl"]
            for s in sites
            if s.get("permissionLevel") != "siteUnverifiedUser"
        ]
    except Exception as e:
        st.sidebar.error(f"Falha na listagem de domínios: {e}")
        return []

@st.cache_data(ttl=600)
def get_gsc_data(
    creds_data,
    site_url,
    start_date,
    end_date,
    dimensions,
    search_type="web",
    filters_tuple=None,
    row_limit=1000,
):
    """Busca métricas no Search Console."""
    try:
        creds = Credentials(**creds_data)
        sess = AuthorizedSession(creds)
        encoded_site = quote_plus(site_url)
        endpoint = f"https://www.googleapis.com/webmasters/v3/sites/{encoded_site}/searchAnalytics/query"

        api_search_type = SEARCH_TYPES_MAP.get(search_type, "WEB")

        request_body = {
            "startDate": str(start_date),
            "endDate": str(end_date),
            "dimensions": list(dimensions),
            "type": api_search_type,
            "rowLimit": row_limit,
            "dataState": "all",
        }

        if filters_tuple:
            filter_list = [
                {"dimension": dim, "operator": op, "expression": expr}
                for dim, op, expr in filters_tuple
            ]
            if filter_list:
                request_body["dimensionFilterGroups"] = [
                    {"filters": filter_list}
                ]

        resp = sess.post(endpoint, json=request_body, timeout=15)

        if resp.status_code != 200:
            st.error(f"Erro na API do Google ({resp.status_code}): {resp.text}")
            return pd.DataFrame()

        rows = resp.json().get("rows", [])
        if not rows:
            return pd.DataFrame()

        data = []
        for row in rows:
            item = {}
            for idx, dim in enumerate(dimensions):
                item[dim] = row["keys"][idx]
            item["clicks"] = row["clicks"]
            item["impressions"] = row["impressions"]
            item["ctr"] = row["ctr"]
            item["position"] = row.get("position", 0.0)
            data.append(item)

        return pd.DataFrame(data)
    except Exception as e:
        st.error(f"Exceção ao buscar dados: {e}")
        return pd.DataFrame()

# --- INTERFACE SIDEBAR ---
with st.sidebar:
    if os.path.exists("Logo_Azul_Horizontal.png"):
        st.image("Logo_Azul_Horizontal.png", use_container_width=True)
    else:
        st.title("i-Cherry")

    st.subheader("Search Console API")

    if st.button("Logout"):
        st.session_state.clear()
        st.rerun()

    with st.spinner("Carregando domínios..."):
        sites = list_user_sites(st.session_state["credentials"])

    if not sites:
        st.warning("Nenhum domínio verificado encontrado nesta conta.")
        st.stop()

    selected_site = st.selectbox("Domínio:", sites)

    search_type = st.selectbox(
        "Tipo de Busca:",
        options=["web", "discover", "image", "news", "video", "googleNews"],
        format_func=lambda x: {
            "web": "🌐 Web (Padrão)",
            "discover": "📰 Discover",
            "image": "🖼️ Imagens",
            "news": "📰 Notícias",
            "video": "🎥 Vídeos",
            "googleNews": "📱 Google Notícias",
        }[x],
    )

    has_query_support = search_type not in ["discover", "googleNews"]

    if has_query_support:
        metric_mode = st.selectbox(
            "Métricas:",
            ["Palavras-Chave", "Páginas"],
            help="Agrupar por palavra-chave ou URL",
        )
        dimension = "query" if metric_mode == "Palavras-Chave" else "page"
    else:
        st.info("ℹ️ Discover não fornece consultas de palavras-chave.")
        dimension = "page"

    st.markdown("---")
    st.subheader("Filtros")

    active_filters = []

    use_url_filter = st.checkbox("Filtrar por URL", value=False)
    if use_url_filter:
        url_op_label = st.selectbox(
            "Operador URL", list(OPERATORS_MAP.keys()), key="url_op"
        )
        url_val = st.text_input(
            "Filtro URL",
            value="",
            placeholder="Ex: /blog/ ou .*categoria.*",
            key="url_val",
        )
        if url_val:
            active_filters.append(
                ("page", OPERATORS_MAP[url_op_label], url_val)
            )

    if has_query_support:
        use_query_filter = st.checkbox(
            "Filtrar por Palavra-Chave", value=False
        )
        if use_query_filter:
            query_op_label = st.selectbox(
                "Operador Consulta", list(OPERATORS_MAP.keys()), key="query_op"
            )
            query_val = st.text_input(
                "Filtro Palavra-Chave",
                value="",
                placeholder="Ex: hb20, comprar.*",
                key="query_val",
            )
            if query_val:
                active_filters.append(
                    ("query", OPERATORS_MAP[query_op_label], query_val)
                )

    st.markdown("---")

    today = datetime.date.today()
    default_end_date = today - datetime.timedelta(days=2)
    default_start_date = default_end_date - datetime.timedelta(days=30)

    dates = st.date_input(
        "Período:",
        value=(default_start_date, default_end_date),
    )

    btn_buscar = st.button("Buscar Dados ✨", type="primary")

# --- CONTEÚDO PRINCIPAL ---
st.title("Google Search Console API")

should_fetch = btn_buscar or "df_dates" not in st.session_state

if should_fetch:
    if isinstance(dates, tuple) and len(dates) == 2:
        start_d, end_d = dates[0], dates[1]
    else:
        start_d = dates[0] if isinstance(dates, tuple) else dates
        end_d = start_d

    filters_tuple = tuple(active_filters) if active_filters else None

    with st.spinner("Buscando dados no Google (Gráfico + Tabela)..."):
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_dates = executor.submit(
                get_gsc_data,
                st.session_state["credentials"],
                selected_site,
                start_d,
                end_d,
                ("date",),
                search_type,
                filters_tuple,
                100,
            )

            future_dimensions = executor.submit(
                get_gsc_data,
                st.session_state["credentials"],
                selected_site,
                start_d,
                end_d,
                (dimension,),
                search_type,
                filters_tuple,
                1000,
            )

            st.session_state["df_dates"] = future_dates.result()
            st.session_state["df_dimensions"] = future_dimensions.result()
            st.session_state["current_dimension"] = dimension
            st.session_state["current_site"] = selected_site

df_dates = st.session_state.get("df_dates", pd.DataFrame())
df_dimensions = st.session_state.get("df_dimensions", pd.DataFrame())
current_dim = st.session_state.get("current_dimension", dimension)
current_site = st.session_state.get("current_site", selected_site)

tab_mode = st.radio("Modo de Visão:", ["📅 Data", "📊 Tabela"], horizontal=True)

if df_dates.empty and df_dimensions.empty:
    st.warning(
        "Nenhum dado encontrado para esse domínio/filtro neste período."
    )
else:
    total_clicks = df_dates["clicks"].sum() if not df_dates.empty else 0
    total_impressions = (
        df_dates["impressions"].sum() if not df_dates.empty else 0
    )
    avg_ctr = (
        (total_clicks / total_impressions * 100) if total_impressions > 0 else 0
    )
    avg_pos = df_dates["position"].mean() if not df_dates.empty else 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Cliques", f"{total_clicks:,.0f}".replace(",", "."))
    col2.metric(
        "Impressões", f"{total_impressions:,.0f}".replace(",", ".")
    )
    col3.metric("CTR", f"{avg_ctr:.2f}%")
    col4.metric("Pos. Média", f"{avg_pos:.1f}")

    st.markdown("---")

    if "Data" in tab_mode:
        if not df_dates.empty:
            df_dates_sorted = df_dates.sort_values("date")
            fig = px.line(
                df_dates_sorted,
                x="date",
                y="clicks",
                title="Cliques ao longo do tempo",
                labels={"date": "Data", "clicks": "Cliques"},
                line_shape="linear",
            )
            fig.update_traces(line_color=NAVY, line_width=3)
            fig.update_layout(
                hovermode="x unified", template="plotly_white"
            )
            st.plotly_chart(fig, use_container_width=True)

    else:
        if not df_dimensions.empty:
            df_table = df_dimensions.copy()
            df_table["ctr"] = (df_table["ctr"] * 100).map("{:.2f}%".format)
            df_table["position"] = df_table["position"].round(2)

            col_name = (
                "Palavra-Chave" if current_dim == "query" else "Página (URL)"
            )
            df_table.rename(
                columns={
                    current_dim: col_name,
                    "clicks": "Cliques",
                    "impressions": "Impressões",
                    "ctr": "CTR",
                    "position": "Posição",
                },
                inplace=False,
            )

            st.dataframe(
                df_table, use_container_width=True, hide_index=False
            )

            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                df_table.to_excel(
                    writer, index=False, sheet_name="GSC Data"
                )

            st.download_button(
                label="📥 Download Excel",
                data=buffer.getvalue(),
                file_name=f'gsc_data_{current_site.replace("://", "_")}.xlsx',
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
