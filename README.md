# hcygnus2mqtt

Intégration **Home Assistant** des pompes à chaleur **MTA HCygnus Tech** (testé sur une
HCygnus Tech 171) via leur liaison **Modbus RTU**, transportée en **LoRaWAN** par un
**Dragino RS485W-LB**. Le projet fournit aussi la boîte à outils pour **reconstituer la
table Modbus**, que le fabricant ne publie pas.

```
 PAC MTA ─RS485─ Dragino RS485W-LB ))) LoRaWAN ))) TTN / ChirpStack ─MQTT─ hcygnus2mqtt ─MQTT─ Home Assistant
   │
   └─RS485─ adaptateur USB (temporaire) ── hcygnus2mqtt scan / watch   ← découverte des registres
```

> ⚠️ **Projet communautaire, non affilié à MTA ni à Dragino.** La table de registres
> livrée est une **hypothèse de départ** : chaque registre est marqué `verified: false`
> tant qu'il n'a pas été confirmé sur une vraie machine. Les écritures sont désactivées
> par défaut.

## Fonctionnalités

**LoRaWAN / Dragino**
- Calcule le plan d'interrogation du Dragino (≤ 15 lectures, ≤ 46 octets par uplink, bascule
  en multi-segments au-delà) et génère les **commandes AT** prêtes à coller
- Décode les uplinks depuis **The Things Network** ou **ChirpStack** (MQTT)
- Génère un formateur de payload JS pour la console TTN / ChirpStack
- Écritures depuis HA via **downlink 0xA8** (trame Modbus FC16), confirmées par l'écho de la PAC
- Lecture et balayage **à distance** des registres (`lora-read`, `lora-scan`)
- Entités de diagnostic : pile du Dragino, RSSI, SNR, dernier uplink ; indisponibilité si l'uplink tarde

**Home Assistant**
- Auto-découverte MQTT : `sensor`, `binary_sensor`, `number`, `select`, `switch`
- Registres non vérifiés rangés en *Diagnostic*, republication au redémarrage de HA

**Découverte des registres (liaison directe USB-RS485)**
- `probe` (vitesse/parité/adresse), `scan` (balayage avec découpage des plages refusées),
  `watch` (changements en direct), `diff` (avant/après), `read`

## Installation

```bash
git clone https://github.com/pduparc40/hcygnus2mqtt.git
cd hcygnus2mqtt
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
cp config.example.yaml config.yaml   # puis édite-le
```

Ou avec Docker : `docker compose up -d` (voir `docker-compose.yml`).

## Mise en route

1. **Câbler** la PAC — l'interface RS485 est une option sur cette gamme : [docs/WIRING.md](docs/WIRING.md)
2. **Découvrir les registres** avec un adaptateur USB-RS485 : [docs/DISCOVERY.md](docs/DISCOVERY.md)
   ```bash
   hcygnus2mqtt probe --port /dev/ttyUSB0 --units 1-10
   hcygnus2mqtt scan holding:0-1023 input:0-511 -o scan.csv
   hcygnus2mqtt watch holding:0-1023 --noise 2        # puis change la consigne au clavier
   ```
3. **Corriger** `registers/hcygnus_tech.yaml` avec ce que tu as trouvé (`verified: true`).
4. **Configurer le Dragino** et le serveur LoRaWAN : [docs/LORAWAN.md](docs/LORAWAN.md)
   ```bash
   hcygnus2mqtt dragino -c config.yaml     # plan + commandes AT + formateur JS
   ```
5. **Lancer la passerelle** :
   ```bash
   hcygnus2mqtt run -c config.yaml
   ```
   L'appareil « PAC centrale » apparaît dans Home Assistant (intégration MQTT).

## Ce qu'il faut savoir sur LoRaWAN

- **Pas de temps réel** : une valeur toutes les 5 à 30 minutes selon la portée radio
  (le temps d'antenne est limité, détail dans [docs/LORAWAN.md](docs/LORAWAN.md)).
- **Commandes différées** : une consigne envoyée depuis HA part au **prochain uplink**
  du Dragino (classe A). Sur TTN, ~10 downlinks par jour.
- **Taille** : ~23 registres par uplink ; au-delà, le Dragino envoie plusieurs segments.

## Topics MQTT

| Topic | Contenu |
|---|---|
| `hcygnus2mqtt/<id>/availability` | `online` / `offline` |
| `hcygnus2mqtt/<id>/<clé>/state` | valeur décodée (`45.2`, `ON`, `Chaud`…) |
| `hcygnus2mqtt/<id>/<clé>/set` | commande (si écriture autorisée) |
| `hcygnus2mqtt/<id>/{battery,rssi,snr,last_uplink}/state` | diagnostic LoRaWAN |
| `homeassistant/<composant>/<id>/<clé>/config` | auto-découverte |

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
    note: "comparé à l'afficheur, 3 relevés"

  - key: compressor_on
    name: Compresseur
    component: binary_sensor
    address: 288
    bit: 0                       # un bit d'un registre d'état

  - key: setpoint_heating
    name: Consigne chaud
    component: number
    address: 769
    scale: 0.1
    min: 25
    max: 50
    step: 0.5
    writable: true
```

## Commandes

| Commande | Rôle |
|---|---|
| `run -c config.yaml` | passerelle (LoRaWAN, ou Modbus direct si `transport: modbus`) |
| `dragino -c config.yaml` | plan d'interrogation, commandes AT, formateur JS |
| `lora-read` / `lora-scan` | lecture à distance via downlink 0xA8 |
| `lora-interval <s>` | change l'intervalle d'uplink du Dragino |
| `probe` / `scan` / `watch` / `diff` / `read` | découverte en liaison directe |

## Développement

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

## Contribuer

La contribution la plus utile : **une table de registres vérifiée** pour ton modèle
(020 à 301) avec la référence du régulateur, et tes retours sur le comportement réel du
Dragino (voir la fin de [docs/LORAWAN.md](docs/LORAWAN.md)).

## Licence

Apache 2.0 — voir [LICENSE](LICENSE).
