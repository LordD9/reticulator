import io
import base64
from collections import deque
import streamlit as st
import streamlit.components.v1 as components
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.patches import Polygon, Patch
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

# --- MODE D'AFFICHAGE ---
st.sidebar.markdown("---")
st.sidebar.subheader("🖼️ Mode d'affichage")
schema_mode = st.sidebar.checkbox(
    "Mode Schéma (représentation simplifiée)", value=False,
    help="Remplace le rendu cartographique (fond OSM + tracés réels) par un schéma "
         "épuré : tracés à angles droits (nord/sud/est/ouest uniquement), gares "
         "alignées tant que la direction ne change pas, espacement régularisé, et "
         "toutes les gares nommées. L'affichage ET l'export reprennent cette version.",
)

# --- CADRAGE / ZOOM ---
st.sidebar.markdown("---")
st.sidebar.subheader("🔍 Cadrage de la carte")
st.sidebar.caption(
    "Le zoom est interactif directement sur la carte : **molette** pour zoomer/"
    "dézoomer sous le curseur, **glisser** pour se déplacer, **double-clic** pour "
    "réinitialiser."
)

# --- GENERATION DE LA CARTE ---
st.title("Générateur de Schéma Réticulaire")
st.markdown("Ce tableau de bord permet de calculer et superposer jusqu'à 8 relations ferroviaires. Les traits se décalent automatiquement s'ils partagent les mêmes voies (Offset), et la hiérarchie visuelle reflète la desserte : **si une gare est décochée (passage sans arrêt), la ligne passe au-dessus du point de la gare**. L'épaisseur des traits est proportionnelle à la fréquence, et lorsque plusieurs missions empruntent les mêmes voies, leurs traits sont empilés côte à côte (jointifs, dans un ordre constant) sans jamais se superposer, quelle que soit l'échelle.")
if schema_mode:
    st.info("📐 **Mode Schéma actif** : rendu simplifié à angles droits, gares alignées et espacement régularisé, toutes les gares nommées. L'export reprend cette version.")

# Espacement schématique de référence (unités ~métriques pour rester cohérent
# avec les marges et conversions mètres<->points existantes).
SCHEMA_UNIT = 12000.0


def schematic_edge_geom(a, b):
    """Tronçon schématique entre deux gares : segment droit si elles partagent une
    ligne (même X ou même Y), sinon un coude à angle droit (horizontal puis
    vertical). Déterministe pour une paire canonique donnée -> les tronçons
    partagés restent superposables et décalables (offset)."""
    xa, ya = pos[a]
    xb, yb = pos[b]
    eps = SCHEMA_UNIT * 1e-3
    if abs(xa - xb) < eps or abs(ya - yb) < eps:
        coords = [(xa, ya), (xb, yb)]
    else:
        coords = [(xa, ya), (xb, ya), (xb, yb)]  # coude à angle droit
    return LineString(coords)


def get_edge_geom(u, v):
    canonical = tuple(sorted((u, v)))
    if schema_mode:
        return schematic_edge_geom(canonical[0], canonical[1])
    coords = station_graph[canonical[0]][canonical[1]].get('geom_m', [])
    if not coords:
        # Fallback ligne droite
        pt_u = gares_dict[canonical[0]]
        pt_v = gares_dict[canonical[1]]
        coords = [(pt_u['x_m'], pt_u['y_m']), (pt_v['x_m'], pt_v['y_m'])]
    return LineString(coords)


def compute_schematic_layout(stations, segments, gd):
    """Place les gares sur une grille schématique inspirée de la forme réelle.

    Parcours en largeur du graphe des tronçons empruntés : chaque tronçon est
    projeté sur l'axe cardinal (horizontal/vertical) dominant de sa direction
    géographique réelle. Tant que la direction ne change pas, les gares restent
    donc alignées sur une même ligne ; un changement d'axe crée un angle droit.
    L'espacement reprend la distance réelle, régularisée (compressée si grande).
    Renvoie sid -> (X, Y) en unités schématiques."""
    adj = {s: [] for s in stations}
    edist = {}
    for (a, b) in segments:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
        d = math.hypot(gd[a]['x_m'] - gd[b]['x_m'], gd[a]['y_m'] - gd[b]['y_m'])
        edist[(a, b)] = d
    dvals = sorted(edist.values())
    med = dvals[len(dvals) // 2] if dvals else 1.0
    med = med or 1.0

    def spacing(d):
        # Proportionnel à la distance réelle mais borné : préserve les écarts
        # relatifs tout en compressant les très longues branches.
        return SCHEMA_UNIT * min(2.0, max(0.55, d / med))

    cell = SCHEMA_UNIT * 0.25

    def key(p):
        return (round(p[0] / cell), round(p[1] / cell))

    pos = {}
    occupied = {}

    def resolve(cand, step):
        """Décale la gare le long de l'axe perpendiculaire au pas si la cellule
        est déjà occupée (crée alors un léger coude à angle droit)."""
        if key(cand) not in occupied:
            return cand
        perp = (0.0, SCHEMA_UNIT * 0.5) if step[0] != 0 else (SCHEMA_UNIT * 0.5, 0.0)
        for k in range(1, 10):
            for sgn in (1, -1):
                c = (cand[0] + perp[0] * k * sgn, cand[1] + perp[1] * k * sgn)
                if key(c) not in occupied:
                    return c
        return cand

    remaining = set(stations)
    comp_offset_x = 0.0
    while remaining:
        # Graine = gare la plus au sud-ouest (oriente le schéma « nord en haut »).
        seed = min(remaining, key=lambda s: (gd[s]['x_m'], gd[s]['y_m']))
        pos[seed] = (comp_offset_x, 0.0)
        occupied[key(pos[seed])] = seed
        q = deque([seed])
        placed = [seed]
        while q:
            u = q.popleft()
            for v in sorted(adj.get(u, []),
                            key=lambda z: edist.get(tuple(sorted((u, z))), 0.0)):
                if v in pos:
                    continue
                gdx = gd[v]['x_m'] - gd[u]['x_m']
                gdy = gd[v]['y_m'] - gd[u]['y_m']
                L = spacing(math.hypot(gdx, gdy))
                if abs(gdx) >= abs(gdy):
                    step = (math.copysign(L, gdx) if gdx else L, 0.0)
                else:
                    step = (0.0, math.copysign(L, gdy) if gdy else L)
                cand = resolve((pos[u][0] + step[0], pos[u][1] + step[1]), step)
                pos[v] = cand
                occupied[key(cand)] = v
                placed.append(v)
                q.append(v)
        remaining -= set(placed)
        # Composante suivante décalée à droite pour éviter tout recouvrement.
        comp_offset_x = max(pos[s][0] for s in placed) + SCHEMA_UNIT * 3

    return pos

fig, ax = plt.subplots(figsize=(16, 12))
ax.set_aspect('equal')
ax.axis('off')

# Épaisseur du trait proportionnelle à la fréquence (en points), avec un
# minimum lisible. Utilisée partout (tracé, passages, légende) pour rester cohérent.
LW_PER_TPH = 2.0
# Liseré blanc (en points) ajouté de chaque côté de chaque trait de mission
# pour séparer visuellement les missions empilées côte à côte. Volontairement
# fin : une simple séparation de lisibilité, pas une bande large.
LISERE_PT = 0.5

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

# 2. Gares dessinées + positions (géographiques ou schématiques selon le mode).
#    `pos` est la source unique de coordonnées utilisée par tout le rendu.
drawn_stations = set()
for od in st.session_state.ods:
    for sid in od['steps']:
        drawn_stations.add(sid)

if schema_mode and drawn_stations:
    pos = compute_schematic_layout(drawn_stations, list(segment_users.keys()), gares_dict)
else:
    pos = {sid: (gares_dict[sid]['x_m'], gares_dict[sid]['y_m']) for sid in drawn_stations}

all_xs = [pos[s][0] for s in drawn_stations]
all_ys = [pos[s][1] for s in drawn_stations]

# Réseau ferré complet en fond (Z-order: 1) — uniquement en mode cartographique.
if not schema_mode:
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
# Marges plus larges en mode Schéma : toutes les gares sont nommées, il faut de
# la place autour du dessin pour poser les étiquettes sans les tronquer.
mfac = 0.12 if schema_mode else 0.06
madd = SCHEMA_UNIT * 0.7 if schema_mode else 5000
margin_x = (maxx - minx) * mfac + madd
margin_y = (maxy - miny) * mfac + madd
bx0, bx1 = minx - margin_x, maxx + margin_x
by0, by1 = miny - margin_y, maxy + margin_y

# Cadrage figé sur l'étendue des missions. Le zoom/déplacement se fait ensuite de
# manière interactive côté navigateur (molette + glisser) sur l'image rendue.
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

# 7. Gares : rectangles PERPENDICULAIRES au faisceau de missions (Z-order: 5).
#    - Bord noir (type a/b) ou gris (c/défaut), fond = couleur du type.
#    - Le rectangle couvre les missions qui s'ARRÊTENT (trait masqué dessous).
#    - Les missions sans arrêt seront redessinées par-dessus (étape 8) -> elles
#      traversent visiblement le rectangle.
#    Le grand côté du rectangle est orienté perpendiculairement aux traits de
#    mission et sa longueur vaut la largeur cumulée de TOUTES les missions passant
#    par la gare (le faisceau incident le plus large) : le rectangle traverse ainsi
#    exactement l'ensemble des traits empilés, desservis ou non.
incident_segments = {}   # sid -> [canonical, ...] segments touchant la gare
station_bundle = {}      # sid -> largeur du faisceau le plus large (mètres)
station_missions = {}    # sid -> set des missions touchant la gare
for canonical in segment_users:
    for sid in canonical:
        incident_segments.setdefault(sid, []).append(canonical)
        station_bundle[sid] = max(station_bundle.get(sid, 0.0), segment_width_m[canonical])
for od in st.session_state.ods:
    for sid in set(od['steps']):
        station_missions.setdefault(sid, set()).add(od['idx'])


def station_axis(sid):
    """Direction unitaire (dx, dy) du faisceau de missions le plus large incident
    à la gare, servant à orienter le rectangle perpendiculairement aux traits.
    Renvoie (1, 0) par défaut si aucune direction exploitable."""
    px, py = pos[sid]
    best_w, best_dir = -1.0, (1.0, 0.0)
    for canonical in incident_segments.get(sid, []):
        geom = get_edge_geom(canonical[0], canonical[1])
        L = geom.length
        if L <= 0:
            continue
        step = min(L, max(L * 0.25, 300.0))
        # La gare est à l'une des deux extrémités de la géométrie centrale.
        c0, c1 = geom.coords[0], geom.coords[-1]
        if (c0[0] - px) ** 2 + (c0[1] - py) ** 2 <= (c1[0] - px) ** 2 + (c1[1] - py) ** 2:
            p_end, p_ref = geom.interpolate(0.0), geom.interpolate(step)
        else:
            p_end, p_ref = geom.interpolate(L), geom.interpolate(L - step)
        vx, vy = p_ref.x - p_end.x, p_ref.y - p_end.y
        norm = math.hypot(vx, vy)
        if norm < 1e-6:
            continue
        w = segment_width_m[canonical]
        if w > best_w:
            best_w, best_dir = w, (vx / norm, vy / norm)
    return best_dir


min_len = meters_per_point * 9      # longueur minimale (petites gares mono-mission)
bar_thick = meters_per_point * 7    # épaisseur (petit côté) du rectangle de gare
station_rects = {}  # sid -> (rx0, ry0, rx1, ry1) bbox englobante (anti-collision)
for sid in drawn_stations:
    info = gares_dict[sid]
    x, y = pos[sid]
    color = COLORS_TYPE_GARE.get(info['type'], COLORS_TYPE_GARE['default'])
    edge = '#000000' if info['type'] in ('a', 'b') else '#555555'
    # Longueur = largeur cumulée du faisceau (missions empilées + liserés).
    length = max(min_len, station_bundle.get(sid, 0.0))
    dx, dy = station_axis(sid)          # direction des traits (grand axe du faisceau)
    nx_, ny_ = -dy, dx                  # normale : axe le long duquel s'empilent les missions
    hl = length / 2.0                   # demi-longueur (le long de la normale)
    ht = bar_thick / 2.0                # demi-épaisseur (le long des traits)
    corners = [
        (x + ht * dx + hl * nx_, y + ht * dy + hl * ny_),
        (x + ht * dx - hl * nx_, y + ht * dy - hl * ny_),
        (x - ht * dx - hl * nx_, y - ht * dy - hl * ny_),
        (x - ht * dx + hl * nx_, y - ht * dy + hl * ny_),
    ]
    poly = Polygon(corners, closed=True, facecolor=color, edgecolor=edge,
                   linewidth=1.2, zorder=5, joinstyle='round')
    poly.set_clip_on(True)
    ax.add_patch(poly)
    xs_c = [c[0] for c in corners]
    ys_c = [c[1] for c in corners]
    station_rects[sid] = (min(xs_c), min(ys_c), max(xs_c), max(ys_c))

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
                station_pt = Point(*pos[sid])
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

# 9. Étiquettes de gares avec anti-collision et trait de rappel.
#    - Mode carte : seules les gares de type a/b sont nommées (lisibilité du fond).
#    - Mode Schéma : TOUTES les gares sont nommées, avec une taille de police
#      croissante selon le type (a > b > c > autre).
def _overlap(a, b):
    return not (a[2] <= b[0] or a[0] >= b[2] or a[3] <= b[1] or a[1] >= b[3])

# Limites internes : on garde une petite marge pour rester dans le cadre.
bound_m = meters_per_point * 2
lim = (x0 + bound_m, y0 + bound_m, x1 - bound_m, y1 - bound_m)

# bboxes déjà occupées : tous les rectangles de gare.
occupied = list(station_rects.values())

# Taille de police par type de gare (mode Schéma).
FONT_BY_TYPE = {'a': 11, 'b': 9, 'c': 8, 'default': 7}
type_rank = {'a': 0, 'b': 1, 'c': 2}

if schema_mode:
    # Toutes les gares, des plus importantes (type a) aux moins prioritaires.
    to_label = sorted(drawn_stations,
                      key=lambda s: type_rank.get(gares_dict[s]['type'], 3))
else:
    # On étiquette d'abord les gares de type a (plus prioritaires), puis b.
    to_label = [sid for sid in drawn_stations if gares_dict[sid]['type'] in ('a', 'b')]
    to_label.sort(key=lambda s: 0 if gares_dict[s]['type'] == 'a' else 1)

gap_lbl = meters_per_point * 4
# directions candidates (dx, dy) : droite, gauche, haut, bas, puis diagonales.
DIRECTIONS = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, 1), (1, -1), (-1, -1)]

for sid in to_label:
    info = gares_dict[sid]
    x, y = pos[sid]
    t = info['type']
    if schema_mode:
        fontsize = FONT_BY_TYPE.get(t, FONT_BY_TYPE['default'])
    else:
        fontsize = 10 if t == 'a' else 8
    fontweight = 'bold' if t == 'a' else 'normal'
    nom = info['nom']
    # estimation de la taille du texte en mètres
    text_w = max(1, len(nom)) * fontsize * 0.62 * meters_per_point
    text_h = fontsize * 1.35 * meters_per_point
    rx0, ry0, rx1, ry1 = station_rects[sid]
    half_rw = (rx1 - rx0) / 2
    half_rh = (ry1 - ry0) / 2

    placed = None
    is_default = False
    # Plus de rayons candidats en mode Schéma : beaucoup plus d'étiquettes à caser.
    max_r = 9 if schema_mode else 6
    for r in range(1, max_r):
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

# 10. Fond de carte géographique OSM clair (mode carte uniquement ; le mode
#     Schéma est volontairement dé-cartographié, sans fond géographique).
if not schema_mode:
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

def render_interactive_map(figure, height=760):
    """Affiche la figure comme une image avec zoom/déplacement interactifs côté
    navigateur : molette pour zoomer sous le curseur, glisser pour se déplacer,
    double-clic pour réinitialiser. Plus dynamique et naturel que des curseurs."""
    buf = io.BytesIO()
    figure.savefig(buf, format="png", dpi=150, bbox_inches='tight')
    b64 = base64.b64encode(buf.getvalue()).decode()
    # Gabarit avec marqueurs textuels (pas de %-formatting : le HTML/CSS/JS
    # contient des « % » et des « { } » qui casseraient str.format ou l'opérateur %).
    html = """
    <div id="vp" style="width:100%;height:__H__px;overflow:hidden;position:relative;
         border:1px solid #ddd;border-radius:8px;background:#fff;cursor:grab;touch-action:none;">
      <img id="mapimg" src="data:image/png;base64,__B64__" draggable="false"
           style="position:absolute;top:0;left:0;width:100%;height:100%;object-fit:contain;
                  transform-origin:0 0;user-select:none;-webkit-user-drag:none;"/>
    </div>
    <div style="font-size:12px;color:#666;margin-top:4px;">
      Molette = zoom · glisser = déplacer · double-clic = réinitialiser
    </div>
    <script>
    (function(){
      var vp=document.getElementById('vp'), img=document.getElementById('mapimg');
      var scale=1, tx=0, ty=0, panning=false, sx=0, sy=0;
      function apply(){ img.style.transform='translate('+tx+'px,'+ty+'px) scale('+scale+')'; }
      vp.addEventListener('wheel', function(e){
        e.preventDefault();
        var r=vp.getBoundingClientRect(), mx=e.clientX-r.left, my=e.clientY-r.top;
        var f=(e.deltaY<0)?1.12:1/1.12, ns=Math.min(25,Math.max(1,scale*f)), k=ns/scale;
        tx=mx-k*(mx-tx); ty=my-k*(my-ty); scale=ns;
        if(scale<=1){ scale=1; tx=0; ty=0; }
        apply();
      }, {passive:false});
      vp.addEventListener('mousedown', function(e){ panning=true; sx=e.clientX-tx; sy=e.clientY-ty; vp.style.cursor='grabbing'; });
      window.addEventListener('mouseup', function(){ panning=false; vp.style.cursor='grab'; });
      window.addEventListener('mousemove', function(e){ if(!panning)return; tx=e.clientX-sx; ty=e.clientY-sy; apply(); });
      vp.addEventListener('dblclick', function(){ scale=1; tx=0; ty=0; apply(); });
    })();
    </script>
    """
    html = html.replace("__H__", str(int(height))).replace("__B64__", b64)
    components.html(html, height=height + 40)


# --- Affichage Streamlit : carte et légende côte à côte, sans superposition ---
col_map, col_leg = st.columns([4, 1])
with col_map:
    render_interactive_map(fig)
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
