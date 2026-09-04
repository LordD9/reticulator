import io
from collections import deque
import streamlit as st
import streamlit.components.v1 as components
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon, Patch
from matplotlib.lines import Line2D
import networkx as nx
import pandas as pd
from shapely.geometry import LineString, Point, box
from shapely.strtree import STRtree
from shapely.affinity import translate
import contextily as cx
import math

# Import depuis le script existant
from schema_reticulaire import (
    charger_donnees,
    construire_graphe,
    voisinages_stricts,
    noms_regions,
    PALETTE_OD,
    CRS_METRIC,
    inserer_gare_sur_reseau,
    retirer_gare_sur_reseau,
    signes_offset_corridor,
    offsets_faisceau_schematique,
)

# --- Palette de couleurs de mission, choisie à la main par l'utilisateur ---
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

st.set_page_config(page_title="Reticulator - Générateur Interactif", layout="wide")

@st.cache_data(show_spinner=False)
def load_and_build_graph(perimetre):
    gares_data, reseau_clip = charger_donnees(perimetre)
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

# --- PÉRIMÈTRE GÉOGRAPHIQUE ---
# Choix du périmètre AVANT le chargement : conditionne les données mises en cache.
st.sidebar.title("🚄 Reticulator")
st.sidebar.subheader("🗺️ Périmètre géographique")
_REGIONS = noms_regions()
_DEFAUT_REGION = "Provence-Alpes-Côte d'Azur"
mode_perimetre = st.sidebar.radio(
    "Gares prises en compte",
    ["Régional", "France entière"],
    index=0,
    help="Régional : gares du/des département(s) de la région choisie "
         "(regions_departements.json). France entière : toutes les gares de "
         "gare.geojson — le premier chargement du graphe national peut être long.",
)
if mode_perimetre == "France entière":
    perimetre = "national"
    st.sidebar.warning(
        "Périmètre national : construction du graphe potentiellement longue et "
        "gourmande en mémoire au premier chargement. Certaines gares peuvent "
        "manquer au routage automatique — ajoutez-les à la main dans les missions."
    )
else:
    idx_def = _REGIONS.index(_DEFAUT_REGION) if _DEFAUT_REGION in _REGIONS else 0
    perimetre = st.sidebar.selectbox(
        "Région", _REGIONS, index=idx_def,
        help="Filtre les gares par département (code INSEE) de la région choisie.",
    )

try:
    with st.spinner("Chargement et construction du graphe géographique..."):
        gares_data, reseau_clip, station_graph, gares_dict, neighbors = load_and_build_graph(perimetre)
except Exception as e:
    st.error(f"Erreur lors du chargement des données. Vérifiez la présence de gare.geojson et regions_departements.json. Détail : {e}")
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
st.sidebar.markdown("---")
st.sidebar.subheader("🚄 Paramétrage des 8 OD")

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

# Messages d'édition de trajet : affichés hors des expanders (repliés par défaut).
for _i in range(len(st.session_state.ods)):
    flash = st.session_state.pop(f"route_msg_{_i}", None)
    if flash:
        kind, text = flash
        getattr(st.sidebar, kind)(f"Mission {_i + 1} : {text}")

for i, od in enumerate(st.session_state.ods):
    with st.sidebar.expander(f"Mission {i+1} - {format_station(od['depart'])} ➔ {format_station(od['arrivee'])}", expanded=False):
        # --- Choix manuel de la couleur dans une palette ---
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
            od['color'] = st.color_picker(
                "Teinte personnalisée", value=od['color'], key=f"color_{i}")
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
                except nx.NodeNotFound:
                    st.error(
                        "Gare isolée sur le réseau (non reliée par le graphe). "
                        "Construisez le trajet à la main via l'ajustement manuel "
                        "ci-dessous."
                    )
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
            # La clé dépend du trajet : après insertion/retrait (recolle réseau)
            # Streamlit ignore `default` si le widget existe déjà.
            _sk = "|".join(od['steps'])
            served = st.multiselect(
                "Gares desservies (décocher = passage sans arrêt)",
                options=od['steps'],
                default=[s for s in od['served_stations'] if s in od['steps']],
                format_func=format_station,
                key=f"served_{i}_{_sk}"
            )
            od['served_stations'] = served

        # --- Ajustement manuel du trajet (toujours disponible) ---
        # Le plus court chemin peut emprunter le mauvais embranchement : on
        # insère/retire des gares, et chaque édition recolle le trajet sur le
        # graphe (gares intermédiaires du réseau, géométrie des voies).
        st.markdown("**✏️ Ajustement manuel du trajet**")
        st.caption(
            "À une bifurcation, retirez les gares du mauvais embranchement et "
            "insérez celles du bon : le tracé est recollé sur le réseau existant."
        )
        if od['steps']:
            st.caption("Ordre actuel : " +
                       " → ".join(format_station(s) for s in od['steps']))
        else:
            st.caption("Aucune gare : insérez-les une à une pour bâtir le trajet.")
        add_g = st.selectbox(
            "Gare à insérer", [None] + [o[0] for o in station_options],
            format_func=format_station, index=0, key=f"addg_{i}")

        pos_opts = list(range(len(od['steps']) + 1))

        def _fmt_pos(k, _steps=list(od['steps'])):
            if not _steps:
                return "Première gare du trajet"
            if k == 0:
                return f"En tête (avant {format_station(_steps[0])})"
            if k == len(_steps):
                return f"En queue (après {format_station(_steps[-1])})"
            return (f"Entre {format_station(_steps[k-1])} "
                    f"et {format_station(_steps[k])}")

        ins_pos = st.selectbox("Position d'insertion", pos_opts,
                               index=len(od['steps']), format_func=_fmt_pos,
                               key=f"addpos_{i}")
        if st.button("➕ Insérer la gare", key=f"addbtn_{i}", use_container_width=True):
            if not add_g:
                st.warning("Choisissez une gare à insérer.")
            elif add_g in od['steps']:
                st.warning("Cette gare est déjà présente dans le trajet.")
            else:
                old_steps = list(od['steps'])
                new_steps, ok = inserer_gare_sur_reseau(
                    station_graph, od['steps'], add_g, ins_pos)
                od['steps'] = new_steps
                # Conserver le statut « desservie » des gares qui restent ;
                # les gares nouvellement injectées par le graphe sont desservies
                # par défaut (l'utilisateur peut les décocher).
                kept = [s for s in od['served_stations'] if s in new_steps]
                added = [s for s in new_steps if s not in old_steps]
                od['served_stations'] = kept + [s for s in added if s not in kept]
                if od['steps']:
                    od['depart'] = od['steps'][0]
                    od['arrivee'] = od['steps'][-1]
                if ok:
                    n_add = len(added)
                    extra = (f" {n_add} gare(s) du réseau ajoutée(s) sur le trajet."
                             if n_add > 1 else "")
                    st.session_state[f"route_msg_{i}"] = (
                        "success",
                        "Gare insérée, tracé recollé sur le réseau." + extra,
                    )
                else:
                    st.session_state[f"route_msg_{i}"] = (
                        "warning",
                        "Gare insérée, mais aucun itinéraire réseau n'a été trouvé "
                        "de part et d'autre : le tronçon est tracé en ligne droite. "
                        "Insérez une gare intermédiaire pour recoller aux voies.",
                    )
                st.rerun()

        if od['steps']:
            rem_g = st.selectbox(
                "Retirer une gare du trajet", [None] + list(od['steps']),
                format_func=format_station, index=0, key=f"remg_{i}")
            if st.button("➖ Retirer la gare", key=f"rembtn_{i}", use_container_width=True):
                if rem_g:
                    old_steps = list(od['steps'])
                    new_steps, ok = retirer_gare_sur_reseau(
                        station_graph, od['steps'], rem_g)
                    od['steps'] = new_steps
                    kept = [s for s in od['served_stations'] if s in new_steps]
                    added = [s for s in new_steps if s not in old_steps]
                    od['served_stations'] = kept + [s for s in added if s not in kept]
                    if od['steps']:
                        od['depart'] = od['steps'][0]
                        od['arrivee'] = od['steps'][-1]
                    else:
                        od['depart'] = None
                        od['arrivee'] = None
                    if ok:
                        st.session_state[f"route_msg_{i}"] = (
                            "success",
                            "Gare retirée, tracé recollé sur le réseau "
                            "(l'embranchement retiré n'est pas réintroduit).",
                        )
                    else:
                        st.session_state[f"route_msg_{i}"] = (
                            "warning",
                            "Gare retirée, mais aucun itinéraire réseau alternatif "
                            "n'a été trouvé : le tronçon restant est en ligne droite. "
                            "Insérez la gare du bon embranchement pour recoller.",
                        )
                    st.rerun()

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
st.markdown(
    "Ce tableau de bord permet de calculer et superposer jusqu'à 8 relations "
    "ferroviaires. Les traits se décalent automatiquement s'ils partagent les "
    "mêmes voies (offset). **Une gare desservie par une seule mission prend la "
    "couleur de cette mission ; une correspondance (plusieurs missions) est un "
    "carré blanc à contour noir.** Si une gare est décochée (passage sans arrêt), "
    "la ligne passe au-dessus du symbole. L'épaisseur des traits est "
    "proportionnelle à la fréquence ; sur un même faisceau les couleurs sont "
    "jointives, dans un ordre constant."
)
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
    # L'arête peut ne pas exister dans le graphe (gare ajoutée à la main sur un
    # tronçon que le routage automatique a manqué) -> repli ligne droite.
    if station_graph.has_edge(canonical[0], canonical[1]):
        coords = station_graph[canonical[0]][canonical[1]].get('geom_m', [])
    else:
        coords = []
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

def oriented_offset(geom, canonical, od_idx):
    """Décale la géométrie dans le référentiel de corridor (carte ET schéma).

    Mode carte : `offset_curve` (gauche du sens canonique UIC) × le signe
    de corridor, pour que l'empilement ne s'inverse pas d'une gare à l'autre.

    Mode schéma : translation monde calculée par `offsets_faisceau_schematique`
    (rails colinéaires fusionnés, signe d'axe unifié). `offset_curve` sur
    une arête UIC min→max schématique inverse l'ordre dès que deux missions
    occupent le même axe sans partager la même paire de gares."""
    if schema_mode:
        ox, oy = schematic_shift.get((canonical, od_idx), (0.0, 0.0))
        if ox == 0.0 and oy == 0.0:
            return geom
        return translate(geom, ox, oy)
    off = segment_offsets.get((canonical, od_idx), 0.0)
    off = off * segment_sign.get(canonical, 1.0)
    return offset_line(geom, off)


fig, ax = plt.subplots(figsize=(16, 12))
ax.set_aspect('equal')
ax.axis('off')

# Épaisseur du trait proportionnelle à la fréquence (en points), avec un
# minimum lisible. Utilisée partout (tracé, passages, légende) pour rester cohérent.
LW_PER_TPH = 2.0
# Casing extérieur du faisceau (en points), dessiné SOUS les couleurs. Les
# couleurs elles-mêmes sont jointives (pas de liseré blanc entre missions) :
# le blanc n'apparaît que sur le pourtour du faisceau, plus sur les joints.
CASING_PT = 0.9

def freq_to_lw(freq):
    return max(1.2, freq * LW_PER_TPH)

# 1. Identifier les tronçons partagés. L'ordre d'empilement est l'indice de
#    mission (tri croissant) : il doit rester identique d'un tronçon au
#    suivant (direct + omnibus, même chemin, desserte différente).
segment_users = {}
for od in st.session_state.ods:
    if len(od['steps']) < 2:
        continue
    seen_edges = set()
    for i in range(len(od['steps']) - 1):
        canonical = tuple(sorted((od['steps'][i], od['steps'][i + 1])))
        if canonical in seen_edges:
            continue
        seen_edges.add(canonical)
        segment_users.setdefault(canonical, []).append(od['idx'])

# 2. Gares dessinées + positions (géographiques ou schématiques selon le mode).
#    `pos` est la source unique de coordonnées utilisée par tout le rendu.
#    On ne dessine QUE les gares réellement utilisées par au moins une mission
#    tracée (mission d'au moins 2 gares, donc portant un tronçon) : une gare
#    présente dans une mission incomplète ou sur le trajet mais utilisée par
#    aucune mission n'est pas affichée.
drawn_stations = set()
for od in st.session_state.ods:
    if len(od['steps']) < 2:
        continue
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
mfac = 0.14 if schema_mode else 0.08
madd = SCHEMA_UNIT * 0.9 if schema_mode else 7000
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

# 5. Pré-calcul des offsets : sur chaque tronçon partagé, les missions sont
#    empilées côte à côte, JOINTIVES, dans l'ordre croissant des indices
#    (mission 1 toujours du même côté de la mission 2, etc.). Un casing blanc
#    est dessiné sous le faisceau : visible sur le pourtour seulement.
segment_offsets = {}   # (canonical, od_idx) -> offset en mètres (mode carte)
segment_width_m = {}   # canonical -> largeur totale du faisceau (mètres)
schematic_shift = {}   # (canonical, od_idx) -> (ox, oy) (mode schéma)
segment_sign = {}
liste_steps = [od['steps'] for od in st.session_state.ods]
widths_by_od = {
    od['idx']: freq_to_lw(od['freq_tph']) * meters_per_point
    for od in st.session_state.ods
}

if schema_mode:
    schema_geoms = {
        canonical: get_edge_geom(canonical[0], canonical[1])
        for canonical in segment_users
    }
    schematic_shift, segment_width_m = offsets_faisceau_schematique(
        schema_geoms, segment_users, liste_steps, pos, widths_by_od,
        eps=SCHEMA_UNIT * 0.05,
    )
else:
    for canonical, raw_users in segment_users.items():
        users = sorted(raw_users)
        widths_m = [widths_by_od[idx] for idx in users]
        total = sum(widths_m)
        segment_width_m[canonical] = total
        cursor = -total / 2.0
        for idx, w in zip(users, widths_m):
            segment_offsets[(canonical, idx)] = cursor + w / 2.0
            cursor += w
    # Sens d'offset par tronçon, propagé le long des corridors pour que
    # l'ordre d'empilement ne s'inverse pas entre deux gares.
    segment_sign = signes_offset_corridor(liste_steps)

def _line_parts(geom):
    """Itère les LineString d'une géométrie (offset_curve peut renvoyer un Multi)."""
    if geom is None or geom.is_empty:
        return
    if geom.geom_type == 'LineString':
        yield geom
        return
    for g in getattr(geom, 'geoms', []):
        if g.geom_type == 'LineString' and not g.is_empty:
            yield g


# 6. Lignes de mission : casing blanc sous le faisceau (z=3), couleurs jointives
#    (z=4), sans path_effects. Le blanc ne reste visible que sur le pourtour.
painted_lines = []  # (LineString, largeur_m) pour l'anti-collision des étiquettes
for canonical_edge, raw_users in segment_users.items():
    geom = get_edge_geom(canonical_edge[0], canonical_edge[1])
    for od_idx in sorted(raw_users):
        od = st.session_state.ods[od_idx]
        shifted_geom = oriented_offset(geom, canonical_edge, od_idx)
        if shifted_geom.is_empty:
            continue
        lw = freq_to_lw(od['freq_tph'])
        join = 'miter' if schema_mode else 'round'
        for ls in _line_parts(shifted_geom):
            xs, ys = ls.xy
            # Casing en caps plats : un casing rond créerait un bulbe blanc
            # à chaque jointure de tronçon (l'ancien effet « boudin »).
            ax.plot(xs, ys, color='white', linewidth=lw + 2 * CASING_PT, zorder=3,
                    solid_capstyle='butt', solid_joinstyle=join)
            ax.plot(xs, ys, color=od['color'], linewidth=lw, zorder=4,
                    solid_capstyle='round', solid_joinstyle=join)
            painted_lines.append((ls, lw * meters_per_point))

# 7. Gares (Z-order: 5) — style inspiré du plan de métro RATP :
#    - desservie par UNE mission : carré de la couleur de la mission ;
#    - desservie par PLUSIEURS missions : carré blanc, contour noir ;
#    - passage sans arrêt uniquement : pas de symbole (la ligne traverse).
#    Le carré est orienté sur le faisceau et dimensionné pour le couvrir.
incident_segments = {}   # sid -> [canonical, ...] segments touchant la gare
station_bundle = {}      # sid -> largeur du faisceau le plus large (mètres)
station_served_by = {}   # sid -> [od_idx, ...] missions qui DESSERVENT la gare
for canonical in segment_users:
    for sid in canonical:
        incident_segments.setdefault(sid, []).append(canonical)
        station_bundle[sid] = max(station_bundle.get(sid, 0.0), segment_width_m[canonical])
for od in st.session_state.ods:
    if len(od['steps']) < 2:
        continue
    for sid in od['served_stations']:
        if sid in drawn_stations:
            station_served_by.setdefault(sid, []).append(od['idx'])


def station_axis(sid):
    """Direction unitaire (dx, dy) du faisceau de missions le plus large incident
    à la gare, servant à orienter le carré sur les traits.
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


min_sq = meters_per_point * 8       # côté mini d'un carré de gare
station_rects = {}  # sid -> (rx0, ry0, rx1, ry1) bbox englobante (anti-collision)
for sid in drawn_stations:
    x, y = pos[sid]
    served = station_served_by.get(sid, [])
    dx, dy = station_axis(sid)
    nx_, ny_ = -dy, dx
    if served:
        # Carré un peu plus large que le faisceau pour que le contour reste visible.
        side = max(min_sq, station_bundle.get(sid, 0.0) + meters_per_point * 2.5)
        if len(served) == 1:
            face, edge, elw = st.session_state.ods[served[0]]['color'], '#111111', 1.1
        else:
            face, edge, elw = '#FFFFFF', '#111111', 1.7
        hs = side / 2.0
        corners = [
            (x + hs * dx + hs * nx_, y + hs * dy + hs * ny_),
            (x + hs * dx - hs * nx_, y + hs * dy - hs * ny_),
            (x - hs * dx - hs * nx_, y - hs * dy - hs * ny_),
            (x - hs * dx + hs * nx_, y - hs * dy + hs * ny_),
        ]
        poly = Polygon(corners, closed=True, facecolor=face, edgecolor=edge,
                       linewidth=elw, zorder=5, joinstyle='miter')
        poly.set_clip_on(True)
        ax.add_patch(poly)
        xs_c = [c[0] for c in corners]
        ys_c = [c[1] for c in corners]
        station_rects[sid] = (min(xs_c), min(ys_c), max(xs_c), max(ys_c))
    else:
        # Passage sans arrêt : pas de symbole, juste une emprise pour les labels.
        pad = meters_per_point * 4
        station_rects[sid] = (x - pad, y - pad, x + pad, y + pad)

# 8. Passage sans arrêt AU-DESSUS d'une gare desservie par d'autres missions :
#    on redessine un extrait LOCAL (rayon = côté du carré, pas un km) par-dessus
#    le symbole, SANS casing blanc — c'est ce casing + un rayon trop large qui
#    produisait l'effet « boudin ».
for od in st.session_state.ods:
    if len(od['steps']) < 2:
        continue
    for i in range(len(od['steps']) - 1):
        u = od['steps'][i]
        v = od['steps'][i + 1]
        canonical = tuple(sorted((u, v)))
        shifted_geom = oriented_offset(
            get_edge_geom(canonical[0], canonical[1]),
            canonical, od['idx'])
        lw = freq_to_lw(od['freq_tph'])
        for sid in (u, v):
            if sid in od['served_stations']:
                continue
            if sid not in station_served_by:
                continue  # personne ne s'arrête : pas de symbole à recouvrir
            rx0, ry0, rx1, ry1 = station_rects[sid]
            catch_radius = 0.55 * max(rx1 - rx0, ry1 - ry0)
            station_pt = Point(*pos[sid])
            local_seg = shifted_geom.intersection(station_pt.buffer(catch_radius))
            if local_seg.is_empty:
                continue
            for ls in _line_parts(local_seg):
                xs, ys = ls.xy
                ax.plot(xs, ys, color=od['color'], linewidth=lw, zorder=6,
                        solid_capstyle='butt', solid_joinstyle='miter')

# 9. Étiquettes de gares avec anti-collision (gares + tracés) et trait de rappel.
#    - Mode carte : seules les gares de type a/b sont nommées (lisibilité du fond).
#    - Mode Schéma : TOUTES les gares sont nommées, taille de police selon le type
#      (a > b > c > autre). Les couleurs de gare ne dépendent plus du type.
def _overlap(a, b):
    return not (a[2] <= b[0] or a[0] >= b[2] or a[3] <= b[1] or a[1] >= b[3])


def _overlap_area(a, b):
    """Aire de recouvrement de deux bboxes (0 si disjointes) — sert à choisir la
    position de repli la moins conflictuelle quand aucune place libre n'existe."""
    dx = min(a[2], b[2]) - max(a[0], b[0])
    dy = min(a[3], b[3]) - max(a[1], b[1])
    if dx <= 0 or dy <= 0:
        return 0.0
    return dx * dy


def _unit(vx, vy):
    n = math.hypot(vx, vy)
    if n < 1e-9:
        return (1.0, 0.0)
    return (vx / n, vy / n)


def _exit_dist(hw, hh, ux, uy):
    """Distance du centre au bord d'une AABB le long du vecteur unitaire (ux, uy)."""
    tx = hw / abs(ux) if abs(ux) > 1e-9 else float('inf')
    ty = hh / abs(uy) if abs(uy) > 1e-9 else float('inf')
    d = min(tx, ty)
    return d if math.isfinite(d) else hw


# Tampons des tracés : une étiquette ne doit pas recouvrir le faisceau.
line_buffers = []
pad_line = meters_per_point * 2.0
for g, w_m in painted_lines:
    try:
        buf = g.buffer(w_m / 2.0 + pad_line)
        if buf is not None and not buf.is_empty:
            line_buffers.append(buf)
    except Exception:
        continue
line_tree = STRtree(line_buffers) if line_buffers else None


def _hits_lines(bbox):
    if line_tree is None:
        return False
    poly = box(bbox[0], bbox[1], bbox[2], bbox[3])
    cand = line_tree.query(poly)
    for i in cand:
        if poly.intersects(line_buffers[int(i)]):
            return True
    return False


# Limites internes : on garde une petite marge pour rester dans le cadre.
bound_m = meters_per_point * 2
lim = (x0 + bound_m, y0 + bound_m, x1 - bound_m, y1 - bound_m)

# bboxes déjà occupées : emprises des gares (carrés ou points de passage).
occupied = list(station_rects.values())

# Taille de police par type de gare (inchangé : le type pilote le nom, pas la couleur).
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

gap_lbl = meters_per_point * 5
cx_map = (minx + maxx) / 2.0
cy_map = (miny + maxy) / 2.0


def _label_dirs(sid, x, y):
    """Directions candidates : d'abord perpendiculaire au tracé (côté extérieur
    du schéma), puis le long du tracé, puis les diagonales."""
    dx, dy = station_axis(sid)
    px, py = -dy, dx
    ox, oy = x - cx_map, y - cy_map

    def pair(ux, uy):
        ux, uy = _unit(ux, uy)
        if ox * ux + oy * uy >= 0:
            return [(ux, uy), (-ux, -uy)]
        return [(-ux, -uy), (ux, uy)]

    dirs = []
    dirs.extend(pair(px, py))
    dirs.extend(pair(dx, dy))
    dirs.extend(pair(px + dx, py + dy))
    dirs.extend(pair(px - dx, py - dy))
    return dirs


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
    max_r = 10 if schema_mode else 7
    directions = _label_dirs(sid, x, y)
    for r in range(1, max_r):
        for di, (ux, uy) in enumerate(directions):
            off = (_exit_dist(half_rw, half_rh, ux, uy)
                   + _exit_dist(text_w / 2, text_h / 2, ux, uy)
                   + gap_lbl) * r
            cx_l = x + ux * off
            cy_l = y + uy * off
            bbox = (cx_l - text_w / 2, cy_l - text_h / 2,
                    cx_l + text_w / 2, cy_l + text_h / 2)
            if bbox[0] < lim[0] or bbox[1] < lim[1] or bbox[2] > lim[2] or bbox[3] > lim[3]:
                continue
            if any(_overlap(bbox, o) for o in occupied):
                continue
            if _hits_lines(bbox):
                continue
            placed = (cx_l, cy_l, bbox)
            is_default = (r == 1 and di == 0)
            break
        if placed:
            break

    if placed is None:
        # Repli : grille LOCALE autour de la gare (pas tout le cadre : trop
        # coûteux, et une étiquette trop loin n'est plus lisible).
        step = max(text_h * 0.7, meters_per_point * 6)
        search_r = max(text_w, text_h) * (12 if schema_mode else 8)
        gx0 = max(lim[0] + text_w / 2, x - search_r)
        gx1 = min(lim[2] - text_w / 2, x + search_r)
        gy0 = max(lim[1] + text_h / 2, y - search_r)
        gy1 = min(lim[3] - text_h / 2, y + search_r)
        best_free = None
        best_free_d = None
        best_any = None
        best_ov = None
        gx = gx0
        while gx <= gx1:
            gy = gy0
            while gy <= gy1:
                bbox = (gx - text_w / 2, gy - text_h / 2,
                        gx + text_w / 2, gy + text_h / 2)
                ov = sum(_overlap_area(bbox, o) for o in occupied)
                if _hits_lines(bbox):
                    ov += text_w * text_h
                d = (gx - x) ** 2 + (gy - y) ** 2
                if ov == 0.0:
                    if best_free_d is None or d < best_free_d:
                        best_free_d = d
                        best_free = (gx, gy, bbox)
                if best_ov is None or ov < best_ov:
                    best_ov = ov
                    best_any = (gx, gy, bbox)
                gy += step
            gx += step
        placed = best_free if best_free is not None else best_any
        if placed is None:
            # Cadre plus étroit que l'étiquette : clamp simple à droite de la gare.
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
            bbox=dict(facecolor='white', alpha=0.82, edgecolor='none', boxstyle='round,pad=0.2'))
    occupied.append(bbox)

# 10. Fond de carte géographique OSM clair (mode carte uniquement ; le mode
#     Schéma est volontairement dé-cartographié, sans fond géographique).
if not schema_mode:
    try:
        # Carto Positron exige désormais une clé API (tuiles « API KEY REQUIRED »).
        # Esri WorldGrayCanvas offre un fond clair équivalent, sans clé ; repli OSM.
        _basemap_ok = False
        for _src in (cx.providers.Esri.WorldGrayCanvas,
                     cx.providers.OpenStreetMap.Mapnik):
            try:
                cx.add_basemap(ax, crs=CRS_METRIC, source=_src, alpha=0.5, zorder=0)
                _basemap_ok = True
                break
            except Exception:
                continue
        if not _basemap_ok:
            raise RuntimeError("aucun fournisseur de tuiles n'a répondu")
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
                label=lbl))
    sample_c = next((od['color'] for od in st.session_state.ods
                     if len(od['steps']) >= 2), '#4E79A7')
    gare_handles = [
        Patch(facecolor=sample_c, edgecolor='#111111',
              label="Gare desservie (une mission)"),
        Patch(facecolor='#FFFFFF', edgecolor='#111111', linewidth=1.5,
              label="Correspondance (plusieurs missions)"),
    ]
    n_rows = len(mission_handles) + len(gare_handles) + 2
    # Largeur adaptée au libellé le plus long pour éviter toute troncature.
    max_len = max([len(l) for l in labels] + [len("Correspondance (plusieurs missions)"), 20])
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
                title="Gares", frameon=False, fontsize=10,
                title_fontproperties={'weight': 'bold', 'size': 11})
    return fig_l

fig_legend = build_legend_figure()

def render_interactive_map(figure, height=760):
    """Affiche la figure en SVG (vectoriel) avec zoom/déplacement interactifs côté
    navigateur : molette pour zoomer sous le curseur, glisser pour se déplacer,
    double-clic pour réinitialiser.

    Le rendu est vectoriel : en zoomant, le navigateur re-rastérise réellement le
    tracé à la nouvelle échelle (traits, gares et étiquettes restent nets), au lieu
    d'agrandir les pixels d'une image figée. On zoome donc « pour de vrai »."""
    buf = io.StringIO()
    figure.savefig(buf, format="svg", bbox_inches='tight')
    svg = buf.getvalue()
    # On isole la balise <svg …> (retrait de l'entête XML/DOCTYPE) puis on lui
    # injecte un id + un style pour qu'elle remplisse le cadre et accepte le
    # transform CSS piloté en JS. Les largeurs/hauteurs en pt de matplotlib sont
    # surchargées par le style (width/height 100 %), le viewBox gère le ratio.
    start = svg.find('<svg')
    if start > 0:
        svg = svg[start:]
    svg = svg.replace(
        '<svg ',
        '<svg id="mapimg" preserveAspectRatio="xMidYMid meet" '
        'style="position:absolute;top:0;left:0;width:100%;height:100%;'
        'transform-origin:0 0;user-select:none;" ',
        1,
    )
    # Gabarit avec marqueurs textuels (pas de %-formatting : le HTML/CSS/JS
    # contient des « % » et des « { } » qui casseraient str.format ou l'opérateur %).
    html = """
    <div id="vp" style="width:100%;height:__H__px;overflow:hidden;position:relative;
         border:1px solid #ddd;border-radius:8px;background:#fff;cursor:grab;touch-action:none;">
      __SVG__
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
        var f=(e.deltaY<0)?1.12:1/1.12, ns=Math.min(40,Math.max(1,scale*f)), k=ns/scale;
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
    html = html.replace("__H__", str(int(height))).replace("__SVG__", svg)
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
