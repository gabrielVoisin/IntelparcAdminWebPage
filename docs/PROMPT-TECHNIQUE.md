# Intel Park : prompt technique (description du logiciel)

> Copiez ce document en tête d’une demande de modification. Il décrit le logiciel tel qu’il est, ses objets,
> leur imbrication et les règles à respecter. Ajoutez ensuite votre demande sous « Ma demande ».

---

## 1. Contexte

Tu travailles sur **Intel Park**, une plateforme de gestion de parkings multi-comptes éditée par **DR Automation**.
Elle est composée de trois dépôts GitHub qui partagent une même API :

| Dépôt | Rôle | Techno |
|---|---|---|
| `intelparcadminwebpage` | Serveur central : API REST `/api/v1`, site web d’administration (back office), base de données | Python 3.12, FastAPI, SQLAlchemy 2, Jinja2, PostgreSQL 16, Alembic, Docker Compose (déployé sur un Raspberry Pi) |
| `intelparcandroidapp` | App Android : conducteurs (`/api/v1/app/*`) et agents de contrôle (endpoints équipe) | Kotlin, Retrofit, kotlinx.serialization (`ignoreUnknownKeys = true`) |
| `cardetector` | Agent des armoires Raspberry Pi : caméras, capteurs de présence, barrières (`/api/v1/device/*`) | Python |

Le site web, l’app et les armoires parlent tous à la **même API**. Les règles métier sont écrites une seule fois,
côté serveur. Les pages web appellent l’API en `fetch` (en-tête `X-Requested-With: fetch`, cookie `pk_access`).

## 2. Les objets et leur imbrication

```
Plateforme
└── Organization (compte)                 organizations      commune, État, régie, propriétaire ; « DR Automation » = interne
    ├── IBAN, BIC, titulaire              (colonnes bank_*)  virement mensuel des encaissements
    ├── StaffUser (utilisateur)           staff_users        rôle admin | manager | agent | viewer ; is_superuser
    │   └── StaffSiteAccess               staff_site_access  limite un non-admin à certains parkings
    ├── Customer (conducteur créé par le compte)  customers.organization_id (facultatif)
    └── Site (= « parking »)              sites              nom, code, adresse d’entrée, GPS, capacité, management_mode
        ├── Setting (paramètres du parking)   settings.site_id      sinon valeurs par défaut de la plateforme
        ├── Tariff (prix du parking)          tariffs.site_id       sinon prix par défaut (site_id NULL)
        ├── Zone                               zones                 une zone « A » par défaut
        │   └── Bay (place)                    bays                  numéro, type, état, référence QR (IPK-XXXXXX)
        │       ├── Reservation                reservations
        │       ├── Subscription (abonné)      subscriptions (aussi site_id)
        │       └── ParkingSession (stationnement) parking_sessions (aussi site_id)
        │           ├── Payment                payments (site_id)
        │           └── PaymentAlarm → Fine    payment_alarms, fines
        ├── Controller (armoire Raspberry Pi)  controllers           clé API affichée une seule fois (hash stocké)
        │   ├── Device (appareil)              devices               capteur frontal / vertical (lié à une Bay), barrière (référence QR), caméra…
        │   └── DeviceEvent                    device_events         alarmes techniques
        └── ReportExport                       report_exports

Customer (conducteur, app Android) ── Vehicle (plaques) ── Reservation / Subscription / ParkingSession / Payment
```

Règles d’imbrication :
- Un **compte** possède des **parkings** et des **utilisateurs** ; supprimer un compte supprime ses parkings et ses utilisateurs.
- Un **parking** contient tout le reste : zones, places, armoires, appareils, prix, paramètres, réservations, abonnements,
  stationnements, paiements, alarmes. Toutes les fonctions du site s’exécutent « dans » un parking ouvert.
- Une **place** appartient à une zone, la zone au parking. Le statut affiché vient de la vue `v_bay_status`
  (free, occupied, alarm, reserved, blocked) ; les compteurs de `v_site_occupancy`.

## 3. Modes de gestion d’un parking (`sites.management_mode`, choisi à la création, non modifiable)

| Mode | Création des places | QR codes | Appareils de contrôle |
|---|---|---|---|
| `qr_code` | Générer N QR codes crée N places (numérotées après la dernière) | un par place (`bays.reference`) | aucun (refus 409 `no_devices_in_qr_mode`) |
| `presence_sensor` | Chaque capteur ajouté crée sa place | un par place (celle du capteur) | armoires + capteurs ; caméra et détection voiture sur « Appareils de contrôle » |
| `smart_barrier` | Les places sont créées avec le parking (= capacité) | un par barrière (`devices.reference`) : le scanner réserve une place libre | armoires + barrières (caméra lecture de plaques) |

Contenu d’un QR code : `intelpark:bay:<SITE>/<ZONE>/<NUM>?ref=IPK-XXXXXX&street=…&gps=lat,lng`
(barrière : `<SITE>/<ZONE>/BR-001`). L’app résout le code via `GET /api/v1/app/bays/by-code`.
La première armoire est créée automatiquement au premier capteur/barrière ; sa clé API est renvoyée une seule fois (`api_key`).

## 4. Droits

- **Super user** (`staff_users.is_superuser`, compte DR Automation, bouton « Turbo » en test) : voit et peut tout faire,
  sur tous les comptes ; seul à pouvoir supprimer un parking ou un compte (nom à retaper), suspendre un compte,
  modifier les prix et paramètres **par défaut** de la plateforme et les règles de notification.
- **Inscription publique** (`/signup`) : crée un compte et son premier utilisateur `admin`.
- Rôles d’un compte : `admin` (tout le compte, utilisateurs, banque), `manager` (crée des parkings, places, appareils, prix),
  `agent` (alarmes, amendes), `viewer` (lecture, rapports). Table `PERMISSIONS` dans `app/deps.py`.
- Cloisonnement : `allowed_site_ids(user)` renvoie `None` (super user = tout) ou l’ensemble des parkings du compte
  (restreint par `staff_site_access` pour un non-admin). **Toute requête de données doit filtrer avec ce périmètre** ;
  `check_site_access`, `get_site_or_404` pour un objet précis.
- Un compte suspendu (`organizations.is_active = false`) ne peut plus se connecter et ses parkings disparaissent de l’app.

## 5. Site web (navigation)

- En haut à droite, **« Mon compte »** : liste des parkings du compte, Utilisateurs, Coordonnées bancaires,
  Console super user (super user), Déconnexion.
- `/parkings` (Mes parkings) → **Ouvrir** un parking (`/parkings/{id}/open`, cookie `pk_site`) → toutes les pages
  du parking : Tableau de bord, Plan des places, Places et QR codes (mode QR), Capteurs / Barrières, Réservations,
  Abonnés, Clients, Tarifs, Rapports, Paramètres (parking, général, contrôle, paiements), Appareils de contrôle (capteur/barrière).
- `/parkings/new` : nom, nombre de places, adresse d’entrée, GPS, mode.
- `/account/users` (utilisateurs du compte), `/account/bank` (IBAN, BIC, relevés mensuels), `/admin` (console super user), `/signup`.
- Les pages se rafraîchissent seules (`/live/state.json` toutes les 2 s + WebSocket `/api/v1/ws`).

## 6. Endpoints ajoutés par la version multi-comptes

| Méthode | Chemin | Qui |
|---|---|---|
| POST | `/account/signup` | public |
| GET · PATCH | `/account` | connecté · admin |
| PUT | `/account/bank` | admin (IBAN mod-97, BIC 8/11) |
| GET | `/account/payouts` | admin (12 mois, frais `payouts.fee_bp`) |
| GET · POST | `/parkings` | connecté · manager (super user : `organization_id`) |
| PATCH · DELETE | `/parkings/{id}` | manager · super user (`confirm_name`) |
| POST | `/parkings/{id}/places` | manager (mode QR) |
| POST | `/parkings/{id}/sensors` · `/barriers` · `/controllers` | manager · manager · admin |
| DELETE | `/bays/{id}` · `/devices/{id}` | manager |
| GET | `/devices/{id}/qr.svg` · `/qr.pdf` | QR code d’une barrière |
| GET · PATCH · DELETE | `/admin/organizations[/{id}]` | super user |
| GET · PUT | `/settings?site_id=` · `/tariffs?site_id=` | par parking ; sans `site_id` = défauts plateforme (super user pour écrire) |

## 7. Conventions de code (à respecter)

- Schéma : chaque évolution = un fichier `backend/app/db/sql/000N_*.sql` + une révision Alembic `000N` qui l’exécute,
  puis les modèles dans `app/models.py` et les schémas Pydantic dans `app/schemas.py`.
- Logique métier dans `app/services/*.py` (lève une erreur dont le message est un **code stable**, ex. `bay_in_use`) ;
  les routeurs `app/api/v1/*.py` traduisent en HTTP `{"detail": "<code>"}` et appellent `audit(...)`.
- Lecture pour les pages : `app/services/queries.py` (SQL brut, filtré par périmètre).
- Pages : `app/web/routes.py` (`@page` = pages d’un parking ouvert, `@account_page` = Mon compte) + `app/web/templates/*.html`.
  Les boutons utilisent `data-api="METHOD /api/v1/..."`, les formulaires `data-api-form` ; `data-confirm-name` pour une suppression
  à confirmer par le nom ; une réponse avec `redirect` ou `api_key` est gérée par `static/js/app.js`.
- Textes : jamais en dur. Ajouter les clés FR/EN/DE dans `tools/build_locales.py` puis `python tools/build_locales.py`.
  Les codes d’erreur ont une clé `error.<code>` et sont listés dans `base.html` (`PK_I18N.errors`).
- Montants en centimes + devise, dates `timestamptz` UTC, clés UUID.
- Ne jamais casser l’app Android : n’enlever ni renommer aucun champ JSON existant ; ajouter seulement.
- Tests : `backend/tests/` contre PostgreSQL (`alembic upgrade head && python -m app.cli seed-demo && pytest`) ; `ruff check`.
- Comptes de démo : `responsable@example.com` / `ChangeMe-2026!` (super user DR Automation après migration 0005).

## 8. Ma demande

<décrivez ici la modification ou la fonctionnalité à ajouter>
