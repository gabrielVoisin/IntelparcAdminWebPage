# Prototype : tout sur un seul Raspberry Pi

Dans le produit final, des centaines de **détecteurs de voiture** sont reliés en Ethernet à une **armoire
de contrôle** (un Raspberry Pi), qui envoie tout au **serveur central** (base PostgreSQL, site web, API de
l’application). Pour le prototype, un seul Raspberry Pi joue tous les rôles :

```
 ┌──────────────────────────── Raspberry Pi 4 / 5 (Raspberry Pi OS Bookworm 64 bits) ──────────────────────────┐
 │                                                                                                              │
 │  Docker :  PostgreSQL 16  ◄──  serveur FastAPI (API + site web)  ◄──  Caddy :80  ◄──── téléphones, navigateur  │
 │                                       ▲ 127.0.0.1:8000                                                       │
 │  systemd : agent de l’armoire ────────┘                                                                      │
 │              ├─ détecteurs simulés (HTTP :8090)   ◄── curl, ou de vrais détecteurs Ethernet plus tard        │
 │              └─ serveur Bluetooth GATT  ◄────────────── application Android (identification à la place)      │
 └──────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

## Installation (≈ 15 min)

```bash
git clone … parking-platform && cd parking-platform        # ou copier le dossier sur la Pi
sudo ./deploy/raspberry-pi/install-all-in-one.sh
```

Le script installe Docker, crée les secrets (`.env`), lance la base, le serveur et le site, charge les
données de démonstration, imprime les étiquettes QR de la zone extérieure de P1
(`bay-labels-P1-EXT.pdf`), installe l’agent avec la configuration `raspberry-agent/config.prototype.yaml`
et active le Bluetooth. Il affiche à la fin l’adresse à saisir dans l’application.

## Scénario de démonstration

1. **Application** : écran d’accueil › roue dentée › `http://<IP de la Pi>`.
2. **Créer un compte** (4 étapes). Sans passerelle SMS, le code de vérification s’affiche dans l’application
   (`SMS_DEBUG=true`, à désactiver dès qu’un fournisseur SMS est branché).
3. **Une voiture arrive** sur la place EXT 05 du parking P1 :
   ```bash
   curl -X POST http://<IP>:8090/simulate/bay/EXT/05 -H 'Content-Type: application/json' -d '{"occupied": true}'
   ```
   Le site (Plan des places) montre la place occupée quelques secondes plus tard.
4. **Scanner la place** : QR code de l’étiquette `P1/EXT/05` (ou « Saisir le numéro de place »), choisir la
   durée, payer (paiement de démonstration).
5. **Identification Bluetooth** : juste après le paiement, le téléphone se signale à l’armoire
   (`IntelPark-CTRL-01`) → « Vous êtes reconnu sur P1 · EXT · 05 ». Le site et l’espace agent affichent
   « Identifié par Bluetooth ».
6. **Mauvais conducteur** : avec un deuxième compte, s’identifier sur la place EXT 12, réservée par la
   cliente de démonstration Camille Martin → statut `reserved_by_other` et alarme « Place réservée occupée
   par un autre utilisateur » dans l’espace agent. (La réservation de démonstration couvre les 2 heures qui suivent
   le chargement des données ; ensuite, créez une réservation depuis le premier compte.)
7. **Agent** : se connecter avec `samir.ait@example.com` / `ChangeMe-2026!` (onglet Agent / Admin) :
   contrôle en direct, carte, guidage GPS, vérification de la plaque, photo et contravention.
8. **Départ** : `{"occupied": false}` sur la même place ferme le stationnement.

Comptes de démonstration (à changer) : `responsable@example.com` (admin), `samir.ait@example.com` (agent),
mot de passe `ChangeMe-2026!`.

## Brancher les vrais détecteurs (Ethernet)

Chaque détecteur envoie son état à l’armoire, et le répète au moins toutes les `detectors.timeout_s`
secondes (sinon il est déclaré défectueux) :

```
POST http://<armoire>:8090/detectors/<code de l’appareil>     {"occupied": true}
X-Detector-Token: <detectors.token>
```

Les codes (`CPV-EXT-05`, `CPF-EXT-05`…) et la place surveillée par chacun sont dans la base
(table `devices`), visibles sur la page « Appareils de contrôle ». Pensez à mettre un `token` et
`timeout_s: 30` dans `/etc/parking-agent/config.yaml`.

## Passer en production

* Serveur central sur une machine dédiée ou un cloud, avec un nom de domaine et HTTPS (`deploy/Caddyfile`).
* Une Raspberry Pi par armoire avec sa propre clé (`python -m app.cli new-controller …`).
* `SMS_DEBUG=false` + fournisseur SMS (`_send_sms` dans `app/api/v1/app_driver.py`).
* Prestataire de paiement réel (`_charge` dans le même fichier ; aujourd’hui `payments.provider = "demo"`).
* Application : retirer le trafic HTTP en clair (`res/xml/network_security_config.xml`).
