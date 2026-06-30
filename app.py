import io
import streamlit as st
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.patches import Rectangle, Patch
from matplotlib.lines import Line2D
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

# --- Palette de couleurs de mission, choisie à la main par l'utilisateur ---
# Exclut volontairement les teintes des types de gare (rouge type A, bleu type B,
# vert type C, gris défaut) afin de préserver la lisibilité des marqueurs de gare.
MISSION_PALETTE = {
    "Bleu ardoise": "#4E79A7",
    "Orange": "#F28E2B",
    "Turquoise": "#76B7B2",
    "Vert olive": "#59A14F",
    "Jaune moutarde": "#EDC948",
    "Mauve": "#B07AA1",
    "Rose poudré": "#FF9DA7",
    "Brun": "#9C755F",
    "Indigo": "#5B5BD6",
    "Cyan profond": "#1B9E9E",
    "Magenta": "#C0399F",
    "Anthracite": "#3D3D5C",
}

# Couleurs réservées aux gares (interdites pour les missions, comparaison RGB).
_RESERVED_GARE_COLORS = [v for v in COLORS_TYPE_GARE.values()]


def _hex_to_rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def color_conflicts_with_gares(hex_color, seuil=60):
    """Renvoie la couleur de gare en conflit si la teinte choisie est trop
    proche (distance euclidienne RGB) d'une couleur réservée aux gares."""
    try:
        r, g, b = _hex_to_rgb(hex_color)
    except Exception:
        return None
    for res in _RESERVED_GARE_COLORS:
        rr, rg, rb = _hex_to_rgb(res)
        if math.sqrt((r - rr) ** 2 + (g - rg) ** 2 + (b - rb) ** 2) < seuil:
            return res
    return None

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
        # --- Choix manuel de la couleur dans une palette (hors couleurs de gare) ---
        palette_names = list(MISSION_PALETTE.keys())
        # Détermine la sélection courante : un nom de palette, sinon "Personnalisé…"
        current_name = next((n for n, h in MISSION_PALETTE.items()
                             if h.lower() == str(od['color']).lower()), "Personnalisé…")
        choix = st.selectbox(
            "Couleur de la mission",
            palette_names + ["Personnalisé…"],
            index=(palette_names + ["Personnalisé…"]).index(current_name),
            key=f"palette_{i}",
        )
        if choix == "Personnalisé…":
            custom = st.color_picker("Teinte personnalisée", value=od['color'], key=f"color_{i}")
            conflit = color_conflicts_with_gares(custom)
            if conflit:
                st.warning(
                    f"Cette teinte est trop proche d'une couleur réservée aux gares "
                    f"({conflit}). Choisissez-en une autre pour garder les gares lisibles."
                )
            else:
                od['color'] = custom
        else:
            od['color'] = MISSION_PALETTE[choix]
        st.markdown(
            f"**Aperçu:** <span style='color:{od['color']};font-size:20px'>&#9608;</span> "
            f"`{od['color']}`",
            unsafe_allow_html=True,
        )

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

# --- CADRAGE / ZOOM MANUEL ---
st.sidebar.markdown("---")
st.sidebar.subheader("🔍 Cadrage de la carte")
manual_zoom = st.sidebar.checkbox("Activer le zoom manuel", value=False,
                                  help="Permet de zoomer et de se déplacer sur une partie de la carte.")
zoom_level, pan_x, pan_y = 1.0, 0.5, 0.5
if manual_zoom:
    zoom_level = st.sidebar.slider("Niveau de zoom", 1.0, 10.0, 1.0, 0.1)
    pan_x = st.sidebar.slider("Déplacement horizontal", 0.0, 1.0, 0.5, 0.01,
                              help="0 = gauche, 1 = droite")
    pan_y = st.sidebar.slider("Déplacement vertical", 0.0, 1.0, 0.5, 0.01,
                              help="0 = bas, 1 = haut")

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
# Liseré blanc (en points) ajouté de chaque côté de chaque trait de mission
# pour séparer visuellement les missions empilées côte à côte.
LISERE_PT = 1.2

def freq_to_lw(freq):
    return max(1.2, freq * LW_PER_TPH)

def white_outline(lw):
    """Effet de tracé : liseré blanc fin sous la couleur de la mission."""
    return [pe.Stroke(linewidth=lw + 2 * LISERE_PT, foreground='white'), pe.Normal()]

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
bx0, bx1 = minx - margin_x, maxx + margin_x
by0, by1 = miny - margin_y, maxy + margin_y

# Zoom manuel : on réduit la fenêtre autour d'un centre piloté par les curseurs.
if manual_zoom and zoom_level > 1.0:
    base_w = bx1 - bx0
    base_h = by1 - by0
    new_w = base_w / zoom_level
    new_h = base_h / zoom_level
    # Le centre se déplace dans la plage où la fenêtre reste incluse dans l'étendue.
    cx_view = bx0 + new_w / 2 + pan_x * (base_w - new_w)
    cy_view = by0 + new_h / 2 + pan_y * (base_h - new_h)
    x0, x1 = cx_view - new_w / 2, cx_view + new_w / 2
    y0, y1 = cy_view - new_h / 2, cy_view + new_h / 2
else:
    x0, x1, y0, y1 = bx0, bx1, by0, by1

ax.set_xlim(x0, x1)
ax.set_ylim(y0, y1)
ax.autoscale(False)

# 4. Facteur mètres <-> points (indépendant du DPI). Comme l'aspect est 'equal',
#    l'échelle est uniforme : on peut convertir une épaisseur en points (écran)
#    vers des mètres (données) pour coller les traits exactement.
fig.canvas.draw()
view_w = x1 - x0
view_h = y1 - y0
meters_per_point = (view_w / ax.get_window_extent().width) * (fig.dpi / 72.0)
gap_m = 2 * LISERE_PT * meters_per_point  # espace réservé au liseré blanc entre traits

# 5. Pré-calcul des offsets : sur chaque tronçon partagé, les missions sont
#    empilées côte à côte dans un ordre constant, avec un fin liseré blanc entre
#    elles. Le décalage de chaque trait dérive de sa propre épaisseur + liseré ->
#    elles restent dans le même ordre quelle que soit l'échelle, sans se superposer.
segment_offsets = {}   # (canonical, od_idx) -> offset en mètres
segment_width_m = {}   # canonical -> largeur totale du faisceau (mètres)
for canonical, raw_users in segment_users.items():
    users = sorted(raw_users)
    widths_m = [freq_to_lw(st.session_state.ods[idx]['freq_tph']) * meters_per_point for idx in users]
    # largeur visuelle du faisceau = traits + liserés internes + liserés externes
    total = sum(widths_m) + gap_m * max(0, len(users) - 1) + 2 * LISERE_PT * meters_per_point
    segment_width_m[canonical] = total
    cursor = -total / 2.0
    for idx, w in zip(users, widths_m):
        segment_offsets[(canonical, idx)] = cursor + w / 2.0
        cursor += w + gap_m

# 6. Lignes de mission (Z-order: 4) avec liseré blanc
for canonical_edge, raw_users in segment_users.items():
    geom = get_edge_geom(canonical_edge[0], canonical_edge[1])
    for od_idx in sorted(raw_users):
        od = st.session_state.ods[od_idx]
        shifted_geom = offset_line(geom, segment_offsets[(canonical_edge, od_idx)])
        if shifted_geom.is_empty:
            continue
        lw = freq_to_lw(od['freq_tph'])
        parts = [shifted_geom] if shifted_geom.geom_type == 'LineString' else list(shifted_geom.geoms)
        for ls in parts:
            xs, ys = ls.xy
            ax.plot(xs, ys, color=od['color'], linewidth=lw, zorder=4,
                    solid_capstyle='round', path_effects=white_outline(lw))

# 7. Gares : rectangles dimensionnés au faisceau de missions (Z-order: 5).
#    - Bord noir (type a/b) ou gris (c/défaut), fond = couleur du type.
#    - Le rectangle couvre les missions qui s'ARRÊTENT (trait masqué dessous).
#    - Les missions sans arrêt seront redessinées par-dessus (étape 8) -> elles
#      traversent visiblement le rectangle.
#    La largeur du rectangle s'adapte au nombre de missions traversant et à leur
#    épaisseur (largeur du faisceau le plus large incident à la gare).
station_bundle = {}    # sid -> largeur du faisceau le plus large (mètres)
station_missions = {}  # sid -> set des missions touchant la gare
for canonical, raw_users in segment_users.items():
    for sid in canonical:
        station_bundle[sid] = max(station_bundle.get(sid, 0.0), segment_width_m[canonical])
for od in st.session_state.ods:
    for sid in set(od['steps']):
        station_missions.setdefault(sid, set()).add(od['idx'])

pad_m = meters_per_point * 3
min_dim = meters_per_point * 9
station_rects = {}  # sid -> (rx0, ry0, rx1, ry1) bbox du rectangle en mètres
for sid in drawn_stations:
    info = gares_dict[sid]
    x, y = info['x_m'], info['y_m']
    color = COLORS_TYPE_GARE.get(info['type'], COLORS_TYPE_GARE['default'])
    edge = '#000000' if info['type'] in ('a', 'b') else '#555555'
    bundle = station_bundle.get(sid, 0.0)
    w_rect = max(min_dim, bundle + 2 * pad_m)
    h_rect = max(min_dim * 0.7, w_rect * 0.5)
    rx, ry = x - w_rect / 2, y - h_rect / 2
    rect = Rectangle((rx, ry), w_rect, h_rect, facecolor=color, edgecolor=edge,
                     linewidth=1.2, zorder=5, joinstyle='round')
    rect.set_clip_on(True)
    ax.add_patch(rect)
    station_rects[sid] = (rx, ry, rx + w_rect, ry + h_rect)

# 8. Gares non-desservies (passage sans arrêt) : on redessine un extrait local
#    de la ligne (mêmes offsets) PAR-DESSUS le rectangle (Z-order: 6) -> le trait
#    traverse visiblement le rectangle.
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
                parts = [local_seg] if local_seg.geom_type == 'LineString' else list(getattr(local_seg, 'geoms', []))
                for ls in parts:
                    if ls.geom_type != 'LineString' or ls.is_empty:
                        continue
                    xs, ys = ls.xy
                    ax.plot(xs, ys, color=od['color'], linewidth=lw, zorder=6,
                            solid_capstyle='round', path_effects=white_outline(lw))

# 9. Étiquettes de gares (types a/b) avec anti-collision et trait de rappel.
#    On évite la superposition entre étiquettes et rectangles, et on garantit que
#    chaque étiquette reste dans le cadre (donc dans le fond OSM).
def _overlap(a, b):
    return not (a[2] <= b[0] or a[0] >= b[2] or a[3] <= b[1] or a[1] >= b[3])

# Limites internes : on garde une petite marge pour rester dans le fond de carte.
bound_m = meters_per_point * 2
lim = (x0 + bound_m, y0 + bound_m, x1 - bound_m, y1 - bound_m)

# bboxes déjà occupées : tous les rectangles de gare.
occupied = list(station_rects.values())

# On étiquette d'abord les gares de type a (plus prioritaires), puis b.
to_label = [sid for sid in drawn_stations if gares_dict[sid]['type'] in ('a', 'b')]
to_label.sort(key=lambda s: 0 if gares_dict[s]['type'] == 'a' else 1)

gap_lbl = meters_per_point * 4
# directions candidates (dx, dy) : droite, gauche, haut, bas, puis diagonales.
DIRECTIONS = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, 1), (1, -1), (-1, -1)]

for sid in to_label:
    info = gares_dict[sid]
    x, y = info['x_m'], info['y_m']
    fontsize = 10 if info['type'] == 'a' else 8
    fontweight = 'bold' if info['type'] == 'a' else 'normal'
    nom = info['nom']
    # estimation de la taille du texte en mètres
    text_w = max(1, len(nom)) * fontsize * 0.62 * meters_per_point
    text_h = fontsize * 1.35 * meters_per_point
    rx0, ry0, rx1, ry1 = station_rects[sid]
    half_rw = (rx1 - rx0) / 2
    half_rh = (ry1 - ry0) / 2

    placed = None
    is_default = False
    for r in range(1, 6):
        for di, (dx, dy) in enumerate(DIRECTIONS):
            offx = (half_rw + text_w / 2 + gap_lbl) * r if dx != 0 else 0
            offy = (half_rh + text_h / 2 + gap_lbl) * r if dy != 0 else 0
            cx_l = x + dx * offx
            cy_l = y + dy * offy
            bbox = (cx_l - text_w / 2, cy_l - text_h / 2,
                    cx_l + text_w / 2, cy_l + text_h / 2)
            # rester dans le cadre (donc dans le fond OSM)
            if bbox[0] < lim[0] or bbox[1] < lim[1] or bbox[2] > lim[2] or bbox[3] > lim[3]:
                continue
            if any(_overlap(bbox, o) for o in occupied):
                continue
            placed = (cx_l, cy_l, bbox)
            is_default = (r == 1 and di == 0)
            break
        if placed:
            break

    if placed is None:
        # repli : à droite, position par défaut même si imparfaite (clampée au cadre)
        cx_l = min(max(x + half_rw + text_w / 2 + gap_lbl, lim[0] + text_w / 2), lim[2] - text_w / 2)
        cy_l = min(max(y, lim[1] + text_h / 2), lim[3] - text_h / 2)
        bbox = (cx_l - text_w / 2, cy_l - text_h / 2, cx_l + text_w / 2, cy_l + text_h / 2)
        placed = (cx_l, cy_l, bbox)
        is_default = False

    cx_l, cy_l, bbox = placed
    # Trait de rappel si l'étiquette a été écartée (sinon le couple est évident).
    if not is_default:
        ax.plot([x, cx_l], [y, cy_l], color='#888888', linewidth=0.6,
                zorder=6, clip_on=True)
    ax.text(cx_l, cy_l, nom, fontsize=fontsize, fontweight=fontweight,
            zorder=7, color='black', ha='center', va='center', clip_on=True,
            bbox=dict(facecolor='white', alpha=0.75, edgecolor='none', boxstyle='round,pad=0.2'))
    occupied.append(bbox)

# 10. Fond de carte géographique OSM clair
try:
    cx.add_basemap(ax, crs=CRS_METRIC, source=cx.providers.CartoDB.Positron, alpha=0.5, zorder=0)
except Exception as e:
    st.warning("Impossible de charger le fond de carte géographique (accès internet requis pour les tuiles).")

# 11. Légende séparée (figure dédiée) -> aucune collision avec la carte, et
#     export d'un PNG carte + un PNG légende distincts.
def build_legend_figure():
    mission_handles = []
    labels = []
    for od in st.session_state.ods:
        if len(od['steps']) >= 2:
            dep_nom = format_station(od['depart'])
            arr_nom = format_station(od['arrivee'])
            lbl = f"Mission {od['idx']+1} : {dep_nom} ➔ {arr_nom} ({od['freq_tph']} t/h)"
            labels.append(lbl)
            mission_handles.append(Line2D(
                [0], [0], color=od['color'], lw=freq_to_lw(od['freq_tph']),
                path_effects=white_outline(freq_to_lw(od['freq_tph'])),
                label=lbl))
    gare_handles = [
        Patch(facecolor=COLORS_TYPE_GARE['a'], edgecolor='black', label="Gare type A"),
        Patch(facecolor=COLORS_TYPE_GARE['b'], edgecolor='black', label="Gare type B"),
        Patch(facecolor=COLORS_TYPE_GARE['c'], edgecolor='#555555', label="Gare type C"),
        Patch(facecolor=COLORS_TYPE_GARE['default'], edgecolor='#555555', label="Autre gare"),
    ]
    n_rows = len(mission_handles) + len(gare_handles) + 2
    # Largeur adaptée au libellé le plus long pour éviter toute troncature.
    max_len = max([len(l) for l in labels] + [len("Types de gare"), 20])
    fig_w = max(5.0, 0.115 * max_len + 1.2)
    fig_l, ax_l = plt.subplots(figsize=(fig_w, max(2.0, 0.34 * n_rows)))
    ax_l.axis('off')
    y_top = 0.98
    leg1 = ax_l.legend(handles=mission_handles if mission_handles else
                       [Line2D([0], [0], color='none', label="Aucune mission tracée")],
                       loc='upper left', bbox_to_anchor=(0.0, y_top),
                       title="Missions", frameon=False, fontsize=10,
                       title_fontproperties={'weight': 'bold', 'size': 11})
    ax_l.add_artist(leg1)
    ax_l.legend(handles=gare_handles, loc='lower left', bbox_to_anchor=(0.0, 0.02),
                title="Types de gare", frameon=False, fontsize=10,
                title_fontproperties={'weight': 'bold', 'size': 11})
    return fig_l

fig_legend = build_legend_figure()

# --- Affichage Streamlit : carte et légende côte à côte, sans superposition ---
col_map, col_leg = st.columns([4, 1])
with col_map:
    st.pyplot(fig)
with col_leg:
    st.markdown("**Légende**")
    st.pyplot(fig_legend)

# --- Exports PNG distincts (carte / légende) ---
buf_map = io.BytesIO()
fig.savefig(buf_map, format="png", dpi=300, bbox_inches='tight')
buf_leg = io.BytesIO()
fig_legend.savefig(buf_leg, format="png", dpi=300, bbox_inches='tight')

dl1, dl2 = st.columns(2)
with dl1:
    st.download_button(
        label="📥 Exporter la carte (PNG HD)",
        data=buf_map.getvalue(),
        file_name="schema_reticulaire_carte.png",
        mime="image/png",
        use_container_width=True,
    )
with dl2:
    st.download_button(
        label="📥 Exporter la légende (PNG)",
        data=buf_leg.getvalue(),
        file_name="schema_reticulaire_legende.png",
        mime="image/png",
        use_container_width=True,
    )
