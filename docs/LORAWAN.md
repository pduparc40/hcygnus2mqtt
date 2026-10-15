# Mise en service LoRaWAN (Dragino RS485W-LB)

```
 PAC MTA ─RS485─ Dragino RS485W-LB ))) passerelle LoRaWAN ── TTN / ChirpStack
                                                                 │ MQTT (uplinks / downlinks)
                                                          hcygnus2mqtt ── MQTT ── Home Assistant
```

Le Dragino est un **maître Modbus autonome** : à chaque cycle, il se réveille, envoie
jusqu'à 15 requêtes de lecture à la PAC, garde les octets utiles de chaque réponse et les
envoie dans un uplink. `hcygnus2mqtt` reçoit l'uplink (via le serveur LoRaWAN), le
décode avec la table de registres et publie les entités Home Assistant.

Le RS485W-LB est la variante du RS485-LB avec une sortie 12 V, même firmware, mêmes
commandes : tout ce qui suit vaut pour les deux.

## 1. Ce qu'il faut

- un **Dragino RS485W-LB-EU868** (pile Li-SOCl2 8500 mAh, ou version -LS solaire) ;
- une **couverture LoRaWAN** : réseau public The Things Network si une passerelle est à
  portée, sinon ta propre passerelle (ex. Dragino LPS8N) avec TTN ou **ChirpStack** en local ;
- pour la configuration : l'appli BLE de Dragino, ou un adaptateur USB-TTL 3,3 V pour la
  console AT (voir le manuel Dragino) ;
- pour la **découverte des registres** : un adaptateur USB-RS485 à quelques euros (fortement
  conseillé, voir plus bas).

## 2. Les limites à connaître

| Limite | Valeur | Conséquence |
|---|---|---|
| Commandes par cycle | 15 (`AT+COMMAND1…F`) | `hcygnus2mqtt dragino` regroupe les registres en ≤ 15 lectures |
| Réponse découpée | 100 octets max | 47 registres max par lecture |
| Données par uplink (EU868) | 46 octets ≈ 23 registres | au-delà : mode multi-segments (`AT+DATAUP=1`) |
| Classe LoRaWAN | A | un downlink (commande) n'est livré qu'**après le prochain uplink** |
| Temps d'antenne | voir tableau | fixe l'intervalle minimal |

**Intervalle d'envoi et temps d'antenne** (uplink de 25 octets, 125 kHz) :

| Facteur d'étalement | Durée d'un uplink | Max/jour (règle TTN 30 s/jour) | Intervalle mini sur TTN |
|---|---|---|---|
| SF7 | 82 ms | ~365 | ~4 min |
| SF9 | 267 ms | ~112 | ~13 min |
| SF10 | 494 ms | ~60 | ~24 min |
| SF12 | 1,97 s | ~15 | ~1 h 36 |

Le facteur d'étalement dépend de la qualité de liaison (ADR). Une passerelle proche
donne du SF7 : un envoi toutes les 5-10 minutes est alors raisonnable. Sur ta propre
passerelle ChirpStack, la règle TTN ne s'applique pas, seul le rapport cyclique légal de
1 % (≈ 36 s/heure) compte. TTN limite aussi les downlinks à environ 10 par jour : les
commandes depuis Home Assistant doivent rester exceptionnelles.

## 3. Câblage

Voir [WIRING.md](WIRING.md) : A/B du Dragino vers A/B de l'interface RS485 de la PAC.
Le Dragino n'alimente pas la PAC et n'a pas besoin de ses sorties 5 V / 12 V ici.

## 4. Déclarer le Dragino sur le serveur LoRaWAN

**TTN** : crée une application, ajoute l'end device (DevEUI / AppEUI / AppKey sur
l'étiquette du boîtier, LoRaWAN 1.0.3, plan EU868). Dans *Integrations → MQTT*, génère
une clé API (droits *read application traffic* et *write downlink application traffic*).

**ChirpStack v4** : ajoute le device (profil EU868, classe A, OTAA) et vérifie que
l'intégration MQTT est active (par défaut, sur le Mosquitto de ChirpStack).

Renseigne ensuite la section `lorawan:` de `config.yaml`.

## 5. Générer et appliquer la configuration du Dragino

```bash
hcygnus2mqtt dragino -c config.yaml
```

Cette commande :
- regroupe les registres de la table en blocs de lecture et affiche le plan (quel bloc,
  combien d'octets, à quel endroit de l'uplink) ;
- écrit `dragino_plan.json` : la passerelle s'en sert pour décoder, **il doit
  correspondre à ce qui est programmé dans le Dragino** ;
- écrit `dragino/dragino_at_commands.txt` : les commandes AT à envoyer (BLE ou UART) ;
- écrit `dragino/payload_formatter.js` : un formateur à coller dans la console TTN /
  ChirpStack, pratique pour voir les valeurs décodées pendant la mise au point.

Exemple de sortie pour la table de départ :

```
AT+MOD=1
AT+BAUDR=9600
AT+PARITY=0
…
AT+COMMAND1=01 03 01 00 00 05,1      ← lecture holding 256-260, CRC ajouté par le Dragino
AT+DATACUT1=15,2,4~13                ← réponse de 15 octets, on garde les octets 4 à 13
AT+CMDDL1=1000                       ← attente de la réponse : 1 s
…
AT+TDC=600000                        ← un uplink toutes les 10 min
```

**À chaque modification de la table de registres** : relance `hcygnus2mqtt dragino`,
incrémente `lorawan.payver`, reprogramme le Dragino. Le PAYVER permet à la passerelle
d'ignorer les uplinks envoyés avec un ancien plan.

## 6. Lancer la passerelle

```bash
hcygnus2mqtt run -c config.yaml
```

Dans Home Assistant apparaissent les entités de la PAC, plus la tension de pile du
Dragino, le RSSI, le SNR et l'heure du dernier uplink. La PAC passe en « indisponible »
si aucun uplink n'arrive pendant 2,5 intervalles.

## 7. Commandes (écriture) depuis Home Assistant

Désactivées par défaut. Quand un registre est vérifié et `writable: true`, et que
`allow_writes: true` :

1. HA publie la nouvelle valeur ;
2. la passerelle programme un **downlink 0xA8** contenant la trame Modbus d'écriture (FC16) ;
3. le serveur la livre **après le prochain uplink** du Dragino (jusqu'à un intervalle d'attente) ;
4. le Dragino l'envoie à la PAC et remonte la réponse ; la passerelle la reconnaît (CRC) et met à jour HA.

Sans confirmation au bout de 3 intervalles, l'écriture est signalée perdue dans le journal.

## 8. Lire la PAC à distance

Pour vérifier un registre sans se déplacer :

```bash
hcygnus2mqtt lora-interval -c config.yaml 120     # accélère temporairement les uplinks
hcygnus2mqtt lora-read -c config.yaml holding 768 10
hcygnus2mqtt lora-scan -c config.yaml holding:256-319 --chunk 16 -o distant.csv
hcygnus2mqtt lora-interval -c config.yaml 600     # retour à la normale
```

Chaque lecture coûte un uplink, et un bloc refusé est recoupé en deux (encore des
uplinks). C'est très bien pour **confirmer** quelques registres, pas pour la découverte
complète : celle-ci se fait en local, voir [DISCOVERY.md](DISCOVERY.md).

## Points à vérifier sur ton Dragino

Le manuel Dragino est imprécis sur certains détails. Ce qui est supposé ici :

- les positions `AT+DATACUT` comptent à partir de 1 (exemple du manuel cohérent avec ça) ;
- les réponses absentes sont remplies de `FF` (`AT+MOD=1`), la passerelle les signale comme manquantes ;
- le downlink de changement d'intervalle est `01` + secondes sur 3 octets (format commun aux nœuds Dragino) ;
- l'uplink déclenché par un downlink `0xA8` contient la réponse Modbus brute : la passerelle
  la cherche par son CRC, sans dépendre de l'en-tête exact.

Si un de ces points diffère sur ton firmware, ouvre une *issue* avec la trace (`-v`).
