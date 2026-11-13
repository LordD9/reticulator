import io
import json
from collections import deque
import streamlit as st
import streamlit.components.v1 as components
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Polygon, Patch
from matplotlib.lines import Line2D
import networkx as nx
import pandas as pd
from shapely.geometry import LineString, Point, box
from shapely.ops import nearest_points
from shapely.strtree import STRtree
from shapely.affinity import translate
import math

# Import depuis le script existant
from branding import LOGO_PATH, composer_pdf_depuis_png, composer_png_avec_logo
from dessin_gares import (
    capsule_polygon,
    contourne_obstacle,
    directions_alignees,
    emprise_gare,
    offset_line,
    trace_coude,
    wrap_nom_gare,
)
from geo_view import folium_map_html, fournisseurs_tuiles
from missions_io import (
    N_MISSIONS,
    mission_vide,
    enregistrer_preset,
    exporter_missions,
    importer_missions,
    lister_presets,
)
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

st.set_page_config(
    page_title="ReticuFer — Générateur de schéma",
    layout="wide",
    page_icon=str(LOGO_PATH) if LOGO_PATH.is_file() else None,
)

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
        # Position dessinée = projection de la gare sur la voie (celle du
        # nœud du graphe, d'où partent les géométries d'arêtes). Le point
        # brut du geojson est souvent le bâtiment voyageurs, à côté des voies.
        snap = snap_info.get(uid)
        x_m = snap["proj_x"] if snap else row.geometry.x
        y_m = snap["proj_y"] if snap else row.geometry.y
        gares_dict[uid] = {
            'nom': row['nom_gare'],
            'type': str(row['type_gare']).lower(),
            'x_m': x_m,
            'y_m': y_m,
        }
            
    return gares_data, reseau_clip, station_graph, gares_dict, neighbors

# --- BARRE LATÉRALE : périmètre, affichage, missions, sauvegarde ---
# Ordre de lecture = ordre d'usage : où (périmètre), comment (affichage),
# quoi (missions), puis sauvegarde. Le choix du périmètre précède le
# chargement : il conditionne les données mises en cache.
if LOGO_PATH.is_file():
    st.sidebar.image(str(LOGO_PATH), width=220)
st.sidebar.title("ReticuFer")

st.sidebar.subheader("🗺️ Périmètre")
_REGIONS = noms_regions()
_DEFAUT_REGION = "Provence-Alpes-Côte d'Azur"
mode_perimetre = st.sidebar.radio(
    "Gares prises en compte",
    ["Régional", "France entière"],
    index=0,
    horizontal=True,
    label_visibility="collapsed",
    help="Régional : gares du/des département(s) de la région choisie "
         "(regions_departements.json). France entière : toutes les gares de "
         "gare.geojson — le premier chargement du graphe national peut être long.",
)
if mode_perimetre == "France entière":
    perimetre = "national"
    st.sidebar.warning(
        "Périmètre national : premier chargement long et gourmand en mémoire. "
        "Certaines gares peuvent manquer au routage automatique — ajoutez-les "
        "à la main dans l'ajustement du trajet."
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
    st.session_state.ods = [mission_vide(i, PALETTE_OD[i]) for i in range(N_MISSIONS)]

# Fonds de plan : Plan IGN par défaut (Géoplateforme, sans clé, pas de 403
# comme les tuiles OSM pour les usages automatisés). OSM reste en option.
FONDS = {"Plan IGN": "ign", "OpenStreetMap": "osm"}

st.sidebar.subheader("🖼️ Affichage")
_vue = st.sidebar.segmented_control(
    "Rendu", ["Carte", "Schéma"], default="Carte", required=True,
    key="vue_rendu",
    help="Carte : tracés réels sur fond de plan. Schéma : angles droits, gares "
         "alignées, espacement régularisé, toutes les gares nommées. L'affichage "
         "ET l'export reprennent la vue choisie.",
)
schema_mode = _vue == "Schéma"
fond_carte = "ign"
if not schema_mode:
    _choix_fond = st.sidebar.radio(
        "Fond de plan", list(FONDS), index=0, horizontal=True,
        help="Utilisé par la carte interactive ET par les exports PNG/PDF. "
             "Commutable aussi dans le contrôle de couches de la carte.",
    )
    fond_carte = FONDS[_choix_fond]

station_options = [(k, v['nom']) for k, v in gares_dict.items()]
station_options.sort(key=lambda x: x[1])
station_ids = [o[0] for o in station_options]


def format_station(uic):
    if not uic:
        return "Aucune"
    return gares_dict.get(uic, {}).get('nom', uic)


def libelle_mission(od):
    if len(od['steps']) < 2:
        return f"{od['idx'] + 1} · (vide)"
    return (f"{od['idx'] + 1} · {format_station(od['depart'])} ➔ "
            f"{format_station(od['arrivee'])}")


def _maj_extremites(od):
    if od['steps']:
        od['depart'], od['arrivee'] = od['steps'][0], od['steps'][-1]
    else:
        od['depart'] = od['arrivee'] = None


def editeur_mission(i, od):
    """Formulaire d'une mission (rendu dans la barre latérale)."""
    gen = st.session_state.get("gen_missions", 0)
    palette_names = list(MISSION_PALETTE.keys())
    opts_couleur = palette_names + ["Personnalisé…"]
    current_name = next((n for n, h in MISSION_PALETTE.items()
                         if h.lower() == str(od['color']).lower()), "Personnalisé…")
    c1, c2 = st.columns([3, 2])
    with c1:
        choix = st.selectbox("Couleur", opts_couleur,
                             index=opts_couleur.index(current_name), key=f"palette_{i}_{gen}")
    with c2:
        if choix == "Personnalisé…":
            od['color'] = st.color_picker("Teinte", value=od['color'], key=f"color_{i}_{gen}")
        else:
            od['color'] = MISSION_PALETTE[choix]
            st.markdown(
                f"<div style='margin-top:30px;height:22px;border-radius:11px;"
                f"background:{od['color']}'></div>",
                unsafe_allow_html=True,
            )

    idx_dep = station_ids.index(od['depart']) + 1 if od['depart'] in station_ids else 0
    idx_arr = station_ids.index(od['arrivee']) + 1 if od['arrivee'] in station_ids else 0
    dep = st.selectbox("Origine", [None] + station_ids, format_func=format_station,
                       index=idx_dep, key=f"dep_{i}_{gen}", placeholder="Rechercher une gare…")
    arr = st.selectbox("Destination", [None] + station_ids, format_func=format_station,
                       index=idx_arr, key=f"arr_{i}_{gen}", placeholder="Rechercher une gare…")
    od['freq_tph'] = st.number_input(
        "Fréquence (trains/heure)", min_value=0.1, max_value=20.0,
        value=float(od['freq_tph']), step=0.1, key=f"freq_{i}_{gen}",
        help="Pilote l'épaisseur du trait et la taille des ronds de gare.")

    b1, b2 = st.columns(2)
    with b1:
        calc = st.button("Calculer", key=f"calc_{i}_{gen}", type="primary",
                         use_container_width=True,
                         disabled=not (dep and arr and dep != arr))
    with b2:
        effacer = st.button("Effacer", key=f"clear_{i}_{gen}", use_container_width=True,
                            disabled=not od['steps'])
    if calc:
        try:
            path = nx.shortest_path(station_graph, source=dep, target=arr, weight='weight')
            od['steps'] = path
            od['served_stations'] = path.copy()  # tout desservi par défaut
            _maj_extremites(od)
            st.session_state[f"route_msg_{i}"] = ("success", "Trajet calculé.")
            st.rerun()
        except nx.NodeNotFound:
            st.error("Gare isolée sur le réseau (non reliée par le graphe). "
                     "Construisez le trajet à la main via l'ajustement ci-dessous.")
        except nx.NetworkXNoPath:
            st.error("Aucun chemin trouvé sur le réseau actuel.")
    if effacer:
        od['steps'], od['served_stations'] = [], []
        _maj_extremites(od)
        st.rerun()

    if od['steps']:
        # La clé dépend du trajet : après insertion/retrait (recollage réseau)
        # Streamlit ignorerait `default` si le widget existait déjà.
        _sk = "|".join(od['steps'])
        with st.expander(f"Desserte ({len(od['served_stations'])}/{len(od['steps'])} gares)",
                         expanded=False):
            od['served_stations'] = st.multiselect(
                "Gares desservies (décocher = passage sans arrêt)",
                options=od['steps'],
                default=[s for s in od['served_stations'] if s in od['steps']],
                format_func=format_station,
                key=f"served_{i}_{gen}_{_sk}",
            )

    # Ajustement manuel : le plus court chemin peut prendre le mauvais
    # embranchement. Chaque édition recolle le trajet sur le graphe.
    with st.expander("✏️ Ajuster le trajet", expanded=not od['steps']):
        if od['steps']:
            st.caption("Ordre actuel : " + " → ".join(format_station(s) for s in od['steps']))
        else:
            st.caption("Aucune gare : insérez-les une à une pour bâtir le trajet.")
        add_g = st.selectbox("Gare à insérer", [None] + station_ids,
                             format_func=format_station, index=0, key=f"addg_{i}_{gen}",
                             placeholder="Rechercher une gare…")
        pos_opts = list(range(len(od['steps']) + 1))

        def _fmt_pos(k, _steps=list(od['steps'])):
            if not _steps:
                return "Première gare du trajet"
            if k == 0:
                return f"En tête (avant {format_station(_steps[0])})"
            if k == len(_steps):
                return f"En queue (après {format_station(_steps[-1])})"
            return f"Entre {format_station(_steps[k-1])} et {format_station(_steps[k])}"

        ins_pos = st.selectbox("Position", pos_opts, index=len(od['steps']),
                               format_func=_fmt_pos, key=f"addpos_{i}_{gen}")
        if st.button("➕ Insérer", key=f"addbtn_{i}_{gen}", use_container_width=True,
                     disabled=not add_g):
            if add_g in od['steps']:
                st.warning("Cette gare est déjà présente dans le trajet.")
            else:
                old_steps = list(od['steps'])
                new_steps, ok = inserer_gare_sur_reseau(station_graph, od['steps'], add_g, ins_pos)
                od['steps'] = new_steps
                # Statut « desservie » conservé ; les gares injectées par le
                # graphe sont desservies par défaut.
                kept = [s for s in od['served_stations'] if s in new_steps]
                added = [s for s in new_steps if s not in old_steps]
                od['served_stations'] = kept + [s for s in added if s not in kept]
                _maj_extremites(od)
                if ok:
                    extra = (f" {len(added)} gare(s) du réseau ajoutée(s)."
                             if len(added) > 1 else "")
                    st.session_state[f"route_msg_{i}"] = (
                        "success", "Gare insérée, tracé recollé sur le réseau." + extra)
                else:
                    st.session_state[f"route_msg_{i}"] = (
                        "warning",
                        "Gare insérée sans itinéraire réseau : tronçon en ligne droite. "
                        "Insérez une gare intermédiaire pour recoller aux voies.")
                st.rerun()

        if od['steps']:
            rem_g = st.selectbox("Gare à retirer", [None] + list(od['steps']),
                                 format_func=format_station, index=0, key=f"remg_{i}_{gen}")
            if st.button("➖ Retirer", key=f"rembtn_{i}_{gen}", use_container_width=True,
                         disabled=not rem_g):
                old_steps = list(od['steps'])
                new_steps, ok = retirer_gare_sur_reseau(station_graph, od['steps'], rem_g)
                od['steps'] = new_steps
                kept = [s for s in od['served_stations'] if s in new_steps]
                added = [s for s in new_steps if s not in old_steps]
                od['served_stations'] = kept + [s for s in added if s not in kept]
                _maj_extremites(od)
                if ok:
                    st.session_state[f"route_msg_{i}"] = (
                        "success", "Gare retirée, tracé recollé (embranchement non réintroduit).")
                else:
                    st.session_state[f"route_msg_{i}"] = (
                        "warning",
                        "Gare retirée sans itinéraire alternatif : tronçon en ligne droite. "
                        "Insérez la gare du bon embranchement pour recoller.")
                st.rerun()


def remplacer_missions(ods, warns):
    """Remplace les missions (import / preset). Les widgets d'édition gardent
    sinon leur ancien état et réécrivent couleur et fréquence de la mission
    affichée par-dessus les valeurs importées : on change de génération de
    clés de widgets."""
    st.session_state.gen_missions = st.session_state.get("gen_missions", 0) + 1
    st.session_state.ods = ods
    st.session_state._missions_import_flash = (warns, True)
    st.rerun()


st.sidebar.subheader(f"🚄 Missions ({N_MISSIONS} max)")
# Une seule mission éditée à la fois : plus de pile de 8 volets à ouvrir.
active = st.sidebar.selectbox(
    "Mission à éditer", list(range(len(st.session_state.ods))),
    format_func=lambda k: libelle_mission(st.session_state.ods[k]),
    key=f"mission_active_{st.session_state.get('gen_missions', 0)}",
)
for _i in range(len(st.session_state.ods)):
    flash = st.session_state.pop(f"route_msg_{_i}", None)
    if flash:
        kind, text = flash
        getattr(st.sidebar, kind)(f"Mission {_i + 1} : {text}")
with st.sidebar:
    editeur_mission(active, st.session_state.ods[active])

# --- Sauvegarde / import des missions ---
with st.sidebar.expander("💾 Sauvegarde et import", expanded=False):
    _payload = json.dumps(exporter_missions(st.session_state.ods), ensure_ascii=False, indent=2)
    st.download_button(
        "Télécharger les missions (JSON)",
        data=_payload.encode("utf-8"),
        file_name="missions_reticufer.json",
        mime="application/json",
        use_container_width=True,
    )
    _preset_nom = st.text_input("Nom du preset (dossier missions/)", value="",
                                placeholder="ex. cote-bleue")
    if st.button("Enregistrer dans missions/<région>/", use_container_width=True):
        try:
            _p = enregistrer_preset(st.session_state.ods, perimetre, _preset_nom or "missions")
            st.success(f"Preset : {_p.parent.name}/{_p.name}")
        except OSError as _e:
            st.error(f"Écriture impossible : {_e}")
    _presets = lister_presets(perimetre)
    if _presets:
        _choix_p = st.selectbox("Preset de la région", [None] + _presets,
                                format_func=lambda p: "—" if p is None else p.stem,
                                key="preset_region")
        if _choix_p is not None and st.button("Charger le preset", use_container_width=True):
            try:
                _ods, _warns = importer_missions(
                    _choix_p.read_text(encoding="utf-8"),
                    gares_connues=set(gares_dict.keys()),
                    couleurs_defaut=list(PALETTE_OD),
                )
            except Exception as _e:
                st.error(f"Preset illisible : {_e}")
            else:
                remplacer_missions(_ods, _warns)
    _up = st.file_uploader("Importer un fichier missions", type=["json"],
                           help="Remplace les missions. Les gares hors périmètre "
                                "actuel sont ignorées.")
    if _up is not None:
        _raw = _up.getvalue()
        _sig = hash(_raw)
        if st.session_state.get("_missions_import_sig") != _sig:
            try:
                _ods, _warns = importer_missions(
                    _raw, gares_connues=set(gares_dict.keys()),
                    couleurs_defaut=list(PALETTE_OD),
                )
            except Exception as _e:
                st.error(f"Import impossible : {_e}")
            else:
                st.session_state._missions_import_sig = _sig
                remplacer_missions(_ods, _warns)

_flash = st.session_state.pop("_missions_import_flash", None)
if _flash:
    _warns, _ok = _flash
    if _ok:
        st.sidebar.success("Missions importées.")
    for _w in _warns:
        st.sidebar.warning(_w)

# --- ZONE PRINCIPALE ---
st.title("Générateur de schéma réticulaire")
st.caption("Cerema — Chronofer / ReticuFer · "
           + ("vue schéma" if schema_mode else f"vue carte, fond {_choix_fond}"))
with st.expander("Comment lire le schéma ?", expanded=False):
    st.markdown(
        "- Jusqu'à 8 missions superposées ; sur un même faisceau les couleurs "
        "sont **jointives**, dans un ordre constant.\n"
        "- **Épaisseur du trait et taille du rond ∝ fréquence.**\n"
        "- Gare desservie par **une** mission : rond de sa couleur. "
        "**Correspondance** (plusieurs missions) : capsule blanche type RATP.\n"
        "- Passage sans arrêt : le trait contourne le symbole, toujours du même côté.\n"
        "- Carte interactive : molette = zoom, traits et gares gardent leur taille."
    )
toolbar = st.container()

# Espacement schématique de référence (unités ~métriques pour rester cohérent
# avec les marges et conversions mètres<->points existantes).
SCHEMA_UNIT = 12000.0


def schematic_edge_geom(a, b):
    """Tronçon schématique entre deux gares : segment droit si elles partagent une
    ligne (même X ou même Y), sinon un coude à angle droit qui prolonge les
    rails existants (voir `trace_coude`). Déterministe pour une paire
    canonique donnée -> les tronçons partagés restent superposables."""
    eps = SCHEMA_UNIT * 1e-3
    return LineString(trace_coude(pos[a], pos[b], schema_dirs.get(a, set()),
                                  schema_dirs.get(b, set()), eps))


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
        # Périurbain (petites distances) : plancher haut pour que chaque gare
        # ait de la place pour son nom. Intercité : compression log, pour que
        # les longs sauts ne bouffent pas tout le cadre (sinon le périurbain
        # devient illisible au zoom global).
        r = max(float(d), 1.0) / med
        raw = 0.92 + 0.28 * math.log1p(r)
        return SCHEMA_UNIT * min(1.30, max(0.95, raw))

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

    # Garantit un écart min entre gares voisines (noms lisibles en périurbain).
    min_gap = SCHEMA_UNIT * 0.95
    for _ in range(6):
        moved = False
        for a, b in segments:
            if a not in pos or b not in pos:
                continue
            x0, y0 = pos[a]
            x1, y1 = pos[b]
            dist = math.hypot(x1 - x0, y1 - y0)
            if dist >= min_gap or dist < 1e-6:
                continue
            ux, uy = (x1 - x0) / dist, (y1 - y0) / dist
            mx, my = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
            half = min_gap / 2.0
            pos[a] = (mx - ux * half, my - uy * half)
            pos[b] = (mx + ux * half, my + uy * half)
            moved = True
        if not moved:
            break

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
    try:
        return offset_line(geom, off, tol=tol_simplif(canonical))
    except Exception:
        return geom


def tol_simplif(canonical):
    """Tolérance de simplification commune à tout le faisceau d'un tronçon :
    chaque mission décale le même squelette -> traits jointifs dans les courbes."""
    return max(25.0, min(segment_width_m.get(canonical, 0.0) * 0.25, 2000.0))


fig, ax = plt.subplots(figsize=(16, 12))
ax.set_aspect('equal')
ax.axis('off')

# Épaisseur du trait proportionnelle à la fréquence (en points), avec un
# minimum lisible. Utilisée partout (tracé, passages, légende) pour rester cohérent.
LW_PER_TPH = 6.0
# Casing extérieur du faisceau (en points), dessiné SOUS les couleurs. Les
# couleurs elles-mêmes sont jointives (pas de liseré blanc entre missions) :
# le blanc n'apparaît que sur le pourtour du faisceau, plus sur les joints.
CASING_PT = 0.9

def freq_to_lw(freq):
    return max(3.6, freq * LW_PER_TPH)

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
# Directions de rail déjà prises par chaque gare (mode schéma) : les coudes
# prolongent ces rails au lieu de créer un décroché parallèle.
schema_dirs = (directions_alignees(segment_users.keys(), pos, SCHEMA_UNIT * 1e-3)
               if schema_mode else {})

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
mfac = 0.18 if schema_mode else 0.08
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


# Missions qui DESSERVENT une gare (un passage sans arrêt ne compte pas).
station_served_by = {}
incident_segments = {}
for canonical in segment_users:
    for sid in canonical:
        incident_segments.setdefault(sid, []).append(canonical)
for od in st.session_state.ods:
    if len(od['steps']) < 2:
        continue
    for sid in od['served_stations']:
        if sid in drawn_stations:
            station_served_by.setdefault(sid, []).append(od['idx'])

min_sq = meters_per_point * 4
# Débord du symbole de gare au-delà des traits qu'il couvre (points).
MARGE_GARE_PT = 2.2


def station_axis(sid):
    """Direction unitaire du faisceau le plus large, pour orienter la capsule."""
    px, py = pos[sid]
    best_w, best_dir = -1.0, (1.0, 0.0)
    for canonical in incident_segments.get(sid, []):
        geom = get_edge_geom(canonical[0], canonical[1])
        L = geom.length
        if L <= 0:
            continue
        step = min(L, max(L * 0.25, 300.0))
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


# 6a. Géométries décalées, avant contournement : les symboles de gare se
#     posent sur les traits réellement dessinés, pas sur le point gare.
shifted = {}
for canonical_edge, raw_users in segment_users.items():
    geom = get_edge_geom(canonical_edge[0], canonical_edge[1])
    for od_idx in sorted(raw_users):
        g = oriented_offset(geom, canonical_edge, od_idx)
        if g is not None and not g.is_empty:
            shifted[(canonical_edge, od_idx)] = g


def position_laterale(sid, od_idx, px, py):
    """Écart signé (le long de la perpendiculaire) entre la gare et le trait
    décalé de la mission `od_idx` à son droit."""
    x, y = pos[sid]
    p = Point(x, y)
    best = None
    for canonical in incident_segments.get(sid, []):
        g = shifted.get((canonical, od_idx))
        if g is None:
            continue
        d = g.distance(p)
        if best is None or d < best[0]:
            best = (d, nearest_points(g, p)[0])
    if best is None:
        return 0.0
    q = best[1]
    return (q.x - x) * px + (q.y - y) * py


# 6b. Symboles : une mission -> rond ∝ fréquence ; plusieurs -> capsule qui
#     couvre exactement les traits desservis (pas un ovale centré à côté).
station_symbol = {}
station_obstacle = {}
marge_gare = MARGE_GARE_PT * meters_per_point
for sid in drawn_stations:
    served = station_served_by.get(sid)
    if not served:
        continue
    x, y = pos[sid]
    dx, dy = station_axis(sid)
    px, py = -dy, dx
    traits = [(position_laterale(sid, idx, px, py), widths_by_od.get(idx, min_sq))
              for idx in served]
    t_c, hl, hw = emprise_gare(traits, marge_gare)
    cx_s, cy_s = x + px * t_c, y + py * t_c
    if len(served) == 1:
        shape = Point(cx_s, cy_s).buffer(hl, quad_segs=24)
    else:
        shape = capsule_polygon(cx_s, cy_s, px, py, hl, hw)
    station_symbol[sid] = {
        "shape": shape, "x": cx_s, "y": cy_s, "perp": (px, py),
        "t_m": t_c, "hl_m": hl, "hw_m": hw,
    }
    station_obstacle[sid] = shape


def skip_prefer_vec(canonical, od_idx, geom):
    """Perpendiculaire au tracé, même côté que l offset de corridor (pas d inversion)."""
    coords = []
    if geom is not None and not geom.is_empty:
        if geom.geom_type == 'LineString':
            coords = list(geom.coords)
        else:
            for g in getattr(geom, 'geoms', []):
                if g.geom_type == 'LineString' and not g.is_empty:
                    coords = list(g.coords)
                    break
    if len(coords) < 2:
        return (1.0, 0.0)
    vx = coords[-1][0] - coords[0][0]
    vy = coords[-1][1] - coords[0][1]
    n = math.hypot(vx, vy)
    if n < 1e-9:
        return (1.0, 0.0)
    left = (-vy / n, vx / n)
    if schema_mode:
        ox, oy = schematic_shift.get((canonical, od_idx), (0.0, 0.0))
        if abs(ox) + abs(oy) > 1e-9:
            return (ox, oy)
        sgn = 1.0
    else:
        off = segment_offsets.get((canonical, od_idx), 0.0)
        sgn = segment_sign.get(canonical, 1.0)
        side = off * sgn
        if side < 0:
            return (-left[0], -left[1])
        if side > 0:
            return left
    if sgn < 0:
        return (-left[0], -left[1])
    return left


# 6c. Lignes de mission : casing blanc sous le faisceau (z=3), couleurs jointives
#     (z=4), sans path_effects. Le blanc ne reste visible que sur le pourtour.
#     La carte Leaflet reçoit la géométrie NON décalée + l'offset en points :
#     le décalage est refait en pixels à chaque zoom (traits toujours jointifs).
painted_lines = []  # (LineString, largeur_m) pour l'anti-collision des étiquettes
leaflet_tracks = []
for canonical_edge, raw_users in segment_users.items():
    geom = get_edge_geom(canonical_edge[0], canonical_edge[1])
    for od_idx in sorted(raw_users):
        od = st.session_state.ods[od_idx]
        shifted_geom = shifted.get((canonical_edge, od_idx))
        if shifted_geom is None:
            continue
        for sid in canonical_edge:
            if sid in od.get('served_stations', []):
                continue
            obs = station_obstacle.get(sid)
            if obs is None:
                continue
            margin = 0.75 * widths_by_od.get(od_idx, min_sq)
            pdx, pdy = skip_prefer_vec(canonical_edge, od_idx, shifted_geom)
            shifted_geom = contourne_obstacle(
                shifted_geom, obs.buffer(margin), pos[sid][0], pos[sid][1],
                pdx, pdy, extra=0.4 * margin)
        if shifted_geom is None or shifted_geom.is_empty:
            continue
        lw = freq_to_lw(od['freq_tph'])
        join = 'miter' if schema_mode else 'round'
        for ls in _line_parts(shifted_geom):
            xs, ys = ls.xy
            ax.plot(xs, ys, color='white', linewidth=lw + 2 * CASING_PT, zorder=3,
                    solid_capstyle='butt', solid_joinstyle=join)
            ax.plot(xs, ys, color=od['color'], linewidth=lw, zorder=4,
                    solid_capstyle='round', solid_joinstyle=join)
            painted_lines.append((ls, lw * meters_per_point))
        if not schema_mode:
            base = geom.simplify(tol_simplif(canonical_edge), preserve_topology=False)
            off_m = segment_offsets.get((canonical_edge, od_idx), 0.0)
            off_m *= segment_sign.get(canonical_edge, 1.0)
            for ls in _line_parts(base):
                xs, ys = ls.xy
                leaflet_tracks.append({
                    "xs": list(xs), "ys": list(ys), "color": od['color'],
                    "weight": lw, "offset": off_m / meters_per_point,
                })

# 7. Gares (Z-order: 5)
#    - UNE mission : rond couleur ∝ fréquence, liseré blanc (toujours visible
#      sur son propre trait, en carte comme en schéma)
#    - PLUSIEURS missions : capsule blanche type RATP, contour noir
#    - personne ne s arrete : pas de symbole
leaflet_stations = []
station_rects = {}
for sid in drawn_stations:
    x, y = pos[sid]
    nom = format_station(sid)
    sym = station_symbol.get(sid)
    if sym is None:
        pad = meters_per_point * 4
        station_rects[sid] = (x - pad, y - pad, x + pad, y + pad)
        continue
    served = station_served_by[sid]
    shape = sym["shape"]
    if len(served) == 1:
        col = st.session_state.ods[served[0]]['color']
        patch = Circle((sym["x"], sym["y"]), sym["hl_m"], facecolor=col,
                       edgecolor='white', linewidth=1.1, zorder=5)
        kind = "circle"
    else:
        col = '#FFFFFF'
        patch = Polygon(list(shape.exterior.coords), closed=True, facecolor=col,
                        edgecolor='#111111', linewidth=1.4, zorder=5, joinstyle='round')
        kind = "capsule"
    patch.set_clip_on(True)
    ax.add_patch(patch)
    station_rects[sid] = shape.bounds
    px, py = sym["perp"]
    leaflet_stations.append({
        "kind": kind, "x": x, "y": y, "color": col, "nom": nom,
        "t": sym["t_m"] / meters_per_point,
        "hl": sym["hl_m"] / meters_per_point,
        "hw": sym["hw_m"] / meters_per_point,
        "perp": [px, py],
    })

# 9. Etiquettes de gares avec anti-collision (gares + traces) et trait de rappel.
#    - Mode carte : seules les gares de type a/b sont nommees (lisibilite du fond).
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
    if schema_mode:
        # Alterne gauche / droite le long de la voie : les noms périurbains
        # ne s'empilent plus tous du même côté.
        parity = int(round((x * dx + y * dy) / (SCHEMA_UNIT * 0.9))) & 1
        if parity:
            dirs.extend(pair(-px, -py))
            dirs.extend(pair(px, py))
        else:
            dirs.extend(pair(px, py))
            dirs.extend(pair(-px, -py))
        dirs.extend(pair(dx, dy))
        dirs.extend(pair(px + dx, py + dy))
        dirs.extend(pair(px - dx, py - dy))
        return dirs
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
    nom = wrap_nom_gare(info['nom'])
    n_lines = nom.count("\n") + 1
    longest = max(len(p) for p in nom.split("\n"))
    dx_ax, dy_ax = station_axis(sid)
    rotate = 0
    if schema_mode and abs(dx_ax) >= abs(dy_ax) * 1.4:
        rotate = 45
    # estimation de la taille du texte en mètres
    text_w = max(1, longest) * fontsize * 0.62 * meters_per_point
    text_h = fontsize * 1.35 * n_lines * meters_per_point
    if rotate:
        text_w, text_h = (text_w + text_h) * 0.72, (text_w + text_h) * 0.72
    rx0, ry0, rx1, ry1 = station_rects[sid]
    half_rw = (rx1 - rx0) / 2
    half_rh = (ry1 - ry0) / 2

    placed = None
    is_default = False
    # Plus de rayons candidats en mode Schéma : beaucoup plus d'étiquettes à caser.
    max_r = 14 if schema_mode else 7
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
            rotation=rotate,
            bbox=dict(facecolor='white', alpha=0.82, edgecolor='none', boxstyle='round,pad=0.2'))
    occupied.append(bbox)

# 10. Fond de plan (mode carte uniquement ; le mode Schéma est volontairement
#     dé-cartographié). Plan IGN d'abord : les tuiles OSM répondent souvent
#     par une image « 403 Access blocked » aux usages automatisés, qui ne lève
#     aucune exception et finit imprimée dans l'export.
if not schema_mode:
    try:
        _basemap_ok = False
        # Import local : contextily tire rasterio, dont la roue exige
        # libexpat (packages.txt). Sans lui, seul le fond de plan saute.
        import contextily as cx
        for _src in fournisseurs_tuiles(fond_carte):
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
        Line2D([0], [0], marker='o', color='none', markerfacecolor=sample_c,
               markeredgewidth=0, markersize=11, linestyle='None',
               label="Gare desservie (une mission)"),
        Patch(facecolor='#FFFFFF', edgecolor='#111111', linewidth=1.5,
              label="Correspondance (capsule, plusieurs missions)"),
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


# --- Exports (en tête de page : visibles sans défiler sous la carte) ---
buf_map = io.BytesIO()
fig.savefig(buf_map, format="png", dpi=300, bbox_inches='tight', facecolor="white")
buf_leg = io.BytesIO()
fig_legend.savefig(buf_leg, format="png", dpi=300, bbox_inches='tight', facecolor="white")
png_map = composer_png_avec_logo(buf_map.getvalue())
png_leg = composer_png_avec_logo(buf_leg.getvalue(), largeur_frac=0.55)
pdf_map = composer_pdf_depuis_png(png_map)

with toolbar:
    dl1, dl2, dl3 = st.columns(3)
    with dl1:
        st.download_button("📥 Carte (PNG HD)", data=png_map,
                           file_name="schema_reticufer_carte.png", mime="image/png",
                           use_container_width=True, type="primary")
    with dl2:
        st.download_button("📥 Carte (PDF)", data=pdf_map,
                           file_name="schema_reticufer_carte.pdf", mime="application/pdf",
                           use_container_width=True)
    with dl3:
        st.download_button("📥 Légende (PNG)", data=png_leg,
                           file_name="schema_reticufer_legende.png", mime="image/png",
                           use_container_width=True)

# --- Carte et légende côte à côte, sans superposition ---
col_map, col_leg = st.columns([4, 1])
with col_map:
    if schema_mode:
        render_interactive_map(fig)
    else:
        try:
            html = folium_map_html(leaflet_tracks, leaflet_stations, height=760, fond=fond_carte)
            components.html(html, height=780)
        except Exception:
            st.warning("Carte interactive indisponible, affichage SVG de repli.")
            render_interactive_map(fig)
with col_leg:
    st.markdown("**Légende**")
    st.pyplot(fig_legend)
