import os
from pathlib import Path

os.environ['STREAMLIT_TELEMETRY_DISABLED'] = 'true'

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    env_path = Path(__file__).resolve().parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)

import streamlit as st
import pandas as pd
import psycopg2
import plotly.express as px
import plotly.graph_objects as go   # <-- add this line
import folium
from streamlit_folium import st_folium
import numpy as np
from math import radians, sin, cos, sqrt, atan2

DB_CONFIG = {
    'host': os.getenv('PGHOST', 'localhost'),
    'port': int(os.getenv('PGPORT', '5432')),
    'database': os.getenv('PGDATABASE', 'semiconductor'),
    'user': os.getenv('PGUSER', ''),
    'password': os.getenv('PGPASSWORD', ''),
}

# ---------- Database Connection ----------
@st.cache_resource
def get_connection():
    missing = [k for k, v in DB_CONFIG.items() if (k != 'password' and not v)]
    if missing:
        raise RuntimeError(f"Database environment not configured: missing {', '.join(missing)}")
    return psycopg2.connect(**DB_CONFIG)

conn = get_connection()

# ---------- Helper Functions ----------
def run_query(query):
    return pd.read_sql(query, conn)

# ---------- Page Config ----------
st.set_page_config(page_title="Intel Logistics Dashboard", layout="wide")
st.title("🏭 Intel Semiconductor Logistics Dashboard")
st.markdown("Real‑time view of front‑end fabs, back‑end assembly, and wafer movements (22nm/14nm/10nm).")

# ---------- Sidebar Filters ----------
st.sidebar.header("Filters")
tech_filter = st.sidebar.multiselect(
    "Technology Node",
    options=run_query("SELECT DISTINCT tech_id FROM technology")['tech_id'].tolist(),
    default=[]
)
fab_filter = st.sidebar.multiselect(
    "Fab",
    options=run_query("SELECT fab_id FROM fab WHERE site_type='Front-End'")['fab_id'].tolist(),
    default=[]
)

# Build WHERE clause for production lots
where_clauses = []
if tech_filter:
    where_clauses.append(f"tech_id IN ({','.join([f"'{t}'" for t in tech_filter])})")
if fab_filter:
    where_clauses.append(f"fab_id IN ({','.join([f"'{f}'" for f in fab_filter])})")
where_sql = " AND ".join(where_clauses)
if where_sql:
    where_sql = "WHERE " + where_sql

# ---------- Key Metrics ----------
col1, col2, col3, col4 = st.columns(4)

with col1:
    total_fabs = run_query("SELECT COUNT(*) FROM fab WHERE site_type='Front-End'").iloc[0,0]
    st.metric("Front‑End Fabs", total_fabs)

with col2:
    total_lots = run_query(f"SELECT COUNT(*) FROM production_lot {where_sql}").iloc[0,0] if where_sql else run_query("SELECT COUNT(*) FROM production_lot").iloc[0,0]
    st.metric("Production Lots", total_lots)

with col3:
    total_wafers = run_query(f"SELECT SUM(wafers_started) FROM production_lot {where_sql}").iloc[0,0] if where_sql else run_query("SELECT SUM(wafers_started) FROM production_lot").iloc[0,0]
    st.metric("Wafers Started", f"{total_wafers:,.0f}" if total_wafers else "N/A")

with col4:
    avg_yield = run_query(f"SELECT AVG(yield_pct) FROM production_lot WHERE yield_pct IS NOT NULL {where_sql.replace('WHERE','AND') if where_sql else ''}").iloc[0,0]
    st.metric("Avg Yield", f"{avg_yield:.1f}%" if avg_yield else "N/A")

st.divider()

# ---------- Map of Facilities ----------
st.subheader("🗺️ Facility Locations")

# Approximate coordinates (for demo)
location_coords = {
    # Front-end fabs
    'AZ_F12': (33.3, -111.8), 'AZ_F22': (33.3, -111.8), 'AZ_F32': (33.3, -111.8),
    'IE_F24': (53.3, -6.5), 'IL_F28': (31.6, 34.8), 'IL_F28a': (31.6, 34.8),
    'IL_F38': (31.6, 34.8), 'OR_D1x': (45.5, -122.9),
    # Back-end sites
    'CN_SH': (31.2, 121.5), 'CN_CD': (30.6, 104.1), 'CR_SJ': (9.9, -84.1),
    'MY_KUL': (5.1, 100.4), 'MY_PG': (5.4, 100.3), 'VN_HCM': (10.8, 106.7),
    # US assembly/test (fictional point)
    'US_AT': (35.1, -106.6)  # Albuquerque area
}

df_fabs = run_query("SELECT fab_id, name, location, site_type FROM fab")
df_fabs['lat'] = df_fabs['fab_id'].map(lambda x: location_coords.get(x, (0,0))[0])
df_fabs['lon'] = df_fabs['fab_id'].map(lambda x: location_coords.get(x, (0,0))[1])

m = folium.Map(location=[20, 0], zoom_start=2)
for _, row in df_fabs.iterrows():
    color = 'blue' if row['site_type'] == 'Front-End' else 'green'
    folium.Marker(
        location=[row['lat'], row['lon']],
        popup=f"{row['name']} ({row['fab_id']})",
        tooltip=row['fab_id'],
        icon=folium.Icon(color=color)
    ).add_to(m)

st_folium(m, width=1000, height=500)

st.divider()

# ---------- Production Lots Table ----------
st.subheader("📋 Production Lots")
lots_df = run_query(f"SELECT * FROM production_lot {where_sql} ORDER BY start_date DESC")
st.dataframe(lots_df, use_container_width=True)

st.divider()

# ---------- Yield Analysis ----------
st.subheader("📊 Yield by Fab and Technology")
yield_df = run_query("""
    SELECT fab_id, tech_id, AVG(yield_pct) as avg_yield, COUNT(*) as lot_count
    FROM production_lot
    WHERE yield_pct IS NOT NULL
    GROUP BY fab_id, tech_id
""")
if not yield_df.empty:
    fig = px.bar(yield_df, x='fab_id', y='avg_yield', color='tech_id',
                 hover_data=['lot_count'], barmode='group',
                 title="Average Yield by Fab and Technology")
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("No yield data available.")

st.divider()

# ---------- Process Flow (Sankey) ----------
st.subheader("🔄 Wafer Flow from Front‑End to Back‑End")

# Build a mock flow based on production lots and a simple distribution rule
# For demo: lots from US fabs go to Costa Rica/Malaysia; Israel to Vietnam; Ireland to US/Asia
lot_flow = run_query("""
    SELECT fab_id, COUNT(*) as lot_count
    FROM production_lot
    GROUP BY fab_id
""")

# Mapping fab to assembly site probabilities (simplified)
fab_to_asm = {
    'AZ_F12': ['CR_SJ', 'MY_KUL', 'MY_PG'],
    'AZ_F22': ['CR_SJ', 'MY_KUL', 'MY_PG'],
    'AZ_F32': ['CR_SJ', 'MY_KUL', 'MY_PG'],
    'IE_F24': ['US_AT', 'CN_SH', 'MY_PG'],
    'IL_F28': ['VN_HCM', 'CN_CD'],
    'IL_F28a': ['VN_HCM', 'CN_CD'],
    'IL_F38': ['VN_HCM', 'CN_CD'],
    'OR_D1x': ['US_AT', 'MY_KUL']
}

# Create Sankey links
sankey_df = pd.DataFrame(columns=['source', 'target', 'value'])
for _, row in lot_flow.iterrows():
    fab = row['fab_id']
    count = row['lot_count']
    if fab in fab_to_asm:
        targets = fab_to_asm[fab]
        # distribute equally
        per_target = count / len(targets)
        for t in targets:
            sankey_df = pd.concat([sankey_df, pd.DataFrame([{'source': fab, 'target': t, 'value': per_target}])], ignore_index=True)

if not sankey_df.empty:
    # Get node labels from both fab and assembly tables
    all_nodes = pd.concat([df_fabs[['fab_id']].rename(columns={'fab_id':'node'}),
                           pd.DataFrame({'node': ['US_AT']})])  # add US_AT manually
    node_list = all_nodes['node'].tolist()
    node_indices = {node: i for i, node in enumerate(node_list)}

    sankey_df['source_idx'] = sankey_df['source'].map(node_indices)
    sankey_df['target_idx'] = sankey_df['target'].map(node_indices)

    fig = px.bar(sankey_df, x='source', y='value', color='target', title="Lot Distribution to Assembly Sites")
    st.plotly_chart(fig, use_container_width=True)

    # Alternatively a Sankey diagram:
    fig = go.Figure(data=[go.Sankey(
        node=dict(
            pad=15,
            thickness=20,
            line=dict(color="black", width=0.5),
            label=node_list,
            color="blue"
        ),
        link=dict(
            source=sankey_df['source_idx'],
            target=sankey_df['target_idx'],
            value=sankey_df['value']
        ))])
    fig.update_layout(title_text="Wafer Flow Sankey Diagram", font_size=10)
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Not enough data to build flow diagram.")

st.divider()

# ---------- Active Constraints ----------
st.subheader("⚠️ Operational Constraints")
constraints_df = run_query("SELECT * FROM operational_constraint")
st.dataframe(constraints_df, use_container_width=True)

st.divider()

# ---------- Optimized Routes (Simple Distance Heuristic) ----------
st.subheader("🚚 Optimized Route Suggestions")
st.markdown("Based on approximate coordinates, here are the shortest distances from each fab to possible assembly sites.")

# Calculate distances (Haversine formula)
def haversine(lat1, lon1, lat2, lon2):
    R = 6371  # Earth radius in km
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    return R * c

# Create a dataframe of distances
routes = []
for _, fab_row in df_fabs[df_fabs['site_type']=='Front-End'].iterrows():
    for _, asm_row in df_fabs[df_fabs['site_type']=='Back-End'].iterrows():
        dist = haversine(fab_row['lat'], fab_row['lon'], asm_row['lat'], asm_row['lon'])
        routes.append({
            'Fab': fab_row['fab_id'],
            'Assembly': asm_row['fab_id'],
            'Distance_km': round(dist, 1)
        })

routes_df = pd.DataFrame(routes)
# For each fab, show top 3 closest assembly sites
top_routes = routes_df.sort_values(['Fab', 'Distance_km']).groupby('Fab').head(3).reset_index(drop=True)
st.dataframe(top_routes, use_container_width=True)

st.caption("Note: This is a simplified demo; actual logistics consider cost, capacity, and export controls.")