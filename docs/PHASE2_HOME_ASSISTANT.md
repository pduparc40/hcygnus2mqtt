# Phase 2 — Intégration Home Assistant

**Prérequis** : une table de registres vérifiée ([phase 1](PHASE1_IDENTIFICATION.md)) et le
câble RS485 posé jusqu'à la chaufferie ([CABLAGE.md](CABLAGE.md)).
**Résultat** : la PAC apparaît dans Home Assistant comme un appareil, avec ses
températures, états, alarmes et, si tu l'autorises, ses consignes modifiables.

```
 PAC ─RS485─ clé USB ou passerelle TCP ── hcygnus2mqtt ──MQTT── Mosquitto ── Home Assistant
                                          (interroge toutes        (auto-découverte)
                                           les 10 s)
```

## 1. Côté Home Assistant

1. Installe le module complémentaire **Mosquitto broker** et l'intégration **MQTT**
   (Paramètres → Appareils et services).
2. Crée un utilisateur dédié pour la passerelle (Paramètres → Personnes → Utilisateurs),
   par exemple `hcygnus`, et note son mot de passe.

## 2. Configurer la passerelle

```bash
cp config.example.yaml config.yaml
```

Dans `config.yaml` :
- `device.unit` et les paramètres série : ceux trouvés avec `probe` en phase 1 ;
- `modbus` : **A** pour une clé USB sur la machine qui fait tourner la passerelle,
  **B** pour une passerelle RS485 ↔ Ethernet/Wi-Fi (`type: tcp`, son adresse IP) ;
- `mqtt` : l'adresse de Home Assistant et l'utilisateur créé plus haut.

Sous Linux, désigne la clé USB par son nom stable (`ls /dev/serial/by-id/`) plutôt que
`/dev/ttyUSB0`, qui peut changer au redémarrage.

## 3. Lancer

Test au premier plan :

```bash
hcygnus2mqtt run -c config.yaml
```

Le journal doit afficher le nombre de registres interrogés, la connexion au broker, la
publication de la découverte, puis « PAC joignable ». Dans Home Assistant, l'appareil
**PAC centrale** apparaît dans l'intégration MQTT.

Ensuite, en service permanent, au choix :

**systemd** (Raspberry Pi, mini-PC Linux) :

```bash
sudo mkdir -p /opt/hcygnus2mqtt && sudo cp -r . /opt/hcygnus2mqtt
cd /opt/hcygnus2mqtt && sudo python3 -m venv .venv && sudo .venv/bin/pip install .
sudo useradd -r -G dialout hcygnus
sudo cp contrib/hcygnus2mqtt.service /etc/systemd/system/
sudo systemctl enable --now hcygnus2mqtt
journalctl -u hcygnus2mqtt -f
```

**Docker** : `docker compose up -d` (décommente la section `devices` pour une clé USB).

## 4. Ce qui est publié

| Topic | Contenu |
|---|---|
| `hcygnus2mqtt/<id>/availability` | `online` / `offline` (LWT) |
| `hcygnus2mqtt/<id>/<clé>/state` | valeur décodée (`45.2`, `ON`, `Chaud`…) |
| `hcygnus2mqtt/<id>/<clé>/set` | commande (si écriture autorisée) |
| `homeassistant/<composant>/<id>/<clé>/config` | auto-découverte |

Les valeurs ne sont republiées que lorsqu'elles changent, plus une republication
complète tous les `republish_every` cycles. Si Home Assistant redémarre, la découverte
est renvoyée automatiquement.

## 5. Autoriser les commandes

Par défaut, tout est en lecture seule. Pour modifier une consigne depuis Home Assistant,
il faut **les trois** :

1. le registre est `writable: true` dans la table ;
2. il est `verified: true` ;
3. `options.allow_writes: true` dans `config.yaml`.

Commence par une seule consigne, avec une valeur proche de l'actuelle, en regardant
l'afficheur de la PAC. La passerelle relit le registre juste après l'écriture et publie
la valeur réellement appliquée. Les bornes `min` / `max` de la table sont vérifiées avant
tout envoi.

## 6. Surveillance longue durée

Pour attraper des événements rares (dégivrages, codes d'alarme) sur des adresses encore
inconnues :

```yaml
debug:
  raw_ranges:
    - {table: holding, start: 256, count: 48}
```

Chaque adresse est publiée sur `hcygnus2mqtt/<id>/raw/holding/addr/<n>` : MQTT Explorer
en garde l'historique.
