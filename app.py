import io
import streamlit as st
import matplotlib.pyplot as plt
import networkx as nx
import pandas as pd
from shapely.geometry import LineString, Point
import contextily as cx
import math

# Import depuis le script existant
from schema_reticulaire import (
    charger_donnees,
    construire_graphe,
    voisinages_stricts,
    PALETTE_OD,
    COLORS_TYPE_GARE,
    CRS_METRIC
)

st.set_page_config(page_title="Reticulator - Générateur Interactif", layout="wide")

@st.cache_data
def load_and_build_graph():
    gares_data, reseau_clip = charger_donnees()
    G, snap_info = construire_graphe(gares_data, reseau_clip)
    neighbors = voisinages_stricts(G)
    
    # Construction du graphe station-à-station pour le routage
    station_graph = nx.Graph()
    for src, nbs in neighbors.items():
        for nb in nbs:
            u, v = src, nb['sid']
            # On stocke toujours la géométrie dans le sens u < v pour avoir une orientation canonique
            if u < v:
                geom_m = nb.get('geom_m', [])
            else:
                geom_m = list(reversed(nb.get('geom_m', [])))
                u, v = v, u
            
            if not station_graph.has_edge(u, v):
                station_graph.add_edge(u, v, weight=nb['dist_m'], geom_m=geom_m)
                
    # Dictionnaire des gares pour accès rapide
    gares_dict = {}
    # On reprojette les gares en métrique pour correspondre à geom_m
    gares_metric = gares_data.to_crs(CRS_METRIC)
    
    for _, row in gares_metric.iterrows():
        uid = str(row['code_uic'])
        gares_dict[uid] = {
            'nom': row['nom_gare'],
            'type': str(row['type_gare']).lower(),
            'x_m': row.geometry.x,
            'y_m': row.geometry.y,
        }
            
    return gares_data, reseau_clip, station_graph, gares_dict, neighbors

try:
    with st.spinner("Chargement et construction du graphe géographique..."):
        gares_data, reseau_clip, station_graph, gares_dict, neighbors = load_and_build_graph()
except Exception as e:
    st.error(f"Erreur lors du chargement des données. Veuillez vérifier que les fichiers geojson et xlsx sont présents. Détail : {e}")
    st.stop()

# --- INITIALISATION SESSION STATE ---
if 'ods' not in st.session_state:
    ods = []
    for i in range(8):
        ods.append({
            'idx': i,
            'color': PALETTE_OD[i],
            'depart': None,
            'arrivee': None,
            'steps': [],
            'served_stations': [],
            'freq_tph': 1.0  # Fréquence en trains/heure
        })
    st.session_state.ods = ods

# --- UI LATERALE ---
st.sidebar.title("🚄 Paramétrage des 8 OD")

station_options = [(k, v['nom']) for k, v in gares_dict.items()]
station_options.sort(key=lambda x: x[1])

def format_station(uic):
    if not uic: return "Aucune"
    return gares_dict.get(uic, {}).get('nom', uic)

def offset_line(geom, offset):
    """Safely offset a LineString."""
    if offset == 0:
        return geom
    if hasattr(geom, 'offset_curve'):
        return geom.offset_curve(offset)
    else:
        # Fallback pour shapely < 2.0
        side = 'left' if offset > 0 else 'right'
        return geom.parallel_offset(abs(offset), side)

for i, od in enumerate(st.session_state.ods):
    with st.sidebar.expander(f"Mission {i+1} - {format_station(od['depart'])} ➔ {format_station(od['arrivee'])}", expanded=False):
        st.markdown(f"**Couleur:** <span style='color:{od['color']}'>&#9608;</span>", unsafe_allow_html=True)
        
        c1, c2 = st.columns(2)
        idx_dep = [o[0] for o in station_options].index(od['depart']) + 1 if od['depart'] else 0
        idx_arr = [o[0] for o in station_options].index(od['arrivee']) + 1 if od['arrivee'] else 0
        
        dep = c1.selectbox(f"Origine", [None] + [o[0] for o in station_options], 
                           format_func=format_station, index=idx_dep, key=f"dep_{i}")
        arr = c2.selectbox(f"Destination", [None] + [o[0] for o in station_options], 
                           format_func=format_station, index=idx_arr, key=f"arr_{i}")
        
        # Fréquence (trains par heure)
        freq = st.number_input("Fréquence (trains/heure)", min_value=0.1, max_value=20.0, value=float(od['freq_tph']), step=0.1, key=f"freq_{i}")
        od['freq_tph'] = freq

        if dep and arr and dep != arr:
            if st.button(f"Calculer le trajet OD {i+1}", key=f"calc_{i}", use_container_width=True):
                try:
                    path = nx.shortest_path(station_graph, source=dep, target=arr, weight='weight')
                    od['depart'] = dep
                    od['arrivee'] = arr
                    od['steps'] = path
                    # Par défaut, toutes les gares du trajet sont desservies
                    od['served_stations'] = path.copy()
                    st.success("Trajet calculé avec succès.")
                    st.rerun()
                except nx.NetworkXNoPath:
                    st.error("Aucun chemin trouvé sur le réseau actuel.")
        
        if len(od['steps']) > 0:
            if st.button(f"🗑️ Effacer la mission {i+1}", key=f"clear_{i}", use_container_width=True):
                od['depart'] = None
                od['arrivee'] = None
                od['steps'] = []
                od['served_stations'] = []
                st.rerun()
                
            # Sélection des gares spécifiquement desservies
            served = st.multiselect(
                "Gares desservies (décocher = passage sans arrêt)", 
                options=od['steps'],
                default=od['served_stations'],
                format_func=format_station,
                key=f"served_{i}"
            )
            od['served_stations'] = served

# --- GENERATION DE LA CARTE ---
st.title("Générateur de Schéma Réticulaire")
st.markdown("Ce tableau de bord permet de calculer et superposer jusqu'à 8 relations ferroviaires. Les traits se décalent automatiquement s'ils partagent les mêmes voies (Offset), et la hiérarchie visuelle reflète la desserte : **si une gare est décochée (passage sans arrêt), la ligne passe au-dessus du point de la gare**. L'épaisseur des traits est proportionnelle à la fréquence, et lorsque plusieurs missions empruntent les mêmes voies, leurs traits sont empilés côte à côte (jointifs, dans un ordre constant) sans jamais se superposer, quelle que soit l'échelle.")

def get_edge_geom(u, v):
    canonical = tuple(sorted((u, v)))
    coords = station_graph[canonical[0]][canonical[1]].get('geom_m', [])
    if not coords:
        # Fallback ligne droite
        pt_u = gares_dict[canonical[0]]
        pt_v = gares_dict[canonical[1]]
        coords = [(pt_u['x_m'], pt_u['y_m']), (pt_v['x_m'], pt_v['y_m'])]
    return LineString(coords)

fig, ax = plt.subplots(figsize=(16, 12))
ax.set_aspect('equal')
ax.axis('off')

# Épaisseur du trait proportionnelle à la fréquence (en points), avec un
# minimum lisible. Utilisée partout (tracé, passages, légende) pour rester cohérent.
LW_PER_TPH = 2.0

def freq_to_lw(freq):
    return max(1.2, freq * LW_PER_TPH)

# 1. Identifier les tronçons partagés (ordre constant = tri par id de mission)
segment_users = {}
for od in st.session_state.ods:
    if len(od['steps']) < 2: continue
    for i in range(len(od['steps'])-1):
        u = od['steps'][i]
        v = od['steps'][i+1]
        canonical = tuple(sorted((u, v)))
        segment_users.setdefault(canonical, []).append(od['idx'])

# 2. Collecter les gares pour le cadrage
all_xs = []
all_ys = []
drawn_stations = set()
for od in st.session_state.ods:
    for sid in od['steps']:
        drawn_stations.add(sid)
        all_xs.append(gares_dict[sid]['x_m'])
        all_ys.append(gares_dict[sid]['y_m'])

# Réseau ferré complet en fond (Z-order: 1)
reseau_m = reseau_clip.to_crs(CRS_METRIC)
reseau_m.plot(ax=ax, color='#d3d3d3', linewidth=1.5, zorder=1)

if not (all_xs and all_ys):
    ax.text(0.5, 0.5, "Aucune mission configurée", horizontalalignment='center',
            verticalalignment='center', transform=ax.transAxes, fontsize=16, color='grey')
    st.pyplot(fig)
    st.stop()

# 3. Figer le cadrage AVANT de tracer pour connaître l'échelle exacte.
#    autoscale(False) empêche les tracés suivants de modifier l'étendue.
minx, maxx = min(all_xs), max(all_xs)
miny, maxy = min(all_ys), max(all_ys)
margin_x = (maxx - minx) * 0.06 + 5000
margin_y = (maxy - miny) * 0.06 + 5000
x0, x1 = minx - margin_x, maxx + margin_x
y0, y1 = miny - margin_y, maxy + margin_y
ax.set_xlim(x0, x1)
ax.set_ylim(y0, y1)
ax.autoscale(False)

# 4. Facteur mètres <-> points (indépendant du DPI). Comme l'aspect est 'equal',
#    l'échelle est uniforme : on peut convertir une épaisseur en points (écran)
#    vers des mètres (données) pour coller les traits exactement.
fig.canvas.draw()
view_w = x1 - x0
meters_per_point = (view_w / ax.get_window_extent().width) * (fig.dpi / 72.0)

# 5. Pré-calcul des offsets : sur chaque tronçon partagé, les missions sont
#    empilées côte à côte (jointives) dans un ordre constant. Le décalage de
#    chaque trait dérive de sa propre épaisseur -> ils restent collés et dans
#    le même ordre quelle que soit l'échelle, sans jamais se superposer.
segment_offsets = {}  # (canonical, od_idx) -> offset en mètres
for canonical, raw_users in segment_users.items():
    users = sorted(raw_users)
    widths_m = [freq_to_lw(st.session_state.ods[idx]['freq_tph']) * meters_per_point for idx in users]
    cursor = -sum(widths_m) / 2.0
    for idx, w in zip(users, widths_m):
        segment_offsets[(canonical, idx)] = cursor + w / 2.0
        cursor += w

# 6. Lignes de mission (Z-order: 4)
for canonical_edge, raw_users in segment_users.items():
    geom = get_edge_geom(canonical_edge[0], canonical_edge[1])
    for od_idx in sorted(raw_users):
        od = st.session_state.ods[od_idx]
        shifted_geom = offset_line(geom, segment_offsets[(canonical_edge, od_idx)])
        if shifted_geom.is_empty:
            continue
        lw = freq_to_lw(od['freq_tph'])
        if shifted_geom.geom_type == 'LineString':
            xs, ys = shifted_geom.xy
            ax.plot(xs, ys, color=od['color'], linewidth=lw, zorder=4, solid_capstyle='round')
        elif shifted_geom.geom_type == 'MultiLineString':
            for ls in shifted_geom.geoms:
                xs, ys = ls.xy
                ax.plot(xs, ys, color=od['color'], linewidth=lw, zorder=4, solid_capstyle='round')

# 7. Points de gare (Z-order: 5) + étiquettes
label_dx = max(1000, meters_per_point * 14)
for sid in drawn_stations:
    info = gares_dict[sid]
    x, y = info['x_m'], info['y_m']
    color = COLORS_TYPE_GARE.get(info['type'], COLORS_TYPE_GARE['default'])
    ax.plot(x, y, marker='o', markersize=12, color='white', zorder=5)
    ax.plot(x, y, marker='o', markersize=8, color=color, zorder=5)
    if info['type'] in ['a', 'b']:
        fontsize = 10 if info['type'] == 'a' else 8
        fontweight = 'bold' if info['type'] == 'a' else 'normal'
        ax.text(x + label_dx, y, info['nom'], fontsize=fontsize, fontweight=fontweight,
                zorder=7, color='black', ha='left', va='center',
                bbox=dict(facecolor='white', alpha=0.7, edgecolor='none', boxstyle='round,pad=0.2'))

# 8. Gares non-desservies (passage sans arrêt) : on redessine un extrait local
#    de la ligne (mêmes offsets) au-dessus du point de la gare (Z-order: 6).
catch_radius = max(1500, view_w * 0.012)
for od in st.session_state.ods:
    if len(od['steps']) < 2: continue
    for i in range(len(od['steps'])-1):
        u = od['steps'][i]
        v = od['steps'][i+1]
        canonical = tuple(sorted((u, v)))
        shifted_geom = offset_line(get_edge_geom(canonical[0], canonical[1]),
                                   segment_offsets[(canonical, od['idx'])])
        lw = freq_to_lw(od['freq_tph'])
        for sid in (u, v):
            if sid not in od['served_stations']:
                info = gares_dict[sid]
                station_pt = Point(info['x_m'], info['y_m'])
                local_seg = shifted_geom.intersection(station_pt.buffer(catch_radius))
                if local_seg.is_empty:
                    continue
                if local_seg.geom_type == 'LineString':
                    xs, ys = local_seg.xy
                    ax.plot(xs, ys, color=od['color'], linewidth=lw, zorder=6, solid_capstyle='round')
                elif local_seg.geom_type == 'MultiLineString':
                    for ls in local_seg.geoms:
                        xs, ys = ls.xy
                        ax.plot(xs, ys, color=od['color'], linewidth=lw, zorder=6, solid_capstyle='round')

# 9. Légende et fond de carte
from matplotlib.lines import Line2D
legend_elements = []
for od in st.session_state.ods:
    if len(od['steps']) >= 2:
        dep_nom = format_station(od['depart'])
        arr_nom = format_station(od['arrivee'])
        legend_elements.append(Line2D([0], [0], color=od['color'], lw=freq_to_lw(od['freq_tph']),
                                      label=f"Mission {od['idx']+1}: {dep_nom} ➔ {arr_nom} ({od['freq_tph']} t/h)"))
if legend_elements:
    ax.legend(handles=legend_elements, loc='upper right', frameon=True, facecolor='white', framealpha=0.9, fontsize=10)

try:
    # Ajout du fond de carte géographique OSM clair
    cx.add_basemap(ax, crs=CRS_METRIC, source=cx.providers.CartoDB.Positron, alpha=0.5, zorder=0)
except Exception as e:
    st.warning("Impossible de charger le fond de carte géographique (accès internet requis pour les tuiles).")

# Affichage Streamlit
st.pyplot(fig)

# Export PNG
if all_xs and all_ys:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=300, bbox_inches='tight')
    st.download_button(
        label="📥 Exporter le schéma en PNG (Haute Résolution)",
        data=buf.getvalue(),
        file_name="schema_reticulaire.png",
        mime="image/png",
        use_container_width=True
    )
