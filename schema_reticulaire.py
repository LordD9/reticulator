"""
schema_reticulaire.py

Genere une page HTML interactive permettant de tracer 8 OD ferroviaires
sur le reseau, soit gare-a-gare en mode manuel (voisinage strict),
soit en routage automatique (plus court chemin).

Sortie : reticulaire_interactif.html
"""

import json
import heapq
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
import networkx as nx
from shapely.geometry import Point, LineString, MultiLineString
from shapely.ops import substring, unary_union, linemerge
from shapely.strtree import STRtree
from pyproj import Transformer


# --- Fichiers ---
FICHIER_GARES_GEOJSON = "gare.geojson"
FICHIER_RESEAU_GEOJSON = "reseau_ferroviaire.geojson"
FICHIER_DONNEES_EXCEL = "donnees_gares.xlsx"
FICHIER_SORTIE_HTML = "reticulaire_interactif.html"

# --- CRS ---
CRS_WGS = "EPSG:4326"
CRS_METRIC = "EPSG:2154"  # Lambert 93

# --- Style gares (identique a schema_auto.py) ---
COLORS_TYPE_GARE = {
    "a": "#e41a1c",
    "b": "#377eb8",
    "c": "#4daf4a",
    "default": "#999999",
}

# --- Palette 8 OD (Tableau 10, doux pour preserver lisibilite des gares) ---
PALETTE_OD = [
    "#4E79A7", "#F28E2B", "#76B7B2", "#59A14F",
    "#EDC948", "#B07AA1", "#FF9DA7", "#9C755F",
]

# --- Snap & graphe ---
NODE_PRECISION_M = 5.0       # tolerance fusion d'extremites (metres)
ENDPOINT_MERGE_TOL_M = 50.0  # rayon de fusion entre extremites de lignes distinctes
BBOX_BUFFER_DEG = 0.05       # buffer autour des gares pour clipper le reseau
SIMPLIFY_TOL_M = 40.0        # Douglas-Peucker sur les geoms de voisinage


# ===================================================================
# 1. CHARGEMENT
# ===================================================================
def charger_donnees():
    gares_gdf = gpd.read_file(FICHIER_GARES_GEOJSON)
    reseau_gdf = gpd.read_file(FICHIER_RESEAU_GEOJSON)
    donnees_df = pd.read_excel(FICHIER_DONNEES_EXCEL)

    if gares_gdf.crs != CRS_WGS:
        gares_gdf = gares_gdf.to_crs(CRS_WGS)
    if reseau_gdf.crs != CRS_WGS:
        reseau_gdf = reseau_gdf.to_crs(CRS_WGS)

    donnees_df = donnees_df.rename(columns={
        "codeUic": "code_uic",
        "nomGare": "nom_gare",
        "typeGare": "type_gare",
        "arretsTer2024": "trafic_ter_2024",
        "Frequentation2024": "freq_2024",
        "habitants10MinAVelo2023": "pop_10_velo",
    })

    gares_gdf["code_uic"] = gares_gdf["code_uic"].astype(str)
    donnees_df["code_uic"] = donnees_df["code_uic"].astype(str)

    if "nom_gare" in gares_gdf.columns and "nom_gare" in donnees_df.columns:
        gares_gdf = gares_gdf.drop(columns=["nom_gare"])
    gares_data = gares_gdf.merge(donnees_df, on="code_uic", how="inner")

    bounds = gares_data.total_bounds
    minx, miny, maxx, maxy = bounds
    bbox = Point(minx, miny).union(Point(maxx, maxy)).envelope.buffer(BBOX_BUFFER_DEG)
    reseau_clip = reseau_gdf[reseau_gdf.intersects(bbox)].copy().reset_index(drop=True)

    return gares_data, reseau_clip


# ===================================================================
# 2. CONSTRUCTION DU GRAPHE ROUTABLE
# ===================================================================
def _quantize(x, y, prec=NODE_PRECISION_M):
    return (round(x / prec) * prec, round(y / prec) * prec)


def _node_id_xy(x, y):
    qx, qy = _quantize(x, y)
    return f"N_{int(qx)}_{int(qy)}"


def _explode_lines(reseau_metric):
    lines = []
    for geom in reseau_metric.geometry:
        if geom is None or geom.is_empty:
            continue
        if geom.geom_type == "LineString":
            lines.append(geom)
        elif geom.geom_type == "MultiLineString":
            lines.extend(list(geom.geoms))
    return lines


def _noder_reseau(lines):
    """Casse toutes les lignes a leurs intersections pour creer une topologie
    propre ou les extremites se rejoignent. Le reseau brut comporte souvent des
    croisements en T qui n'apparaissent pas comme des extremites partagees."""
    if not lines:
        return []
    # Workaround numpy 2.x / shapely 2.0.x : union_all et MultiLineString
    # echouent sur la creation de collections. On accumule par reduce.
    from functools import reduce
    union = reduce(lambda a, b: a.union(b), lines)
    if union.is_empty:
        return []
    if union.geom_type == "LineString":
        return [union]
    if union.geom_type == "MultiLineString":
        return list(union.geoms)
    # GeometryCollection : on extrait les LineStrings
    out = []
    for g in getattr(union, "geoms", []):
        if g.geom_type == "LineString":
            out.append(g)
        elif g.geom_type == "MultiLineString":
            out.extend(list(g.geoms))
    return out


def construire_graphe(gares_data, reseau_clip):
    """Renvoie (graph nx, infos snap par gare)."""
    gares_m = gares_data.to_crs(CRS_METRIC)
    reseau_m = reseau_clip.to_crs(CRS_METRIC)

    lines = _explode_lines(reseau_m)
    lines = _noder_reseau(lines)
    tree = STRtree(lines)

    # Snap chaque gare a la ligne la plus proche
    snap_info = {}            # sid -> dict(line_idx, proj_dist, dist_snap_m)
    line_to_stations = {i: [] for i in range(len(lines))}
    for _, row in gares_m.iterrows():
        pt = row.geometry
        idx = int(tree.nearest(pt))
        line = lines[idx]
        proj_dist = line.project(pt)
        proj_pt = line.interpolate(proj_dist)
        sid = str(row["code_uic"])
        snap_info[sid] = {
            "line_idx": idx,
            "proj_dist": proj_dist,
            "snap_dist_m": pt.distance(proj_pt),
            "proj_x": proj_pt.x,
            "proj_y": proj_pt.y,
        }
        line_to_stations[idx].append((sid, proj_dist))

    G = nx.Graph()

    # Noeuds gares
    for sid, info in snap_info.items():
        G.add_node(
            f"S_{sid}",
            x=info["proj_x"], y=info["proj_y"],
            is_station=True, sid=sid,
        )

    # Decoupe chaque ligne en segments avec les gares comme points de coupe
    for idx, line in enumerate(lines):
        stations_on_line = sorted(line_to_stations[idx], key=lambda x: x[1])
        cuts = [(0.0, None)]
        cuts.extend((d, sid) for sid, d in stations_on_line)
        cuts.append((line.length, None))
        # dedoublonnage / tri
        seen = set()
        cuts_uniq = []
        for d, sid in sorted(cuts, key=lambda x: x[0]):
            key = round(d, 2)
            if key in seen:
                continue
            seen.add(key)
            cuts_uniq.append((d, sid))

        for i in range(len(cuts_uniq) - 1):
            d1, s1 = cuts_uniq[i]
            d2, s2 = cuts_uniq[i + 1]
            if d2 - d1 < 0.5:
                continue
            seg = substring(line, d1, d2)
            if seg.is_empty or seg.length < 0.5:
                continue

            if s1 is not None:
                n1 = f"S_{s1}"
            else:
                p = line.interpolate(d1)
                n1 = _node_id_xy(p.x, p.y)
                if not G.has_node(n1):
                    G.add_node(n1, x=p.x, y=p.y, is_station=False)

            if s2 is not None:
                n2 = f"S_{s2}"
            else:
                p = line.interpolate(d2)
                n2 = _node_id_xy(p.x, p.y)
                if not G.has_node(n2):
                    G.add_node(n2, x=p.x, y=p.y, is_station=False)

            if n1 == n2:
                continue

            length = seg.length
            coords = list(seg.coords)
            if G.has_edge(n1, n2):
                if G[n1][n2]["length"] <= length:
                    continue
            G.add_edge(n1, n2, length=length, geom=coords)

    _fusionner_extremites_proches(G, ENDPOINT_MERGE_TOL_M)
    return G, snap_info


def _fusionner_extremites_proches(G, tol):
    """Fusionne les noeuds non-gares geographiquement proches (< tol metres).
    Les LineStrings du reseau ne partagent souvent pas exactement leurs
    extremites; cette etape repare la topologie pour eviter la fragmentation."""
    non_gares = [(n, d["x"], d["y"]) for n, d in G.nodes(data=True) if not d.get("is_station")]
    if not non_gares:
        return

    pts = [Point(x, y) for _, x, y in non_gares]
    ids = [n for n, _, _ in non_gares]
    tree = STRtree(pts)

    # union-find
    parent = {n: n for n in ids}
    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i, p in enumerate(pts):
        # voisins dans un buffer
        candidates = tree.query(p.buffer(tol))
        for j in candidates:
            j = int(j)
            if j <= i:
                continue
            if p.distance(pts[j]) <= tol:
                union(ids[i], ids[j])

    # collecter les groupes
    groups = {}
    for n in ids:
        r = find(n)
        groups.setdefault(r, []).append(n)

    for canonical, members in groups.items():
        for m in members:
            if m == canonical:
                continue
            if not G.has_node(m):
                continue
            nx.contracted_nodes(G, canonical, m, self_loops=False, copy=False)
    # nettoyage : retirer l'attribut 'contraction' pose par networkx
    for n, d in G.nodes(data=True):
        d.pop("contraction", None)


# ===================================================================
# 3. VOISINAGE STRICT (Dijkstra qui s'arrete sur les noeuds-gares)
# ===================================================================
def voisinages_stricts(G):
    """Pour chaque gare, donne la liste des gares atteignables sans passer par
    une autre gare (la 'gare suivante'). Retourne (neighbors, transformer)."""
    transformer = Transformer.from_crs(CRS_METRIC, CRS_WGS, always_xy=True)

    station_nodes = [n for n, d in G.nodes(data=True) if d.get("is_station")]
    neighbors = {}

    for src in station_nodes:
        dist = {src: 0.0}
        prev = {src: None}
        heap = [(0.0, src)]
        visited = set()
        found = []

        while heap:
            d, u = heapq.heappop(heap)
            if u in visited:
                continue
            visited.add(u)

            if u != src and G.nodes[u].get("is_station"):
                found.append((u, d))
                continue  # ne pas etendre depuis une autre gare

            for v in G.neighbors(u):
                if v in visited:
                    continue
                w = G[u][v]["length"]
                nd = d + w
                if nd < dist.get(v, float("inf")):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(heap, (nd, v))

        nb_list = []
        for tgt, total in found:
            # reconstruction
            path_nodes = []
            cur = tgt
            while cur is not None:
                path_nodes.append(cur)
                cur = prev.get(cur)
            path_nodes.reverse()

            coords_m = []
            for i in range(len(path_nodes) - 1):
                a, b = path_nodes[i], path_nodes[i + 1]
                seg = list(G[a][b]["geom"])
                ax, ay = G.nodes[a]["x"], G.nodes[a]["y"]
                sx, sy = seg[0]
                # orienter le segment a -> b
                if (sx - ax) ** 2 + (sy - ay) ** 2 > 4.0:
                    seg = list(reversed(seg))
                if i == 0:
                    coords_m.extend(seg)
                else:
                    coords_m.extend(seg[1:])

            # Simplification Douglas-Peucker pour limiter les artefacts au dezoom
            # et l'epaisseur visuelle excessive due aux micro-coudes du trace.
            if len(coords_m) > 2:
                ls = LineString(coords_m).simplify(SIMPLIFY_TOL_M, preserve_topology=False)
                coords_m = list(ls.coords)

            # transformation metric -> wgs (Leaflet attend [lat, lon])
            latlon = []
            for x, y in coords_m:
                lon, lat = transformer.transform(x, y)
                latlon.append([round(lat, 6), round(lon, 6)])

            tgt_sid = G.nodes[tgt]["sid"]
            nb_list.append({
                "sid": tgt_sid,
                "dist_m": round(total, 1),
                "geom": latlon,
                "geom_m": coords_m,
            })

        src_sid = G.nodes[src]["sid"]
        neighbors[src_sid] = nb_list

    return neighbors


# ===================================================================
# 4. STYLE RESEAU (reprise schema_auto.py)
# ===================================================================
def style_reseau_feature(props):
    infra = str(props.get("infrastructure", "")).lower()
    mnemo = str(props.get("mnemo", "")).upper()
    is_lgv = "lgv" in infra or mnemo == "LGV"
    is_dv = "double" in infra or mnemo == "DV"
    is_electrified = "lectrifi" in infra or is_lgv

    color = "#5e2b97"
    if is_lgv:
        color = "#0055a4"
    elif "fret" in infra:
        color = "#008b5a"
    elif "mixte" in infra or mnemo == "BANAL":
        color = "#800080"
    elif "suspendue" in infra:
        color = "#808080"

    if is_lgv:
        weight, opacity = 8, 0.9
    else:
        weight = 4 if is_dv else 2
        if not is_electrified:
            weight *= 0.7
            opacity = 0.6
        else:
            opacity = 0.85

    dash = None if is_electrified else "5, 5"
    return {"color": color, "weight": weight, "opacity": opacity, "dashArray": dash}


# ===================================================================
# 5. GENERATION HTML
# ===================================================================
def serialiser_gares(gares_data):
    out = []
    for _, g in gares_data.iterrows():
        t = str(g["type_gare"]).lower()
        out.append({
            "sid": str(g["code_uic"]),
            "nom": g["nom_gare"],
            "type": t,
            "lat": float(g.geometry.y),
            "lon": float(g.geometry.x),
            "freq": int(g["freq_2024"]) if pd.notna(g["freq_2024"]) else 0,
            "ter": int(g["trafic_ter_2024"]) if pd.notna(g["trafic_ter_2024"]) else 0,
            "pop_velo": int(g["pop_10_velo"]) if pd.notna(g["pop_10_velo"]) else 0,
        })
    return out


def serialiser_reseau(reseau_clip):
    feats = []
    for _, row in reseau_clip.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        props = {k: (None if pd.isna(v) else v) for k, v in row.items() if k != "geometry"}
        feats.append({
            "type": "Feature",
            "properties": props,
            "geometry": geom.__geo_interface__,
        })
    return {"type": "FeatureCollection", "features": feats}


def construire_html(gares_data, reseau_clip, neighbors):
    gares_json = serialiser_gares(gares_data)
    neighbors_json = neighbors

    centre_lat = float(gares_data.geometry.y.mean())
    centre_lon = float(gares_data.geometry.x.mean())

    data_blob = json.dumps({
        "gares": gares_json,
        "neighbors": neighbors_json,
        "palette": PALETTE_OD,
        "colors_gare": COLORS_TYPE_GARE,
        "center": [centre_lat, centre_lon],
    }, ensure_ascii=False)

    html = HTML_TEMPLATE.replace("__DATA_BLOB__", data_blob)
    Path(FICHIER_SORTIE_HTML).write_text(html, encoding="utf-8")
    print(f"HTML genere : {FICHIER_SORTIE_HTML}")


# ===================================================================
# 6. TEMPLATE HTML / JS
# ===================================================================
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Schema reticulaire interactif - 8 OD</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
  html, body { margin:0; padding:0; height:100%; font-family: Arial, sans-serif; }
  #map { position:absolute; top:0; left:0; right:0; bottom:260px; }
  #panel {
    position:absolute; bottom:0; left:0; right:0; height:260px;
    background:#fafafa; border-top:2px solid #888;
    overflow:auto; padding:8px 12px; box-sizing:border-box;
  }
  #panel h3 { margin:0 0 6px 0; font-size:14px; }
  #status {
    background:#fff8d6; padding:6px 10px; border-left:4px solid #d4a300;
    margin-bottom:8px; font-size:12px; display:none;
  }
  #status.active { display:block; }
  table.od-table { border-collapse:collapse; width:100%; font-size:12px; }
  table.od-table th, table.od-table td {
    border:1px solid #ccc; padding:4px 6px; text-align:center;
    vertical-align:middle; white-space:nowrap;
  }
  table.od-table th { background:#eee; }
  .pastille { width:18px; height:18px; border-radius:50%; display:inline-block; border:1px solid #444; }
  .btn { cursor:pointer; padding:2px 8px; border:1px solid #888; background:#fff; border-radius:3px; font-size:11px; }
  .btn:hover { background:#eaeaea; }
  .btn.primary { background:#d4ecff; }
  .btn.danger { background:#ffd4d4; }
  .btn.active { background:#ffd54a; }
  .leaflet-tooltip.gare-label { background:transparent; border:none; box-shadow:none; }
  #legend {
    position:absolute; top:10px; right:10px; background:rgba(255,255,255,0.95);
    padding:8px 10px; border:1px solid #888; border-radius:4px;
    font-size:11px; max-width:240px; z-index:1000;
  }
  #legend h4 { margin:0 0 4px 0; font-size:12px; }
</style>
</head>
<body>
<div id="map"></div>
<div id="legend"></div>
<div id="panel">
  <h3>Tableau des 8 OD</h3>
  <div id="status"></div>
  <table class="od-table" id="od-table">
    <thead>
      <tr>
        <th>#</th><th>Couleur</th><th>Mode</th><th>Depart</th><th>Arrivee</th>
        <th>Etapes</th><th>Frequence</th><th>Actions</th>
      </tr>
    </thead>
    <tbody></tbody>
  </table>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet-polylineoffset@1.1.1/leaflet.polylineoffset.js"></script>
<script>
const DATA = __DATA_BLOB__;

// ----- 1. CARTE -----
const map = L.map('map', { preferCanvas:true }).setView(DATA.center, 8);
L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png', {
  attribution:'&copy; OpenStreetMap, &copy; CartoDB',
  maxZoom:18
}).addTo(map);

// ----- 2. RESEAU (cache : ne sert qu'aux geometries de fond, non affiche) -----
// Le reseau ferroviaire n'est pas dessine : seules les missions OD sont tracees.

// ----- 3. GARES -----
const gareById = {};
DATA.gares.forEach(g => gareById[g.sid] = g);

const gareMarkers = {};
const haloMarkers = {};
const labelMarkers = {};

function radiusForFreq(freq) {
  return 4 + Math.log1p((freq||0)/10000) * 3;
}

DATA.gares.forEach(g => {
  const color = DATA.colors_gare[g.type] || DATA.colors_gare.default;
  const r = radiusForFreq(g.freq);

  // halo (initialement invisible)
  const halo = L.circleMarker([g.lat, g.lon], {
    radius: r + 6, color:'#fff', weight:3, fill:false, opacity:0
  }).addTo(map);
  haloMarkers[g.sid] = halo;

  const m = L.circleMarker([g.lat, g.lon], {
    radius: r, color:'#fff', weight:1, fill:true, fillColor:color, fillOpacity:0.85
  }).addTo(map);
  m.bindTooltip(g.nom + ' (' + g.freq.toLocaleString('fr-FR') + ' voy.)');
  m.on('click', () => onGareClick(g.sid));
  gareMarkers[g.sid] = m;

  if (g.type === 'a' || g.type === 'b') {
    const fs = g.type === 'a' ? '10px' : '8.5px';
    const col = g.type === 'a' ? '#000' : '#444';
    const lbl = L.tooltip({
      permanent:true, direction:'right', offset:[6,0], className:'gare-label'
    }).setContent('<span style="font-weight:bold;color:'+col+';font-size:'+fs+
                  ';text-shadow:-1px -1px 0 #fff,1px -1px 0 #fff,-1px 1px 0 #fff,1px 1px 0 #fff;">'+
                  g.nom+'</span>').setLatLng([g.lat, g.lon]);
    lbl.addTo(map);
    labelMarkers[g.sid] = lbl;
  }
});

// ----- 4. STRUCTURES OD -----
const FREQ_OPTIONS = [
  { label:'15 min', val:15,  weight:11 },
  { label:'30 min', val:30,  weight:6.5},
  { label:'1 h',    val:60,  weight:3.5},
  { label:'2 h',    val:120, weight:1.8},
];
const OFFSET_SPACING = 6;   // espacement (px) entre OD sur un meme troncon

const ods = [];
for (let i=0; i<8; i++) {
  ods.push({
    idx: i,
    color: DATA.palette[i],
    mode: 'manuel',       // 'manuel' | 'auto'
    depart: null,         // sid
    arrivee: null,
    steps: [],            // sids successifs incluant depart et arrivee
    freq: 60,
    layer: null,          // L.polyline
  });
}

let editingOD = null;     // index de l'OD en cours d'edition

// ----- 5. TABLE -----
const tbody = document.querySelector('#od-table tbody');
function renderTable() {
  tbody.innerHTML = '';
  ods.forEach(od => {
    const tr = document.createElement('tr');
    const freqOpts = FREQ_OPTIONS.map(o =>
      `<option value="${o.val}" ${o.val===od.freq?'selected':''}>${o.label}</option>`).join('');
    const modeOpts = ['manuel','auto'].map(m =>
      `<option value="${m}" ${m===od.mode?'selected':''}>${m==='manuel'?'Manuel':'Auto'}</option>`).join('');
    const depNom = od.depart ? gareById[od.depart].nom : '<i>-</i>';
    const arrNom = od.arrivee ? gareById[od.arrivee].nom : '<i>-</i>';
    const editing = editingOD === od.idx;
    tr.innerHTML = `
      <td>${od.idx+1}</td>
      <td><span class="pastille" style="background:${od.color}"></span></td>
      <td><select data-act="mode" data-idx="${od.idx}">${modeOpts}</select></td>
      <td>${depNom}</td>
      <td>${arrNom}</td>
      <td>${od.steps.length}</td>
      <td><select data-act="freq" data-idx="${od.idx}">${freqOpts}</select></td>
      <td>
        <button class="btn ${editing?'active':'primary'}" data-act="edit" data-idx="${od.idx}">
          ${editing?'En cours...':'Editer'}
        </button>
        <button class="btn danger" data-act="clear" data-idx="${od.idx}">Vider</button>
      </td>`;
    tbody.appendChild(tr);
  });
}

tbody.addEventListener('change', e => {
  const act = e.target.dataset.act, idx = +e.target.dataset.idx;
  if (act === 'freq') { ods[idx].freq = +e.target.value; redrawAllOds(); renderLegend(); }
  if (act === 'mode') {
    ods[idx].mode = e.target.value;
    if (editingOD === idx) cancelEdit();
    clearOD(idx); renderTable();
  }
});
tbody.addEventListener('click', e => {
  if (e.target.tagName !== 'BUTTON') return;
  const act = e.target.dataset.act, idx = +e.target.dataset.idx;
  if (act === 'edit') startEdit(idx);
  if (act === 'clear') { clearOD(idx); renderTable(); renderLegend(); }
});

// ----- 6. EDITION -----
const statusDiv = document.getElementById('status');
function setStatus(html) {
  if (!html) { statusDiv.classList.remove('active'); statusDiv.innerHTML=''; return; }
  statusDiv.classList.add('active'); statusDiv.innerHTML = html;
}

function startEdit(idx) {
  if (editingOD !== null) cancelEdit();
  editingOD = idx;
  clearOD(idx);
  const od = ods[idx];
  if (od.mode === 'manuel') {
    setStatus(`<b>Edition OD #${idx+1}</b> (Manuel) - Cliquez la gare de DEPART.
      <button class="btn" id="btn-undo">Annuler dernier</button>
      <button class="btn primary" id="btn-validate">Valider</button>
      <button class="btn danger" id="btn-cancel">Quitter edition</button>`);
  } else {
    setStatus(`<b>Edition OD #${idx+1}</b> (Auto) - Cliquez la gare de DEPART puis la gare d'ARRIVEE.
      <button class="btn danger" id="btn-cancel">Quitter edition</button>`);
  }
  hookStatusButtons();
  renderTable();
  updateHalos();
}

function hookStatusButtons() {
  const u = document.getElementById('btn-undo');
  const v = document.getElementById('btn-validate');
  const c = document.getElementById('btn-cancel');
  if (u) u.onclick = undoLastStep;
  if (v) v.onclick = validateEdit;
  if (c) c.onclick = cancelEdit;
}

function cancelEdit() {
  if (editingOD === null) return;
  clearOD(editingOD);
  editingOD = null;
  setStatus(null);
  updateHalos();
  renderTable();
  renderLegend();
}

function validateEdit() {
  if (editingOD === null) return;
  const od = ods[editingOD];
  if (od.mode === 'manuel' && od.steps.length < 2) {
    alert('Selectionnez au moins 2 gares.');
    return;
  }
  od.arrivee = od.steps[od.steps.length - 1];
  editingOD = null;
  setStatus(null);
  updateHalos();
  redrawAllOds();
  renderTable();
  renderLegend();
}

function undoLastStep() {
  if (editingOD === null) return;
  const od = ods[editingOD];
  if (od.steps.length === 0) return;
  od.steps.pop();
  if (od.steps.length === 0) { od.depart = null; }
  redrawAllOds();
  updateHalos();
  refreshEditStatus();
  renderTable();
}

function refreshEditStatus() {
  if (editingOD === null) return;
  const od = ods[editingOD];
  if (od.mode === 'manuel') {
    let info;
    if (od.steps.length === 0) info = 'Cliquez la gare de DEPART.';
    else if (od.steps.length === 1) info = `Depart: <b>${gareById[od.depart].nom}</b>. Cliquez une gare voisine.`;
    else {
      const last = od.steps[od.steps.length-1];
      info = `Dernier: <b>${gareById[last].nom}</b>. Continuez ou validez.`;
    }
    setStatus(`<b>Edition OD #${editingOD+1}</b> (Manuel) - ${info}
      <button class="btn" id="btn-undo">Annuler dernier</button>
      <button class="btn primary" id="btn-validate">Valider</button>
      <button class="btn danger" id="btn-cancel">Quitter edition</button>`);
  } else {
    let info = od.depart ? `Depart: <b>${gareById[od.depart].nom}</b>. Cliquez la gare d'ARRIVEE.`
                          : 'Cliquez la gare de DEPART.';
    setStatus(`<b>Edition OD #${editingOD+1}</b> (Auto) - ${info}
      <button class="btn danger" id="btn-cancel">Quitter edition</button>`);
  }
  hookStatusButtons();
}

// ----- 7. CLICS GARE -----
function onGareClick(sid) {
  if (editingOD === null) return;
  const od = ods[editingOD];

  if (od.mode === 'manuel') {
    if (od.steps.length === 0) {
      od.depart = sid;
      od.steps.push(sid);
    } else {
      const last = od.steps[od.steps.length-1];
      const vois = DATA.neighbors[last] || [];
      if (!vois.some(v => v.sid === sid)) {
        alert('Cette gare n est pas une voisine directe de ' + gareById[last].nom);
        return;
      }
      od.steps.push(sid);
    }
    redrawAllOds();
    updateHalos();
    refreshEditStatus();
    renderTable();
    return;
  }

  // Mode auto
  if (od.depart === null) {
    od.depart = sid;
    od.steps = [sid];
  } else if (od.depart === sid) {
    alert('Cliquez une gare d arrivee differente.');
    return;
  } else {
    // Dijkstra sur le graphe station-a-station
    const path = dijkstraStations(od.depart, sid);
    if (!path) {
      alert('Aucun chemin trouve sur le reseau.');
      return;
    }
    od.steps = path;
    od.arrivee = sid;
    redrawAllOds();
    editingOD = null;
    setStatus(null);
    updateHalos();
    renderTable();
    renderLegend();
    return;
  }
  redrawAllOds();
  updateHalos();
  refreshEditStatus();
  renderTable();
}

// ----- 8. DIJKSTRA STATION-A-STATION -----
function dijkstraStations(src, dst) {
  const dist = {[src]:0};
  const prev = {[src]:null};
  const visited = new Set();
  // file de priorite simple (suffisant pour 42 noeuds)
  const pq = [[0, src]];
  while (pq.length) {
    pq.sort((a,b)=>a[0]-b[0]);
    const [d, u] = pq.shift();
    if (visited.has(u)) continue;
    visited.add(u);
    if (u === dst) break;
    const vois = DATA.neighbors[u] || [];
    for (const nb of vois) {
      const v = nb.sid, w = nb.dist_m;
      const nd = d + w;
      if (nd < (dist[v] ?? Infinity)) {
        dist[v] = nd; prev[v] = u;
        pq.push([nd, v]);
      }
    }
  }
  if (!(dst in prev)) return null;
  const out = [];
  let cur = dst;
  while (cur !== null) { out.push(cur); cur = prev[cur]; }
  return out.reverse();
}

// ----- 9. TRACE DES OD (decalage parallele dynamique par troncon partage) -----
function edgeKey(a, b) { return a < b ? a+'_'+b : b+'_'+a; }

function getEdgeGeom(a, b) {
  // Rendu reticulaire : segment droit gare-a-gare, dans le sens canonique
  // min(sid)->max(sid) pour que polyline-offset decale les OD du meme cote
  // quel que soit le sens de parcours. Le routage (Dijkstra) continue lui
  // d'utiliser les distances reelles portees par DATA.neighbors[*].dist_m.
  const [s1, s2] = a < b ? [a, b] : [b, a];
  return [[gareById[s1].lat, gareById[s1].lon], [gareById[s2].lat, gareById[s2].lon]];
}

function redrawAllOds() {
  // 1. Recense les OD qui empruntent chaque troncon (cle = paire de gares non orientee)
  const edgeUsers = {};
  ods.forEach(od => {
    if (od.steps.length < 2) return;
    for (let i = 0; i < od.steps.length - 1; i++) {
      const k = edgeKey(od.steps[i], od.steps[i+1]);
      if (!edgeUsers[k]) edgeUsers[k] = [];
      edgeUsers[k].push(od.idx);
    }
  });

  // 2. Pour chaque OD, retrace tronçon par tronçon avec offset selon le rang
  ods.forEach(od => {
    if (od.layer) { map.removeLayer(od.layer); od.layer = null; }
    if (od.steps.length < 2) return;
    const w = (FREQ_OPTIONS.find(o => o.val === od.freq) || {weight:4}).weight;
    const group = L.featureGroup();
    for (let i = 0; i < od.steps.length - 1; i++) {
      const a = od.steps[i], b = od.steps[i+1];
      const users = edgeUsers[edgeKey(a,b)] || [od.idx];
      const rank = users.indexOf(od.idx);
      const count = users.length;
      const offset = (rank - (count - 1) / 2) * OFFSET_SPACING;
      const geom = getEdgeGeom(a, b);
      L.polyline(geom, {
        color: od.color, weight: w, opacity: 0.9,
        lineCap: 'butt', lineJoin: 'miter',
        smoothFactor: 3.0,    // simplification zoom-adaptative (px)
        offset: offset
      }).addTo(group);
    }
    group.addTo(map);
    od.layer = group;
  });
}

function clearOD(idx) {
  const od = ods[idx];
  if (od.layer) { map.removeLayer(od.layer); od.layer = null; }
  od.depart = null; od.arrivee = null; od.steps = [];
  redrawAllOds();
}

// ----- 10. HALOS (voisinage strict) -----
function updateHalos() {
  Object.values(haloMarkers).forEach(h => h.setStyle({ opacity:0 }));
  if (editingOD === null) return;
  const od = ods[editingOD];
  if (od.mode !== 'manuel') return;
  if (od.steps.length === 0) {
    // toutes les gares cliquables
    Object.values(haloMarkers).forEach(h => h.setStyle({ opacity:0.6, color:'#ffd54a' }));
    return;
  }
  const last = od.steps[od.steps.length-1];
  const vois = DATA.neighbors[last] || [];
  vois.forEach(v => {
    if (haloMarkers[v.sid]) haloMarkers[v.sid].setStyle({ opacity:0.9, color:'#ffd54a' });
  });
}

// ----- 11. LEGENDE DYNAMIQUE -----
const legendDiv = document.getElementById('legend');
function renderLegend() {
  let html = '<h4>OD definies</h4>';
  let any = false;
  ods.forEach(od => {
    if (od.steps.length < 2) return;
    any = true;
    const fopt = FREQ_OPTIONS.find(o => o.val === od.freq);
    html += `<div style="margin:2px 0;">
      <i style="display:inline-block;width:18px;height:4px;background:${od.color};vertical-align:middle;"></i>
      &nbsp;OD ${od.idx+1} : ${gareById[od.depart].nom} &harr; ${gareById[od.arrivee].nom}
      <span style="color:#666;">(${fopt?fopt.label:''})</span>
    </div>`;
  });
  if (!any) html += '<div style="color:#888;">Aucune OD tracee.</div>';
  html += '<hr><h4>Gares</h4>';
  html += `<div><i style="background:${DATA.colors_gare.a};width:10px;height:10px;border-radius:50%;display:inline-block;"></i> Type A</div>`;
  html += `<div><i style="background:${DATA.colors_gare.b};width:10px;height:10px;border-radius:50%;display:inline-block;"></i> Type B</div>`;
  html += `<div><i style="background:${DATA.colors_gare.c};width:10px;height:10px;border-radius:50%;display:inline-block;"></i> Type C</div>`;
  legendDiv.innerHTML = html;
}

// ----- 12. INIT -----
renderTable();
renderLegend();
</script>
</body>
</html>
"""


# ===================================================================
# 7. MAIN
# ===================================================================
def main():
    print("Chargement des donnees...")
    gares_data, reseau_clip = charger_donnees()
    print(f"  {len(gares_data)} gares, {len(reseau_clip)} entites reseau dans la BBOX")

    print("Construction du graphe routable...")
    G, snap_info = construire_graphe(gares_data, reseau_clip)
    print(f"  {G.number_of_nodes()} noeuds, {G.number_of_edges()} aretes")
    pires = sorted(snap_info.items(), key=lambda kv: -kv[1]["snap_dist_m"])[:5]
    print("  Snap distances max (m) :", [(k, round(v["snap_dist_m"],1)) for k,v in pires])

    print("Calcul des voisinages stricts...")
    neighbors = voisinages_stricts(G)
    avg_n = sum(len(v) for v in neighbors.values()) / max(1, len(neighbors))
    iso = [k for k,v in neighbors.items() if len(v) == 0]
    print(f"  Voisins moyens par gare : {avg_n:.1f}")
    print(f"  Gares isolees : {len(iso)}")

    print("Generation du HTML...")
    construire_html(gares_data, reseau_clip, neighbors)
    print("Termine.")


if __name__ == "__main__":
    main()
