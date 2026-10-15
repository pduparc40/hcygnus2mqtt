# Câblage RS485

## Ce qu'il faut vérifier sur la PAC

Sur la gamme HCygnus Tech, l'interface **RS485 ModBus est une option** (fiche produit
MTA, rubrique « Main Options »). Ouvre le tableau électrique (**PAC hors tension**,
sectionneur coupé) et cherche :

- un bornier marqué `RS485`, `A/B`, `+/-`, `D+/D-` ou `TX/RX` ;
- ou un petit module enfiché sur le régulateur (chez Dixell : convertisseur type
  **XJ485** branché sur le port série TTL / « HOT KEY » du régulateur).

Si rien n'est présent, il faut l'option RS485 auprès de MTA / de l'installateur, ou un
convertisseur compatible avec le régulateur. Les régulateurs Dixell n'ont souvent pas de
driver RS485 intégré et passent par ce convertisseur TTL → RS485.

Relève aussi la référence exacte du régulateur (étiquette) : elle aide énormément pour
retrouver la table de registres.

## Matériel côté domotique

| Option | Rôle | Config |
|---|---|---|
| **Dragino RS485W-LB** (LoRaWAN) | installation définitive, sans câble ni Wi-Fi jusqu'à la PAC | `transport: lorawan` |
| Adaptateur **USB-RS485** (FTDI ou CH340) sur un portable | **découverte des registres**, dépannage | `transport: modbus`, commandes `scan` / `watch` |
| Passerelle RS485 ↔ Ethernet/Wi-Fi (Waveshare, Elfin EW11…) | alternative filaire au Dragino | `transport: modbus`, `modbus.type: tcp` |

## Dragino RS485W-LB

- Relie les bornes **A** et **B** du Dragino aux bornes A/B de l'interface RS485 de la PAC
  (et la masse si les deux en ont une).
- Les sorties 5 V / 12 V du Dragino servent à alimenter des capteurs : **ne rien y brancher**
  ici, la PAC a sa propre alimentation.
- Le boîtier est étanche, mais place-le hors du flux d'air du ventilateur et à l'abri
  du ruissellement des dégivrages, antenne verticale, loin de la carrosserie métallique.
- **Un seul maître sur le bus** : débranche le Dragino (ou retire A/B) pendant les sessions
  de découverte avec l'adaptateur USB, sinon les deux maîtres se marchent dessus.

## Câble

- Paire torsadée blindée (type câble bus 2×2×0,5 ou Cat5/6), **une paire** pour A/B.
- Relie aussi la **masse (GND / 0 V)** de la PAC à celle de l'adaptateur si elle est disponible.
- Blindage relié à la terre **d'un seul côté**.
- Résistance de terminaison **120 Ω** entre A et B aux deux extrémités si le câble dépasse
  quelques dizaines de mètres (beaucoup d'adaptateurs ont un cavalier pour ça).
- Éloigne le câble des câbles de puissance du compresseur et des ventilateurs.

## Si rien ne répond

1. Inverse A et B (la convention varie selon les fabricants — c'est la cause n°1).
2. Vérifie l'adresse série dans le menu du régulateur (paramètre souvent nommé `Adr`, `SEr` ou `CF..`).
3. `hcygnus2mqtt probe` (adaptateur USB) essaie toutes les vitesses/parités usuelles.
4. Un seul maître sur le bus : débranche tout autre superviseur (xWEB…) pendant les essais.
