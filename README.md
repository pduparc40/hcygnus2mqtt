# hcygnus2mqtt

Intégration **Home Assistant** des pompes à chaleur **MTA HCygnus Tech** (modèle de
référence : HCygnus Tech 171) par leur liaison **Modbus RTU**.

MTA ne publie pas la table Modbus de ces PAC. Le projet se déroule donc en deux phases :

| | Phase 1 — Identification | Phase 2 — Home Assistant |
|---|---|---|
| **But** | retrouver les registres (températures, états, consignes…) | publier la PAC dans Home Assistant |
| **Matériel** | clé USB-RS485 + ordinateur portable | clé USB-RS485 ou passerelle RS485 ↔ Ethernet/Wi-Fi, dans la chaufferie |
| **Outils** | `probe`, `scan`, `find`, `watch`, `diff`, `read` | `run` (passerelle MQTT + auto-découverte) |
| **Guide** | [docs/PHASE1_IDENTIFICATION.md](docs/PHASE1_IDENTIFICATION.md) | [docs/PHASE2_HOME_ASSISTANT.md](docs/PHASE2_HOME_ASSISTANT.md) |

```
 PAC (extérieur) ──RS485, ~20 m── chaufferie ── clé USB / passerelle ── hcygnus2mqtt ──MQTT── Home Assistant
```

Câblage et vérification de l'option RS485 : [docs/CABLAGE.md](docs/CABLAGE.md).

> ⚠️ **Projet communautaire, non affilié à MTA.** La table livrée dans
> `registers/hcygnus_tech.yaml` est une **hypothèse de départ** : tous ses registres sont
> `verified: false` tant qu'ils n'ont pas été confirmés sur une vraie machine. Les
> écritures sont désactivées par défaut.

## Feuille de route

**Phase 1 — Identification**
- [x] Outils de découverte : `probe`, `scan`, `find`, `watch`, `diff`, `read`
- [ ] Vérifier la présence de l'option RS485 et relever la référence du régulateur
- [ ] Établir la liaison (`probe`) : vitesse, parité, adresse esclave
- [ ] Températures d'eau (entrée / sortie) et extérieure
- [ ] Haute / basse pression
- [ ] États : compresseur, circulateur, ventilateur, dégivrage
- [ ] Mode de fonctionnement et marche / arrêt
- [ ] Consignes froid / chaud
- [ ] Alarmes

**Phase 2 — Home Assistant**
- [x] Passerelle Modbus → MQTT avec auto-découverte HA
- [x] Liaison RTU (clé USB) ou TCP (passerelle RS485 ↔ Ethernet/Wi-Fi)
- [ ] Câble RS485 PAC → chaufferie
- [ ] Mise en service avec la table vérifiée
- [ ] Commandes (consignes) depuis Home Assistant
- [ ] Tableau de bord Home Assistant d'exemple

## Installation

```bash
git clone https://github.com/pduparc40/hcygnus2mqtt.git
cd hcygnus2mqtt
python3 -m venv .venv
. .venv/bin/activate          # Windows : .venv\Scripts\activate
pip install -e .
hcygnus2mqtt --help
```

## Phase 1 en bref

```bash
hcygnus2mqtt probe --port COM3 --units 1-10                          # la PAC répond-elle ?
hcygnus2mqtt scan --port COM3 holding:0-2047 input:0-1023 -o scan_ref.csv
hcygnus2mqtt find scan_ref.csv 44.8 39.6 12.5                         # valeurs lues sur l'afficheur
hcygnus2mqtt watch --port COM3 holding:0-2047 --noise 2               # puis change la consigne au clavier
```

Exemple de sortie de `find` :

```
44.8 → 1 candidat(s)
  holding    257  brut=  448  ×0.1 = 44.8   (écart 0.00)
…
Adresses proches qui correspondent à plusieurs valeurs (piste prometteuse) :
  256=39.6, 257=44.8, 258=12.5
```

## Phase 2 en bref

```bash
cp config.example.yaml config.yaml    # liaison, broker MQTT, options
hcygnus2mqtt run -c config.yaml
```

L'appareil « PAC centrale » apparaît dans l'intégration MQTT de Home Assistant.
Service permanent : `contrib/hcygnus2mqtt.service` (systemd) ou `docker compose up -d`.

## Format de la table de registres

```yaml
registers:
  - key: water_out_temp          # identifiant (topics, entity_id)
    name: Température départ eau
    table: holding               # holding | input | coil | discrete
    address: 257                 # adresse 0-based
    type: int16                  # int16 | uint16 | int32 | uint32 | bool
    scale: 0.1
    unit: "°C"
    device_class: temperature
    state_class: measurement
    verified: true
    note: "= afficheur sur 3 relevés"

  - key: compressor_on
    name: Compresseur
    component: binary_sensor     # sensor | binary_sensor | number | select | switch
    address: 288
    bit: 0                       # un bit d'un registre d'état

  - key: operating_mode
    name: Mode
    component: select
    address: 300
    options: {0: "Arrêt", 1: "Froid", 2: "Chaud"}
    writable: true
```

## Développement

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

## Historique

Une variante avec transport LoRaWAN (Dragino RS485W-LB) a été explorée puis écartée au
profit d'un câble : pas de temps réel et commandes différées. Elle reste disponible sous
l'étiquette git [`lorawan-archive`](https://github.com/pduparc40/hcygnus2mqtt/tree/lorawan-archive).

## Contribuer

La contribution la plus utile : **une table de registres vérifiée** pour ton modèle (020 à
301), avec la référence du régulateur. Utilise le modèle d'issue « Table de registres ».

## Licence

Apache 2.0 — voir [LICENSE](LICENSE).
