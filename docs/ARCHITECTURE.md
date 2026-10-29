# Solution technique

## Vue d’ensemble

```
                         HTTPS (Caddy, Let’s Encrypt)
 ┌──────────────┐   cookie httpOnly   ┌───────────────────────────────┐   SQL    ┌───────────────┐
 │ Site web     │ ──────────────────► │ Serveur central               │ ───────► │ PostgreSQL 16 │
 │ (navigateur) │ ◄── WebSocket ───── │ Python 3.12 · FastAPI         │          │ 25 tables     │
 └──────────────┘                     │  • pages HTML (Jinja2)        │          └───────────────┘
 ┌──────────────┐   Bearer JWT        │  • API REST /api/v1 (OpenAPI) │
 │ App Android  │ ──────────────────► │  • WebSocket /api/v1/ws       │
 │ (Kotlin)     │ ◄── FCM / WebSocket │  • moteur d’alarmes           │
 └──────────────┘                     └───────────────▲───────────────┘
                                                      │ HTTPS · X-Controller-Key
                    ┌─────────────────────────────────┼─────────────────────────────────┐
             ┌──────┴───────┐                  ┌──────┴───────┐                  ┌──────┴───────┐
             │ Armoire P1   │                  │ Armoire P2   │                  │ Armoire P3   │
             │ Raspberry Pi │                  │ Raspberry Pi │                  │ Raspberry Pi │
             └──────┬───────┘                  └──────────────┘                  └──────────────┘
         capteurs frontaux et verticaux (MCP23017, I²C) · caméras LAPI (RTSP) · barrières (relais)
```

Un seul serveur central détient la base de données. Le site web, l’application Android et les
Raspberry Pi parlent tous à la **même API REST**, donc les règles métier (qui peut acquitter une
alarme, comment on calcule un prix, quand on ouvre la barrière) sont écrites une seule fois.

## Base de données : PostgreSQL 16

| Choix | Pourquoi |
|---|---|
| **PostgreSQL 16** | Base relationnelle libre, transactions ACID, très stable, hébergeable partout (OVH, Scaleway, AWS RDS, Azure). |
| Clés **UUID** | Générables côté Android ou Pi sans collision, pas d’identifiants devinables dans les URL. |
| **BIGINT identity** pour la télémétrie | `bay_readings` et `audit_log` grossissent vite : clés compactes et index plus petits. |
| Dates **`timestamptz` en UTC**, ISO 8601 | Même format pour Kotlin (`Instant`), Python et le Pi ; l’affichage se fait en heure de Paris. |
| Montants en **centimes** + devise ISO 4217 | Aucun arrondi flottant sur l’argent. |
| **`plate_norm`** (colonne générée) | `AB-123-CD`, `ab 123 cd` et `AB123CD` désignent la même plaque ; toutes les recherches utilisent cet index. |
| **Contrainte d’exclusion** (`btree_gist`) | La base refuse elle-même deux réservations qui se chevauchent sur une place, même si deux agents cliquent en même temps. |
| **ENUM** PostgreSQL | Statuts fermés (`open`, `acknowledged`, `fined`…) partagés tels quels avec l’app Android. |
| Triggers `updated_at`, `audit_log` | Traçabilité : qui a acquitté, qui a changé un prix. |
| Vues `v_site_occupancy`, `v_bay_status` | L’état d’une place (libre, occupée, alarme, réservée, bloquée) est calculé à un seul endroit. |
| **Alembic** | Migrations versionnées (`alembic upgrade head`) ; le schéma de référence est le fichier SQL lisible `0001_schema.sql`. |

Accès depuis Python : **SQLAlchemy 2.0** (ORM typé) + pilote **psycopg 3**. Les écrans de
synthèse utilisent des requêtes SQL explicites (`services/queries.py`) pour rester rapides.

Le détail des 25 tables est dans [DATABASE.md](DATABASE.md).

### Utilisateurs et machines : compatibles Android et Raspberry Pi

* **Utilisateurs** (`staff_users`) : mot de passe haché **scrypt**, rôle (`admin`, `manager`, `agent`,
  `viewer`), parkings autorisés, langue. Connexion par **JWT** (30 min) + **jeton de rafraîchissement**
  (30 jours, rotation à chaque usage, seule l’empreinte SHA-256 est stockée). Le site web reçoit le JWT
  dans un cookie httpOnly ; Android l’envoie dans l’en-tête `Authorization: Bearer`.
* **Machines** (`controllers`, `devices`) : chaque armoire a une clé `préfixe.secret` envoyée dans
  `X-Controller-Key` ; seule son empreinte est stockée et elle peut être régénérée depuis le site.
  La Pi télécharge sa liste d’appareils (`GET /api/v1/device/config`) : capteurs, caméras et la place
  surveillée par chacun. Ajouter une place ou un capteur se fait donc dans la base, sans toucher la Pi.
* **Idempotence** : chaque mesure, événement ou passage porte un `uid` créé par la Pi. Si la connexion
  coupe pendant un envoi, la Pi renvoie le lot et le serveur ignore les doublons.

## Serveur : Python 3.12 + FastAPI

* **FastAPI** + **Pydantic v2** : validation de chaque requête et documentation **OpenAPI** générée
  automatiquement (`/api/docs`, `/openapi.json`), qui sert à générer le client Android.
* **Jinja2** : les 10 écrans de la maquette rendus côté serveur, en **français, anglais, allemand**
  (`app/locales/*.json`, 577 clés). Les boutons appellent l’API en `fetch` ; aucun framework JavaScript.
* **WebSocket** `/api/v1/ws` : une nouvelle alarme ou une place qui se libère rafraîchit l’écran.
* **Moteur d’alarmes** (tâche de fond, toutes les 60 s) : véhicule sans paiement après le délai de
  tolérance, ou durée payée dépassée → alarme de paiement (voiture rouge).
* **Rapports** : 10 rapports en PDF (**ReportLab**) ou Excel (**openpyxl**), plaques masquées si le
  paramètre RGPD est activé.
* **Uvicorn** derrière **Caddy** (HTTPS automatique), le tout en **Docker Compose**.

## Raspberry Pi dans chaque armoire

* Raspberry Pi 4 ou 5, Raspberry Pi OS, agent Python lancé par **systemd** (redémarrage automatique).
* Entrées : capteurs de présence **frontal** et **vertical** de chaque place sur des extensions
  **MCP23017** (16 entrées chacune, bus I²C) ; boucles de détection et relais de barrière sur les GPIO.
* Caméras **LAPI** en RTSP ; un service local de lecture de plaque est optionnel (`anpr_url`).
* File d’attente **SQLite** : aucune donnée perdue pendant une coupure réseau. Après 60 s sans serveur,
  la Pi crée le warning **« Perte de connexion avec le serveur »** et l’envoie dès le retour du réseau.
* Diagnostic : capteur illisible → « Capteur de présence … défectueux » ; capteurs frontal et vertical
  en désaccord plus de 10 min → warning sur le capteur frontal ; caméra qui ne répond plus →
  alarme « Caméra défectueuse ». Les événements disparaissent tout seuls quand l’appareil revient.
* Barrière : la Pi envoie le passage, le serveur répond `open_barrier` (abonné reconnu). Hors ligne, la
  sortie reste toujours possible.

## Identification du conducteur à la place (schéma v2)

Chaque stationnement (`parking_sessions`) sait **qui** est garé et **comment** il a été reconnu :
Bluetooth d’abord (le téléphone prouve son identité à l’armoire avec un jeton signé par le serveur et une
clé du Keystore Android), puis paiement dans l’application (QR code), puis caméra (plaque lue). Le serveur
décide si c’est le bon conducteur à partir de la vue `v_customer_rights` (abonnement, réservation de cette
place, stationnement payé) ; une place réservée occupée par quelqu’un d’autre lève l’alarme
`reserved_bay_misuse`. Détails : [BLUETOOTH.md](BLUETOOTH.md).

Les détecteurs de voiture peuvent être reliés à l’armoire en **Ethernet** (`io_backend: network`) : ils
poussent leur état sur le réseau local de l’armoire, qui le traite comme les capteurs câblés (anti-rebond,
défauts, file hors ligne). Le prototype tout-en-un sur un Raspberry Pi est décrit dans
[PROTOTYPE-RASPBERRY-PI.md](PROTOTYPE-RASPBERRY-PI.md).

## Application Android

Application **Kotlin / Jetpack Compose** dans `android-app/` : espace conducteur (inscription en 4 étapes,
QR code et paiement, carte OpenStreetMap, réservation, abonnement, Bluetooth) et espace agent (contrôle en
direct, carte et guidage GPS, vérification, photo et contravention). Retrofit + OkHttp (JWT, rafraîchissement
automatique), jetons chiffrés par le Keystore, CameraX + ML Kit sur le téléphone. **Firebase Cloud
Messaging** reste à brancher (jeton enregistré par `POST /api/v1/auth/push-token`). Voir [ANDROID.md](ANDROID.md).

## Sécurité

HTTPS partout ; mots de passe scrypt ; JWT courts ; cookie httpOnly `SameSite=Strict` + en-tête
anti-CSRF obligatoire pour les écritures du site ; droits vérifiés côté serveur pour chaque action
(matrice de la page « Équipe de gestion ») ; clés d’armoire hachées et régénérables ; journal d’audit ;
durées de conservation RGPD paramétrables.

## Montée en charge

Un serveur 2 vCPU / 4 Go suffit pour 3 parkings et quelques milliers de mesures par heure. Pour
plusieurs instances : remplacer le diffuseur WebSocket en mémoire par PostgreSQL `LISTEN/NOTIFY` ou
Redis (même interface `publish()` / `subscribe()` dans `services/realtime.py`), et partitionner
`bay_readings` par mois.
