# Câblage RS485

```
 PAC (extérieur)                         chaufferie
 ┌──────────────┐   paire torsadée blindée   ┌────────────────────────────────────┐
 │ régulateur   │ A ─────────────────────── A │ clé USB-RS485 → machine HA         │
 │ + option     │ B ─────────────────────── B │   ou passerelle RS485 ↔ Ethernet   │
 │   RS485      │ GND ───────────────────── GND│   (Waveshare, Elfin EW11…)         │
 └──────────────┘   ~20 m, dans une gaine     └────────────────────────────────────┘
```

## Vérifier l'option RS485 sur la PAC

Sur la gamme HCygnus Tech, l'interface **RS485 ModBus est une option** (fiche produit
MTA, rubrique « Main Options »). **PAC hors tension, sectionneur coupé**, ouvre le tableau
électrique et cherche :

- un bornier marqué `RS485`, `A/B`, `+/-`, `D+/D-` ou `TX/RX` ;
- ou un petit module enfiché sur le régulateur (chez Dixell : convertisseur type
  **XJ485** branché sur le port série TTL / « HOT KEY » du régulateur).

Si rien n'est présent, il faut commander l'option RS485 auprès de MTA ou de
l'installateur. Les régulateurs Dixell n'ont souvent pas de driver RS485 intégré et
passent par ce convertisseur TTL → RS485.

Relève aussi la **référence exacte du régulateur** (étiquette) : elle aide énormément à
retrouver la table de registres.

## Le câble jusqu'à la chaufferie

Le RS485 supporte des centaines de mètres : 20 m ne posent aucun problème.

- **Câble** : paire torsadée blindée. Un câble bus 2×2×0,5 mm² convient, ou un câble
  réseau Cat6 extérieur (une paire pour A/B, un fil d'une autre paire pour la masse).
  Prends une version **extérieure ou enterrable** si le câble passe en tranchée.
- **Passage** : idéalement dans **une gaine à part**, ou au moins sans longer de près le
  câble d'alimentation de la PAC. S'ils doivent cohabiter, le blindage fait l'essentiel.
- **Masse** : relie la masse (GND / 0 V) de l'interface RS485 de la PAC à celle de la clé
  ou de la passerelle si les deux en ont une.
- **Blindage** : relié à la terre **d'un seul côté**, côté chaufferie.
- **Terminaison** : sur 20 m à 9600 bauds, elle n'est généralement pas nécessaire. En cas
  d'erreurs de communication, ajoute une résistance de 120 Ω entre A et B aux deux
  extrémités (beaucoup de clés et passerelles ont un cavalier pour ça).

Fais valider le passage du câble par ton électricien ou ton installateur, surtout s'il
partage la tranchée des câbles de puissance.

## Matériel côté chaufferie

| Option | Pour | Config phase 2 |
|---|---|---|
| **Clé USB-RS485** (FTDI ou CH340) | la phase 1 avec un portable ; puis la phase 2 si la machine Home Assistant est dans la chaufferie | `modbus.type: rtu` |
| **Passerelle RS485 ↔ Ethernet** (Waveshare RS485 TO ETH, Elfin EE11…) | la phase 2 si la machine HA est ailleurs dans la maison | `modbus.type: tcp` |
| **Passerelle RS485 ↔ Wi-Fi** (Elfin EW11, Waveshare RS485 TO WIFI/ETH) | idem, sans câble réseau jusqu'à la chaufferie | `modbus.type: tcp` |

Les passerelles s'alimentent en 5–36 V : un petit bloc secteur dans la chaufferie suffit.
Configure-les en **Modbus TCP ↔ RTU** (passerelle Modbus), pas en simple « port série
transparent ».

## Un seul maître sur le bus

Le Modbus RTU n'accepte qu'**un seul maître**. Ne branche pas en même temps la clé USB
du portable et la passerelle définitive, ni un autre superviseur (xWEB…) : débranche l'un
pendant que l'autre travaille.

## Si rien ne répond

1. Inverse A et B (la convention varie selon les fabricants — c'est la cause n°1).
2. Vérifie l'adresse série dans le menu du régulateur (paramètre souvent nommé `Adr`, `SEr` ou `CF..`).
3. Lance `hcygnus2mqtt probe` : il essaie les vitesses et parités usuelles.
4. Vérifie la masse commune et, sur un long câble, la terminaison 120 Ω.
