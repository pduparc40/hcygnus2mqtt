# Retrouver la table Modbus sans documentation

MTA ne publie pas la table Modbus des HCygnus Tech. La méthode ci-dessous permet de la
reconstruire de façon fiable, par observation. Compte une soirée pour l'essentiel.

> **Règle d'or : on ne fait que LIRE tant qu'un registre n'est pas identifié avec certitude.**
> Écrire au hasard dans un régulateur peut modifier un paramètre de sécurité
> (pressostats, antigel, temporisations compresseur). `allow_writes` est à `false` par défaut.

## 0. Avant de commencer

Note sur papier ce qu'affiche le régulateur de la PAC à l'instant T : températures
d'entrée et de sortie d'eau, température extérieure, consignes, mode, état compresseur.
Ce sont tes « valeurs de référence ». Si tu as le manuel du régulateur (étiquette au
dos : modèle Dixell type IC2xx, iChill…), garde la liste des paramètres à côté.

## 1. Trouver la liaison : `probe`

```bash
hcygnus2mqtt probe --port /dev/ttyUSB0 --units 1-10
```

Essaie les vitesses 9600/19200/38400 en parité N et E. Une réponse du type
« exception 2 » est une **bonne nouvelle** : la PAC répond, elle refuse juste l'adresse
testée. Les régulateurs Dixell sont en général en 9600 8N1.

## 2. Cartographier ce qui existe : `scan`

```bash
hcygnus2mqtt scan --unit 1 holding:0-1023 input:0-511 -o scan_ref.csv
hcygnus2mqtt scan --unit 1 coil:0-511 discrete:0-511 -o scan_bits.csv
```

Le scan découpe automatiquement les plages refusées pour trouver les adresses lisibles.
Ouvre le CSV dans un tableur et cherche :

| Ce que tu cherches | Indice dans la colonne `div10` |
|---|---|
| Température d'eau | valeur proche de l'affichage (ex. 44.8 si l'écran affiche 44,8 °C) |
| Température extérieure | proche de la météo, peut être négative (colonne `signed`) |
| Haute pression (R410A) | ~25–30 bar en chauffage, ~20–27 en froid |
| Consignes | valeurs rondes (450 = 45,0 °C) qui ne bougent jamais seules |
| Paramètres | suite de petites valeurs à partir de ~768 qui suivent l'ordre du manuel |

## 3. Confirmer par le mouvement : `watch`

C'est l'étape qui transforme une hypothèse en certitude.

```bash
hcygnus2mqtt watch --unit 1 holding:0-1023 --noise 2 --log changements.csv
```

Puis, **au clavier de la PAC**, fais une action à la fois et note l'heure :

1. change la consigne chaud de 0,5 °C → un registre doit bouger de exactement 5 ;
2. remets-la → il revient ;
3. passe en mode froid / chaud / arrêt → un registre prend 2-3 valeurs distinctes ;
4. démarrage du compresseur (attente de la temporisation) → un bit s'allume ;
5. dégivrage (en hiver) → un autre bit s'allume, le ventilateur s'arrête.

`--noise 2` masque les sondes qui bougent de 0,1–0,2 °C en permanence. Pour les
températures, relance `watch` sans `--noise` et compare à l'écran sur plusieurs minutes.

## 4. Comparer deux instantanés : `diff`

Pour les changements lents (dégivrage, alarme) :

```bash
hcygnus2mqtt scan holding:0-1023 -o avant.csv
# … provoquer / attendre l'événement …
hcygnus2mqtt scan holding:0-1023 -o apres.csv
hcygnus2mqtt diff avant.csv apres.csv
```

## 5. Observer depuis Home Assistant (optionnel)

Avec la passerelle lancée, ajoute dans `config.yaml` :

```yaml
debug:
  raw_ranges:
    - {table: holding, start: 256, count: 48}
```

Chaque adresse est publiée sur `hcygnus2mqtt/<id>/raw/holding/addr/<n>`. Avec
MQTT Explorer tu vois l'historique de chaque valeur, et tu peux laisser tourner
plusieurs jours pour attraper les dégivrages et les alarmes.

## 6. Mettre à jour la table

Dans `registers/hcygnus_tech.yaml`, corrige `address`, `scale`, `bit`, `options`, puis :

```yaml
    verified: true
    note: "consigne changée 45→45,5 au clavier : registre 769 450→455 (15/10/2026)"
```

Seuls les registres `verified: true` **et** `writable: true` peuvent être écrits, et
seulement si `allow_writes: true`. Commence par une écriture sur une consigne, avec
une valeur proche de la valeur actuelle, en regardant l'écran de la PAC.

## 7. Partager

Ouvre une Pull Request avec ta table (et le modèle / version de firmware du régulateur) :
c'est ce qui manque aujourd'hui à tous les propriétaires de HCygnus.
