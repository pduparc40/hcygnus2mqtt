# hcygnus2mqtt

Passerelle **Modbus RTU → MQTT** pour les pompes à chaleur **MTA HCygnus Tech**
(testé sur une HCygnus Tech 171), avec **auto-découverte Home Assistant** et une boîte à
outils pour **reconstituer la table Modbus**, que le fabricant ne publie pas.

```
 PAC MTA ──RS485──▶ adaptateur USB/Ethernet ──▶ hcygnus2mqtt ──MQTT──▶ Home Assistant
 (régulateur)                                     (Python)               (entités auto)
```

> ⚠️ **Projet communautaire, non affilié à MTA.** La table de registres livrée est une
> **hypothèse de départ** : chaque registre est marqué `verified: false` tant qu'il n'a
> pas été confirmé sur une vraie machine. Les écritures sont désactivées par défaut.

## Fonctionnalités

- Lecture des registres holding / input / coils / discrete, regroupés en blocs pour limiter le trafic
- Décodage int16/uint16/int32, facteur d'échelle, extraction de bits, énumérations
- Auto-découverte Home Assistant : `sensor`, `binary_sensor`, `number`, `select`, `switch`
- Disponibilité (LWT), republication automatique au redémarrage de Home Assistant
- Écritures protégées : il faut `allow_writes: true` **et** un registre `writable` **et** `verified`
- Modbus RTU (USB-RS485) ou Modbus TCP (passerelle RS485/Ethernet)
- Outils d'exploration : `probe`, `scan`, `watch`, `diff`, `read`
- Publication de plages brutes sur MQTT pour observer la machine depuis MQTT Explorer

## Installation

```bash
git clone https://github.com/<ton-compte>/hcygnus2mqtt.git
cd hcygnus2mqtt
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
cp config.example.yaml config.yaml   # puis édite-le
```

Ou avec Docker :

```bash
cp config.example.yaml config.yaml
docker compose up -d
```

## Démarrage rapide

1. **Câbler** la PAC (voir [docs/WIRING.md](docs/WIRING.md)) — l'interface RS485 est une option sur cette gamme.
2. **Trouver la liaison** :
   ```bash
   hcygnus2mqtt probe --port /dev/ttyUSB0 --units 1-10
   ```
3. **Explorer** et identifier les registres (méthode complète : [docs/DISCOVERY.md](docs/DISCOVERY.md)) :
   ```bash
   hcygnus2mqtt scan holding:0-1023 input:0-511 -o scan.csv
   hcygnus2mqtt watch holding:0-1023 --noise 2      # puis change la consigne au clavier
   ```
4. **Corriger** `registers/hcygnus_tech.yaml` avec ce que tu as trouvé.
5. **Lancer la passerelle** :
   ```bash
   hcygnus2mqtt run -c config.yaml
   ```
   L'appareil « PAC centrale » apparaît dans Home Assistant (intégration MQTT).
   Les registres non vérifiés sont rangés dans la catégorie *Diagnostic*.

## Topics MQTT

| Topic | Contenu |
|---|---|
| `hcygnus2mqtt/<id>/availability` | `online` / `offline` |
| `hcygnus2mqtt/<id>/<clé>/state` | valeur décodée (`45.2`, `ON`, `Chaud`…) |
| `hcygnus2mqtt/<id>/<clé>/set` | commande (si écriture autorisée) |
| `hcygnus2mqtt/<id>/raw/<table>/addr/<n>` | valeur brute (mode debug `raw_ranges`) |
| `homeassistant/<composant>/<id>/<clé>/config` | auto-découverte |

## Format de la table de registres

```yaml
registers:
  - key: water_out_temp          # identifiant (topics, entity_id)
    name: Température départ eau
    table: holding               # holding | input | coil | discrete
    address: 257                 # adresse 0-based (registre 40258 en notation "Modicon")
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

## Exécution en service (systemd)

```bash
sudo cp contrib/hcygnus2mqtt.service /etc/systemd/system/
sudo systemctl enable --now hcygnus2mqtt
journalctl -u hcygnus2mqtt -f
```

## Développement

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

## Contribuer

La contribution la plus utile : **une table de registres vérifiée** pour ton modèle
(020 à 301) avec la référence du régulateur. Ouvre une PR en indiquant comment chaque
registre a été confirmé.

## Licence

Apache 2.0 — voir [LICENSE](LICENSE).
