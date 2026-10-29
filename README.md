# Intel Park · plateforme de stationnement extérieur (DR Automation)

Site web de gestion (10 écrans, français / anglais / allemand), base de données **PostgreSQL 16**, API REST,
**application Android** (conducteurs et agents) et agent pour les **Raspberry Pi** des armoires de contrôle,
avec **identification Bluetooth** du conducteur à la place (la caméra prend le relais si le Bluetooth n’est
pas disponible).

```
parking-platform/
├── backend/                  serveur central (Python 3.12, FastAPI)
│   ├── app/
│   │   ├── db/sql/           0001_schema.sql · 0002_driver_app_bluetooth.sql · seed_demo.sql
│   │   ├── api/v1/           API REST : équipe, conducteurs (app_driver.py), Raspberry Pi, WebSocket
│   │   ├── services/         requêtes, alarmes, ingestion, identification (Bluetooth / caméra), jetons BLE
│   │   ├── web/              pages HTML (Jinja2), CSS, JS
│   │   └── cli.py            seed-demo · create-admin · new-controller · bay-labels · ble-public-key
│   ├── alembic/              migrations 0001, 0002
│   └── tests/                tests unitaires, API de bout en bout, sql/test_driver_sql.sql (psql)
├── android-app/              application Android Intel Park (Kotlin, Jetpack Compose)
├── raspberry-agent/          agent de l’armoire : capteurs, détecteurs Ethernet, caméras, barrières, Bluetooth
├── deploy/                   Caddyfile (HTTPS) · raspberry-pi/ (prototype tout-en-un)
├── tools/build_locales.py    source unique des traductions du site
└── docs/                     ARCHITECTURE · DATABASE · API · ANDROID · BLUETOOTH · PROTOTYPE-RASPBERRY-PI
```

## Les trois dépôts

| Dépôt | Contenu |
|---|---|
| [IntelparcAdminWebPage](https://github.com/gabrielVoisin/IntelparcAdminWebPage) (celui-ci) | serveur central, base de données, site web de gestion, déploiement |
| [CarDetector](https://github.com/gabrielVoisin/CarDetector) | agent de l’armoire (Raspberry Pi) : capteurs de présence, détecteurs, caméras, barrières, Bluetooth |
| [IntelparcAndroidApp](https://github.com/gabrielVoisin/IntelparcAndroidApp) | application Android (conducteurs et agents) |

Dans ce README, `raspberry-agent/` désigne le dépôt CarDetector et `android-app/` le dépôt IntelparcAndroidApp.
Pour le prototype, clonez les deux premiers côte à côte :

```bash
git clone https://github.com/gabrielVoisin/IntelparcAdminWebPage.git
git clone https://github.com/gabrielVoisin/CarDetector.git
cd IntelparcAdminWebPage && sudo ./deploy/raspberry-pi/install-all-in-one.sh
```

## Prototype sur un seul Raspberry Pi

```bash
sudo ./deploy/raspberry-pi/install-all-in-one.sh
```

Serveur, base, site, agent, détecteurs simulés et Bluetooth sur la même Pi. Scénario de démonstration pas à
pas : [docs/PROTOTYPE-RASPBERRY-PI.md](docs/PROTOTYPE-RASPBERRY-PI.md).

## Serveur seul (Docker)

```bash
cp .env.example .env               # DOMAIN, POSTGRES_PASSWORD, JWT_SECRET, BLE_SIGNING_KEY
docker compose up -d --build       # crée la base (alembic upgrade head) et lance le site
docker compose exec api python -m app.cli seed-demo      # données de démonstration
docker compose exec api python -m app.cli bay-labels P1 EXT /tmp/labels.pdf   # étiquettes QR des places
```

`https://<DOMAIN>` : `responsable@example.com` / `ChangeMe-2026!` (à changer immédiatement).

## Développement sans Docker

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
export DATABASE_URL=postgresql+psycopg://parking:parking@localhost:5432/parking
alembic upgrade head && python -m app.cli seed-demo
uvicorn app.main:app --reload       # http://localhost:8000 · API : /api/docs
pytest                              # tests API de bout en bout actifs si DATABASE_URL est défini
psql "postgresql://parking:parking@localhost:5432/parking" -f tests/sql/test_driver_sql.sql
```

Simuler une armoire sans matériel :

```bash
cd raspberry-agent && pip install -r requirements.txt
python -m agent -c config.simulator.yaml -v
curl -X POST localhost:8090/simulate/bay/EXT/05 -H 'Content-Type: application/json' -d '{"occupied": true}'
python -m unittest discover -s tests          # 18 tests, sans matériel
```

## Application Android

Ouvrir `android-app/` dans Android Studio › Run. Détails : [docs/ANDROID.md](docs/ANDROID.md).

## Ce qui a été vérifié

* **Base de données** : schéma v1, données de démo et migration v2 appliqués sur PostgreSQL 16 ; 13
  contrôles SQL de l’application conducteur et du Bluetooth réussis (`tests/sql/test_driver_sql.sql` :
  inscription, QR code, décision « bon conducteur » sur 5 cas, place libre pour une réservation, refus des
  chevauchements, identification en attente de sa voiture, alarme de place réservée, paiement qui évite
  l’alarme).
* **Jetons Bluetooth** : 5 tests serveur ; 11 tests agent (défis, trames MTU 23 à 517, deux téléphones à la
  fois, jeton copié, rejeu, expiration, mise en file hors ligne, détecteurs Ethernet) ; test croisé : un jeton
  émis par le serveur est accepté par la Pi. Les 7 tests d’origine de l’agent passent toujours.
* **Code** : backend et agent sans erreur Ruff (règles E, F, W, B) ; Kotlin sans erreur de syntaxe au
  compilateur Kotlin 2.0, ViewModels typés sans erreur.
* **Non exécuté ici** (pas d’accès à PyPI, au SDK Android ni à un adaptateur Bluetooth) : les tests API de
  bout en bout (`tests/test_driver_api.py`, prêts à lancer avec `pytest`), la compilation de l’APK, la
  couche BlueZ de la Pi et le Bluetooth du téléphone. À faire sur votre machine, puis un essai réel.

Documentation : [solution technique](docs/ARCHITECTURE.md) · [base de données](docs/DATABASE.md) ·
[API](docs/API.md) · [Android](docs/ANDROID.md) · [Bluetooth](docs/BLUETOOTH.md) ·
[prototype Raspberry Pi](docs/PROTOTYPE-RASPBERRY-PI.md).
