import streamlit as st
import pydeck as pdk
from constants import LOCATION_COORDS, FAB_TO_ASM

def render_global_route_map(fabs_df, lots_df=None):
    """
    Renders the PyDeck arc map for fab-to-assembly routes with integrated risk alerts.
    Single Responsibility: Visualization only.
    """
    
    # 1. Identify "At Risk" Fabs based on yield or status
    at_risk_fabs = []
    if lots_df is not None:
        at_risk_fabs = lots_df[
            (lots_df['yield_pct'] < 85) | (lots_df['status'] == 'On Hold')
        ]['fab_id'].unique().tolist()

    # 2. Build fab marker points with risk metadata
    fab_points = []
    for fab_id, coords in LOCATION_COORDS.items():
        site_type = fabs_df[fabs_df['fab_id'] == fab_id]['site_type'].iloc[0] \
                    if fab_id in fabs_df['fab_id'].values else 'Unknown'
        
        fab_points.append({
            'fab_id': fab_id,
            'lat': coords[0],
            'lon': coords[1],
            'site_type': site_type,
            'is_at_risk': fab_id in at_risk_fabs,
            'risk_label': "⚠️ HIGH RISK" if fab_id in at_risk_fabs else "✅ NORMAL"
        })

    # 3. Build arc routes
    arcs = []
    for source, targets in FAB_TO_ASM.items():
        if source not in LOCATION_COORDS: continue
        s_lat, s_lon = LOCATION_COORDS[source]
        for target in targets:
            if target not in LOCATION_COORDS: continue
            t_lat, t_lon = LOCATION_COORDS[target]
            arcs.append({
                'source_lat': s_lat, 'source_lon': s_lon,
                'target_lat': t_lat, 'target_lon': t_lon,
                'source': source, 'target': target,
            })

    # --- DEFINE LAYERS ---

    # Layer 1: The "Pulse" for Risk (Red glow under markers)
    risk_layer = pdk.Layer(
        'ScatterplotLayer',
        data=[p for p in fab_points if p['is_at_risk']],
        get_position='[lon, lat]',
        get_fill_color=[255, 0, 0, 140], # Red
        get_radius=200000,              # Large radius for visibility
        pickable=False,
    )

    # Layer 2: Arcs (Routes)
    arc_layer = pdk.Layer(
        'ArcLayer',
        data=arcs,
        get_source_position='[source_lon, source_lat]',
        get_target_position='[target_lon, target_lat]',
        get_source_color=[0, 180, 180, 160], # Teal
        get_target_color=[0, 128, 255, 160], # Blue
        get_width=2,
        pickable=True,
    )

    # Layer 3: Fabs (Nodes)
    scatter_layer = pdk.Layer(
        'ScatterplotLayer',
        data=fab_points,
        get_position='[lon, lat]',
        # Orange for Front-End, Strong Blue for others
        get_fill_color='[site_type == "Front-End" ? 255 : 0, site_type == "Front-End" ? 140 : 128, site_type == "Front-End" ? 0 : 255, 200]',
        get_radius=80000,
        pickable=True,
    )

    # 4. Render
    view = pdk.ViewState(latitude=30, longitude=10, zoom=1.2, pitch=30)

    st.pydeck_chart(pdk.Deck(
        layers=[risk_layer, arc_layer, scatter_layer],
        initial_view_state=view,
        tooltip={'text': '{fab_id} ({site_type})\nStatus: {risk_label}'},
        map_style='dark', # Use 'dark' or 'light' for built-in maps without Mapbox tokens
    ))

    # Updated Legend
    st.markdown("""
    **Legend:** 🟠 Front-End Fab · 🔵 Assembly Site · 🗺️ Teal Arcs (Routes) · 🔴 **Red Glow (Yield < 85% or On Hold)**
    """)