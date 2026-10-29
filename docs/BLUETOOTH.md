# Identification Bluetooth du conducteur (« place intelligente »)

La place doit savoir **qui** est garé, pour vérifier que c’est le bon utilisateur (abonné, réservation,
stationnement payé). Ordre de reconnaissance :

1. **Bluetooth** : le téléphone du conducteur s’identifie auprès du Raspberry Pi de l’armoire ;
2. **Application** : le conducteur a scanné le QR code de la place et payé ;
3. **Caméra** : la plaque lue par une caméra correspond à un véhicule enregistré (module caméra
   développé séparément ; il envoie la plaque dans `POST /device/readings`) ;
4. **Personne** : après le délai de tolérance, le moteur d’alarmes crée l’alarme « Occupée sans paiement ».

Une source plus fiable n’est jamais écrasée par une moins fiable (`bluetooth` > `app` > `camera`).
La méthode retenue est enregistrée dans `parking_sessions.identified_by` et affichée aux agents.

## Vue d’ensemble

```
 Téléphone (app Intel Park)            Raspberry Pi de l’armoire                 Serveur central
 ───────────────────────────           ─────────────────────────                 ───────────────
 clé EC P-256 (Android Keystore)       clé publique Ed25519 du serveur
        │  1. POST /app/ble/token (clé publique du téléphone)  ─────────────────────────►│
        │◄──────────────── jeton signé Ed25519 (24 h) ───────────────────────────────────│
        │                                     ▲ GET /device/config → ble.public_key ─────┘
        │  2. BLE : lit CHALLENGE  ─────────►│ défi aléatoire, usage unique, 60 s
        │  3. BLE : écrit IDENTIFY ─────────►│ vérifie : signature serveur, expiration,
        │     (jeton + preuve signée)        │ preuve du téléphone sur CE défi
        │                                    │ 4. POST /device/identifications ──────────►│ décide :
        │                                    │◄──────────── authorised / payment_required… │ bon conducteur ?
        │  5. BLE : notification + lit RESULT│ (hors ligne : « pending », mis en file)
```

* **Aucun appairage** Bluetooth : rien de secret ne circule. Le jeton est public, la preuve n’est valable
  que pour un défi et une armoire.
* **Copier un jeton ne sert à rien** : il contient la clé publique du téléphone ; sans la clé privée
  (jamais extraite du Keystore), la preuve est refusée (`bad_proof`).
* **Rejouer un échange ne sert à rien** : le défi change à chaque fois et ne sert qu’une fois.
* **Hors ligne** : l’armoire vérifie seule le jeton (clé publique du serveur en cache) ; l’identification
  est mise en file SQLite et envoyée au retour du réseau. Le téléphone reçoit `pending`.

## Jeton d’identité (serveur → téléphone)

`token = base64url(payload) "." base64url(signature Ed25519 de la chaîne ASCII base64url(payload))`

```json
{"v": 1, "cid": "<id client>", "veh": [{"id": "<id véhicule>", "plate": "GE215908"}],
 "pk": "<clé publique EC P-256 du téléphone, X.509 SubjectPublicKeyInfo, base64>", "iat": 1793000000, "exp": 1793086400}
```

* Clé de signature : `BLE_SIGNING_KEY` (base64 de 32 octets aléatoires) ; vide = dérivée de `JWT_SECRET`.
* Validité : paramètre `ble.token_hours` (24 h). L’application le renouvelle quand il reste moins de 2 h.
* Code : `backend/app/services/ble_identity.py`, `raspberry-agent/agent/identity.py`
  (test croisé : un jeton émis par le serveur est accepté par l’agent).

## Service GATT (Raspberry Pi = périphérique, téléphone = central)

| Caractéristique | UUID | Propriétés | Contenu |
|---|---|---|---|
| Service | `6e1f0001-5a2b-4c8e-9a7d-1f2e3d4c5b6a` | annoncé | nom `IntelPark-<armoire>` |
| INFO | `6e1f0002-…` | lecture | `{"v":1,"ctrl":"CTRL-01","site":"Parking 1 · Centre","online":true}` |
| CHALLENGE | `6e1f0003-…` | lecture | `{"v":1,"ctrl":"CTRL-01","ch":"<16 octets base64url>"}` (nouveau à chaque lecture) |
| IDENTIFY | `6e1f0004-…` | écriture | message découpé en trames (voir ci-dessous) |
| RESULT | `6e1f0005-…` | lecture, notification | `{"v":1,"ch":"…","ok":true,"status":"authorised","bay":"P1 · EXT · 05","bay_id":"…","online":true}` |

**Trames IDENTIFY** : `[en-tête][morceau]`, au plus MTU − 3 octets ; en-tête = index (0…127) | `0x80` sur la
dernière trame. Le téléphone demande un MTU de 247 (3 trames) ; avec le MTU minimal de 23, 35 trames.

**Message IDENTIFY** (JSON) : `{"token": "…", "proof": "<signature DER base64>", "bay": "<id place ou vide>", "rssi": -60}`
avec `proof = SHA256withECDSA("intelpark-ble-v1|<armoire>|<défi>|<id place>")`.

**RESULT** : la notification ne contient que les 8 premiers caractères du défi (« votre résultat est prêt ») ;
le téléphone lit ensuite RESULT (la lecture est propre à chaque téléphone connecté).

### Statuts renvoyés au téléphone

| status | Sens | Effet côté serveur |
|---|---|---|
| `authorised` | abonnement, réservation de cette place ou stationnement payé sur cette place | session liée au client |
| `payment_required` | reconnu mais rien de payé | alarme « Occupée sans paiement » après le délai si rien n’est payé |
| `reserved_by_other` | la place est réservée / attribuée à un autre utilisateur | **alarme « Place réservée occupée par un autre utilisateur »** |
| `wrong_bay` | le conducteur a réservé une autre place du parking | — |
| `pending` | armoire hors ligne : décision à venir | identification envoyée au retour du réseau |
| `invalid_token`, `expired_token`, `bad_proof`, `bad_challenge`, `bad_message`, `bad_frame` | refusé par l’armoire | — |

### Quelle place ?

* L’application envoie l’id de la place quand elle le connaît (QR code scanné, réservation en cours).
* Sinon le serveur rapproche l’identification de la voiture arrivée sur ce parking au plus près dans le
  temps (fenêtre `ble.match_window_min`, 10 min). Les deux ordres fonctionnent : téléphone puis voiture,
  ou voiture puis téléphone.

## Côté Raspberry Pi

* `agent/ble_gatt.py` : serveur GATT BlueZ (D-Bus), annonce LE, boucle GLib dans un thread.
* `agent/identity.py` : défis, trames, vérifications (testé sans matériel : `python -m unittest`).
* Paquets : `sudo apt install bluez python3-dbus python3-gi python3-cryptography` (fait par `install.sh`).
* L’utilisateur `parking-agent` est dans le groupe `bluetooth` (droit d’utiliser `org.bluez`).
* Si l’adaptateur manque, l’armoire continue sans Bluetooth et lève le warning
  « Bluetooth indisponible : identification des téléphones arrêtée ».

Vérifier sur la Pi :

```bash
bluetoothctl show                     # Powered: yes, Alias: IntelPark-CTRL-01
journalctl -u parking-agent -f        # "GATT service registered", "advertising as IntelPark-CTRL-01"
```

Depuis un téléphone, l’application gratuite *nRF Connect* doit voir `IntelPark-CTRL-01` et le service `6e1f0001-…`.

## Ce qui a été vérifié / ce qui reste à essayer

* Vérifié ici : format du jeton (serveur ↔ Pi, test croisé), défis, trames (MTU 23 à 517, deux téléphones
  en parallèle), refus (mauvaise signature, expiration, jeton copié, rejeu), mise en file hors ligne,
  décision « bon conducteur » en SQL sur PostgreSQL 16.
* À essayer sur le matériel : la couche BlueZ (`ble_gatt.py`) et la partie GATT de l’application Android
  (`BleIdentifier.kt`) n’ont pas pu être exécutées dans cet environnement (pas d’adaptateur Bluetooth).
