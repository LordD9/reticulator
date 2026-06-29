# Chronofer - Reticulator

Reticulator est un module de l'écosystème **Chronofer** permettant de construire, calculer et visualiser des schémas réticulaires ferroviaires interactifs. 
Il transforme des données géographiques brutes (gares, réseau ferré) en un graphe topologique propre et génère des cartes d'exploitation claires (superposition de lignes, offsets géométriques, etc.).

## 🚀 Fonctionnalités
- Génération d'un graphe topologique NetworkX depuis des données SIG (noding & snapping).
- Application interactive interactive sous **Streamlit** pour le paramétrage de 8 missions de desserte.
- Décalage géométrique automatique (offsetting) des tracés en superposition pour garder une lisibilité maximale.
- Gestion des relations de passages sans arrêt vs dessertes locales (z-order des points de gares par rapport aux lignes).
- Export des schémas en `.png` haute définition avec légende automatique.

## 📦 Installation

Assurez-vous de disposer de Python 3.10 ou supérieur.
Installez les dépendances nécessaires listées dans le fichier `requirements.txt` via pip :

```bash
pip install -r requirements.txt
```

## ⚙️ Données d'entrée requises
L'application s'appuie sur la présence de 3 fichiers sources (non versionnés s'ils sont lourds) dans le dossier du projet :
- `gare.geojson` : La liste géographique des gares au format WGS 84.
- `reseau_ferroviaire.geojson` : Les géométries du réseau ferré de référence.
- `donnees_gares.xlsx` : Le fichier de correspondances et d'attributs (code UIC, libellés, typologie de gares, et statistiques de trafic).

## 🏃 Lancement de l'interface

Une fois l'environnement prêt, exécutez simplement la commande suivante à la racine du projet :

```bash
streamlit run app.py
```

Votre navigateur s'ouvrira automatiquement sur l'application Web où vous pourrez paramétrer les gares de départ, les gares d'arrivée, ainsi que configurer visuellement la topologie des passages sans arrêt.
