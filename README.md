# Chronofer - ReticuFer

<p align="center">
  <img src="logo.png" alt="République française — Cerema" width="420">
</p>

**ReticuFer** est un module de l'écosystème **Chronofer** (Cerema) permettant de construire, calculer et visualiser des schémas réticulaires ferroviaires interactifs.
Il transforme des données géographiques brutes (gares, réseau ferré) en un graphe topologique propre et génère des cartes d'exploitation claires (superposition de lignes, offsets géométriques, etc.).

## 🚀 Fonctionnalités
- Génération d'un graphe topologique NetworkX depuis des données SIG (noding & snapping).
- Application interactive interactive sous **Streamlit** pour le paramétrage de 8 missions de desserte.
- Décalage géométrique automatique (offsetting) des tracés en superposition pour garder une lisibilité maximale.
- Gestion des relations de passages sans arrêt vs dessertes locales (z-order des points de gares par rapport aux lignes).
- Export des schémas en `.png` haute définition avec légende automatique et bandeau logo Cerema (la carte n'est pas recouverte).

## 📦 Installation

Assurez-vous de disposer de Python 3.10 ou supérieur.
Installez les dépendances nécessaires listées dans le fichier `requirements.txt` via pip :

```bash
pip install -r requirements.txt
```

## ⚙️ Données d'entrée requises
L'application s'appuie sur la présence de ces fichiers dans le dossier du projet :
- `gare.geojson` : La liste géographique des gares au format WGS 84, **type de gare inclus** (champ `typeGare` : A / B / C).
- `reseau_ferroviaire.geojson` : Les géométries du réseau ferré de référence.
- `regions_departements.json` : Le découpage **région → départements** (code INSEE) servant au périmètre régional. Éditable pour ajuster les périmètres.

> `donnees_gares.xlsx` n'est plus utilisé par l'application : le type de gare est désormais porté directement par `gare.geojson`.

## 🗺️ Périmètre & trajets
- **Périmètre géographique** (barre latérale) : *Régional* (par défaut — les gares d'une région française, filtrées par département via le code INSEE) ou *France entière* (toutes les gares de `gare.geojson`, premier chargement plus long).
- **Ajustement manuel des missions** : le routage automatique peut manquer une gare située sur le parcours (ou échouer sur une gare isolée), surtout en périmètre national. Chaque mission dispose donc d'un ajustement manuel permettant d'insérer une gare à une position précise du trajet (ou d'en bâtir un de zéro) et d'en retirer.
- Seules les gares réellement utilisées par au moins une mission tracée sont affichées.

## 🏃 Lancement de l'interface

Une fois l'environnement prêt, exécutez simplement la commande suivante à la racine du projet :

```bash
streamlit run app.py
```

Votre navigateur s'ouvrira automatiquement sur l'application Web où vous pourrez paramétrer les gares de départ, les gares d'arrivée, ainsi que configurer visuellement la topologie des passages sans arrêt.
