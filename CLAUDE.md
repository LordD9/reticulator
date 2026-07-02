# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Reticulator is a module of the **Chronofer** ecosystem. It turns raw rail GIS data (stations + network geometry) into a clean topological graph, then lets a user interactively design and export "schémas réticulaires" (reticular operating diagrams). Code identifiers mix French (domain functions/columns: `charger_donnees`, `nom_gare`, `code_uic`) and CamelCase (JS/layout: `getEdgeGeom`). Comments are in French.

A detailed companion doc, [AGENTS.md](AGENTS.md), covers style conventions, input-data schemas, and known algorithmic pitfalls (Shapely noding, offset orientation). Read it before touching GIS/geometry code.

## Commands

```bash
pip install -r requirements.txt      # Python 3.10+
streamlit run app.py                 # launch the interactive app (main entrypoint)
python -m py_compile app.py schema_reticulaire.py   # quick syntax check (no test suite exists)
```

There is no test suite, linter config, or build step. Validation is manual via the Streamlit UI and `py_compile`.

## Required input files (project root, not all versioned)

- `gare.geojson` — station Points (WGS 84); key prop `code_uic`, uses `statut_gare == "ouverte"`.
- `reseau_ferroviaire.geojson` — rail network MultiLineStrings.
- `donnees_gares.xlsx` — attributes merged on `codeUic` (type, `Frequentation2024`, etc.).

## Architecture

Two files carry all logic; data flows one direction: raw GIS → graph → interactive diagram.

**`schema_reticulaire.py`** — GIS backend. Loads/reprojects data (Lambert 93 `EPSG:2154` ↔ WGS 84), clips the national network to the stations' bbox, then builds a topological `networkx.Graph`:
- `charger_donnees()` → `construire_graphe()` (noding/snapping stations onto lines, then `_fusionner_extremites_proches()` to heal digitization gaps) → `voisinages_stricts()` computes each station's *direct* rail neighbors (no intermediate station) with local Dijkstra.
- Hard-coded GIS tolerances near the top (`NODE_PRECISION_M`, `ENDPOINT_MERGE_TOL_M=50`, `BBOX_BUFFER_DEG`, `SIMPLIFY_TOL_M`) materially affect graph topology — change only with care.
- Also contains a legacy standalone-HTML/Leaflet export path (`construire_html`, `serialiser_*`); the Streamlit app is the current reference, not the HTML.

**`app.py`** — Streamlit app (the product). Consumes the station-to-station graph (cached via `@st.cache_data` in `load_and_build_graph()`), lets the user define up to 8 origin–destination missions, routes each with Dijkstra, and renders with Matplotlib. Two render modes: geographic and schematic (`compute_schematic_layout`). Key concerns when editing here:
- **Parallel-line offsetting**: overlapping missions are spread apart with a static Shapely offset (`offset_line`). Edge geometry must always be drawn in a canonical orientation (low station id → high) so offsets don't diverge for reversed missions — see the offset-orientation pitfall in AGENTS.md.
- **Z-order trick**: stations draw at `zorder=5`; a mission passing *through* a station without stopping redraws a local line segment at `zorder=6` to visually cover the station dot.
- Exports the map and legend as separate high-res PNGs (`build_legend_figure`, export section at end of file).
