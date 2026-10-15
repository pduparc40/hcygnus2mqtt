---
name: Table de registres
about: Partager des registres identifiés sur une PAC HCygnus (ou un autre modèle MTA)
title: "[Registres] HCygnus Tech <modèle>"
labels: registres
---

**PAC** : HCygnus Tech <020 … 301>, année :
**Régulateur** (étiquette) :
**Liaison** : vitesse / parité / adresse esclave trouvées avec `probe` :

### Registres confirmés

| clé | table | adresse | type / échelle | comment c'est confirmé |
|---|---|---|---|---|
| water_out_temp | holding | 257 | int16 ×0,1 | = afficheur sur 3 relevés |

### Registres probables (non confirmés)

### Fichiers

Joins si possible `scan_ref.csv` (avec tes valeurs d'afficheur au même moment) et le
journal de `watch --log`.
