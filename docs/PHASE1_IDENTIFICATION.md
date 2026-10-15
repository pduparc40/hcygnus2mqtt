# Phase 1 — Identifier les registres Modbus

**Objectif** : reconstruire la table Modbus de la PAC, que MTA ne publie pas, avec une clé
USB-RS485 et un ordinateur portable branchés directement sur la PAC.
**Résultat** : un fichier `registers/hcygnus_tech.yaml` dont les registres sont marqués
`verified: true`, prêt pour la [phase 2](PHASE2_HOME_ASSISTANT.md).
**Durée** : une soirée pour l'essentiel ; quelques jours d'observation pour les
événements rares (dégivrage, alarmes).

> **Règle d'or : on ne fait que LIRE pendant toute cette phase.**
> Écrire au hasard dans un régulateur peut modifier un paramètre de sécurité
> (pressostats, antigel, temporisations du compresseur). Les outils de cette phase
> ne savent d'ailleurs que lire.

## 1. Matériel

- **Une clé USB-RS485** (5 à 15 €) à puce FTDI FT232 ou CH340. Les modèles FTDI
  fonctionnent sans pilote sur Windows, macOS et Linux ; pour CH340 sous Windows ou
  macOS, installe le pilote du fabricant (WCH).
- **Un ordinateur portable** (Windows, macOS ou Linux) avec Python 3.10 ou plus récent.
- **Deux fils** (ou un bout de câble réseau) pour relier A/B de la clé à la PAC, plus la
  masse si les deux côtés en ont une.
- Le câble définitif vers la chaufferie peut aussi servir : la clé se branche alors au
  bout du câble, dans la chaufferie, ce qui est plus confortable. Voir [CABLAGE.md](CABLAGE.md).

## 2. Installer l'outil

```bash
git clone https://github.com/pduparc40/hcygnus2mqtt.git
cd hcygnus2mqtt
python3 -m venv .venv
. .venv/bin/activate          # Windows : .venv\Scripts\activate
pip install -e .
hcygnus2mqtt --help
```

**Trouver le nom du port** de la clé :

| Système | Commande | Nom typique |
|---|---|---|
| Windows | Gestionnaire de périphériques → *Ports (COM et LPT)* | `COM3`, `COM4`… |
| macOS | `ls /dev/tty.usbserial*` | `/dev/tty.usbserial-A10K…` |
| Linux | `ls /dev/serial/by-id/` | `/dev/ttyUSB0` |

Sous Linux, ajoute ton utilisateur au groupe `dialout` (puis reconnecte-toi) si l'accès au
port est refusé : `sudo usermod -aG dialout $USER`.

Dans la suite, remplace `--port COM3` par le nom de ton port.

## 3. Se brancher sur la PAC

L'interface RS485 est une **option** sur cette gamme : vérifie d'abord qu'elle est
présente, en suivant [CABLAGE.md](CABLAGE.md). **Le tableau de la PAC est en 400 V : coupe
le sectionneur avant de l'ouvrir**, et confie l'opération à ton installateur si tu n'es pas
à l'aise.

Relève la **référence du régulateur** (étiquette, souvent un Dixell) : avec elle, on peut
retrouver le manuel et deviner beaucoup d'adresses.

Note sur papier ce qu'affiche l'afficheur à cet instant : températures d'entrée et de
sortie d'eau, température extérieure, consignes, mode, état du compresseur. Ce sont tes
**valeurs de référence**.

## 4. Trouver la liaison : `probe`

```bash
hcygnus2mqtt probe --port COM3 --units 1-10
```

L'outil essaie 9600, 19200 et 38400 bauds, parités N et E. Une réponse « exception 2 »
est une **bonne nouvelle** : la PAC répond, elle refuse seulement l'adresse testée. Les
régulateurs Dixell sont en général en 9600 bauds, 8N1, esclave 1.

Rien ne répond ? Voir la fin de [CABLAGE.md](CABLAGE.md) (inverser A et B en premier).

Pour la suite, ajoute `--baud`, `--parity` et `--unit` si `probe` a trouvé autre chose que
9600 / N / 1.

## 5. Relever tout ce qui est lisible : `scan`

```bash
hcygnus2mqtt scan --port COM3 holding:0-2047 input:0-1023 -o scan_ref.csv
hcygnus2mqtt scan --port COM3 coil:0-511 discrete:0-511 -o scan_bits.csv
```

Le scan recoupe les plages refusées jusqu'à trouver chaque adresse lisible. Garde
`scan_ref.csv` : c'est l'état de référence, pris en même temps que tes notes papier.

## 6. Retrouver les valeurs de l'afficheur : `find`

Donne à `find` les valeurs que tu as notées :

```bash
hcygnus2mqtt find scan_ref.csv 44.8 39.6 12.5 45
```

Pour chaque valeur, il liste les adresses qui la contiennent, en essayant les facteurs
×1, ×0,1 et ×0,01 (les régulateurs Dixell stockent les températures ×10 : 448 = 44,8 °C).
Il signale aussi les **adresses voisines** qui correspondent à plusieurs valeurs : les
sondes sont souvent rangées côte à côte, c'est généralement la bonne piste.

Repères utiles pour le reste :

| Grandeur | Ce qu'on attend |
|---|---|
| Températures d'eau | proches de l'afficheur, ×0,1 |
| Température extérieure | proche de la météo, peut être négative |
| Haute pression (R410A) | ~25–30 bar en chauffage, ~20–27 bar en froid |
| Consignes | valeurs rondes (450 = 45,0 °C) qui ne bougent jamais seules |
| Paramètres | suite de petites valeurs, souvent à partir de 768, dans l'ordre du manuel |

## 7. Confirmer par le mouvement : `watch`

C'est l'étape qui transforme une hypothèse en certitude.

```bash
hcygnus2mqtt watch --port COM3 holding:0-2047 --noise 2 --log changements.csv
```

Puis, **au clavier de la PAC**, fais une action à la fois et note l'heure :

1. change la consigne chaud de 0,5 °C → un registre doit bouger d'exactement 5 ;
2. remets-la → il revient ;
3. passe en mode froid / chaud / arrêt → un registre prend 2 ou 3 valeurs distinctes ;
4. démarrage du compresseur (après sa temporisation) → un bit s'allume ;
5. dégivrage (en hiver) → un autre bit s'allume, le ventilateur s'arrête.

`--noise 2` masque les sondes qui bougent de 0,1–0,2 °C en permanence. Pour confirmer une
température, relance `watch` sans `--noise` sur quelques adresses et compare à l'afficheur
pendant plusieurs minutes.

## 8. Les changements lents : `diff`

Pour un dégivrage ou une alarme, qu'on ne peut pas provoquer à volonté :

```bash
hcygnus2mqtt scan --port COM3 holding:0-2047 -o avant.csv
# … attendre l'événement …
hcygnus2mqtt scan --port COM3 holding:0-2047 -o apres.csv
hcygnus2mqtt diff avant.csv apres.csv
```

Une fois la [phase 2](PHASE2_HOME_ASSISTANT.md) en place, l'option `debug.raw_ranges`
permet de surveiller des plages brutes pendant des jours depuis MQTT Explorer.

## 9. Écrire la table

Dans `registers/hcygnus_tech.yaml`, corrige `address`, `scale`, `bit`, `options` de
chaque registre trouvé, puis marque-le vérifié en notant comment :

```yaml
  - key: setpoint_heating
    name: Consigne chaud
    component: number
    address: 769
    scale: 0.1
    writable: true
    verified: true
    note: "consigne changée 45→45,5 au clavier : registre 769 450→455 (15/10/2026)"
```

Supprime ou laisse `verified: false` sur ce qui n'est pas confirmé : la phase 2 ignore
par défaut les registres non vérifiés. Pour valider le fichier :

```bash
pytest -q
```

## 10. Partager

Ouvre une *issue* « Table de registres » ou une Pull Request avec ta table, la référence
du régulateur et le modèle de PAC : c'est ce qui manque aujourd'hui à tous les
propriétaires de HCygnus.
