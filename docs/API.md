# API REST v1

Base : `https://<domaine>/api/v1` · documentation interactive : `/api/docs` · contrat : `/openapi.json`.
Toutes les dates sont en UTC (ISO 8601), les montants en centimes, les erreurs au format `{"detail": "<code>"}`.

## Équipe (site web et Android) — `Authorization: Bearer <JWT>`

| Méthode | Chemin | Droit requis | Rôle |
|---|---|---|---|
| POST | `/auth/login` | — | E-mail + mot de passe → JWT + jeton de rafraîchissement |
| POST | `/auth/refresh` | — | Nouveau couple de jetons (l’ancien est invalidé) |
| POST | `/auth/logout` | connecté | Révoque le jeton de rafraîchissement |
| GET | `/auth/me` | connecté | Profil, rôle, langue |
| POST | `/auth/push-token` | connecté | Jeton FCM du téléphone |
| GET | `/sites` · `/dashboard` | view_dashboard | Parkings, indicateurs clés |
| PATCH | `/sites/{id}` | change_settings | Activer / désactiver un parking |
| GET | `/sites/{id}/zones` · `/zones/{id}/bays` · `/bays/{id}` | view_dashboard | Plan des places, détail d’une place |
| PATCH | `/bays/{id}` | rôle manager ou plus | Bloquer / débloquer une place |
| GET | `/payment-alarms` | view_dashboard | Alarmes de paiement (`status=open|acknowledged|fined|all`) |
| POST | `/payment-alarms/{id}/acknowledge` | acknowledge_alarm | Acquitter |
| POST | `/payment-alarms/{id}/fine` | issue_fine | Envoyer une amende |
| POST | `/payment-alarms/{id}/reopen` | rôle manager ou plus | Rouvrir |
| GET | `/reservations` · `/reservations/stats` | view_dashboard | Liste (`tab`, `q`) et compteurs |
| POST | `/reservations` | manage_bookings_subscriptions | Créer (refus si chevauchement) |
| PATCH | `/reservations/{id}` | manage_bookings_subscriptions | Modifier |
| POST | `/reservations/{id}/extend` · `/complete` · `/cancel` | manage_bookings_subscriptions | Prolonger, terminer, annuler |
| GET | `/subscription-plans` · `/subscriptions` · `/subscriptions/stats` | view_dashboard | Formules, abonnés, compteurs |
| POST | `/subscriptions` | manage_bookings_subscriptions | Créer |
| PATCH | `/subscriptions/{id}` | manage_bookings_subscriptions | Modifier |
| POST | `/subscriptions/{id}/renew` | manage_bookings_subscriptions | +1 mois |
| POST | `/subscriptions/{id}/suspend` · `/reactivate` · `/terminate` | manage_bookings_subscriptions | Suspendre, réactiver, résilier |
| GET | `/customers/search?q=` · `/customers/{id}` | view_dashboard | Recherche nom / plaque, fiche complète |
| POST · PATCH | `/customers` · `/customers/{id}` | edit_customers | Créer, modifier |
| POST · DELETE | `/customers/{id}/vehicles[/{vehicle_id}]` | edit_customers | Ajouter / retirer un véhicule |
| GET | `/tariffs` | view_dashboard | Prix en vigueur et historique |
| PUT | `/tariffs/{hourly|daily|monthly}` | change_prices | Nouveau prix (historisé) |
| GET | `/reports` · `/reports/exports` | connecté | Types de rapports, exports récents |
| GET | `/reports/{type}?format=pdf|xlsx&site_id=&period=` | download_reports | Télécharger un rapport |
| GET · PUT | `/settings` | view_dashboard · change_settings | Paramètres |
| GET · PUT | `/notification-rules` | view_dashboard · change_settings | Règles de notification |
| GET | `/team` · `/team/permissions` | rôle manager ou plus | Équipe, matrice des droits |
| POST | `/team/invitations` | manage_team | Inviter |
| PATCH | `/team/{id}` | manage_team | Rôle, activation |
| DELETE | `/team/{id}/invitation` | manage_team | Annuler une invitation |
| GET | `/controllers` · `/controllers/{id}/devices` | view_dashboard | Armoires, alarmes et appareils |
| POST | `/controllers/{id}/acknowledge` | acknowledge_alarm | Acquittement général d’une armoire |
| POST | `/controllers` · `/controllers/{id}/rotate-key` | rôle admin | Déclarer une armoire, régénérer sa clé (affichée une fois) |
| WS | `/ws?token=<JWT>` | connecté | Événements temps réel |

Photos de preuve (agents) : `POST /payment-alarms/{id}/evidence` (multipart : `photo` JPEG ≤ 3 Mo, `plate_read`),
`GET /payment-alarms/{id}/evidence`, `GET /payment-alarms/{id}/evidence/{evidence_id}` (droit issue_fine / view_dashboard).
Les alarmes renvoient aussi `identified_by` (`bluetooth`, `app`, `camera`), `customer_name` et `evidence_count`.

## Conducteurs (application Intel Park) — `Authorization: Bearer <jeton conducteur>`

Jeton de type « customer » : refusé par les routes de l’équipe, et inversement.

| Méthode | Chemin | Rôle |
|---|---|---|
| POST | `/app/auth/register` | Étape 1 : prénom, nom, e-mail, mot de passe → jetons + profil |
| POST | `/app/auth/login` · `/app/auth/refresh` · `/app/auth/logout` | Connexion, rotation du jeton de rafraîchissement, déconnexion |
| GET · PATCH | `/app/me` | Profil ; étape 3 : adresse |
| POST | `/app/vehicles` · DELETE `/app/vehicles/{id}` | Étape 2 : plaque (unique) |
| POST | `/app/me/phone` · `/app/me/phone/verify` | Étape 4 : code SMS à 6 chiffres (10 min, 5 essais) |
| GET | `/app/home` | Accueil : profil, stationnement en cours, prochaine réservation, abonnements |
| GET | `/app/sites?lat=&lng=` · `/app/nearest?lat=&lng=` | Parkings avec places libres et prix ; plus proche avec une place libre |
| GET | `/app/bays/by-code?code=intelpark:bay:P1/EXT/05` · `/app/bays/{id}` | Place du QR code, tarifs |
| POST | `/app/quote` | Prix d’une durée |
| POST | `/app/sessions` · `/app/sessions/{id}/extend` | Payer une place (rejoint la session ouverte par le capteur), prolonger |
| GET · POST | `/app/reservations` · POST `/app/reservations/{id}/cancel` | Réserver (place choisie par le serveur si absente), annuler (gratuit jusqu’à 1 h avant) |
| GET | `/app/subscription-plans` | Formules |
| GET · POST | `/app/subscriptions` | Abonnements du conducteur, souscrire |
| POST | `/app/ble/token` | Jeton d’identité Bluetooth (voir [BLUETOOTH.md](BLUETOOTH.md)) |

Paiements : `payments.provider = "demo"` (prototype, accepté immédiatement). Tout autre fournisseur renvoie
`501 payment_provider_not_configured` tant que `_charge()` n’est pas branché.

## Armoires (Raspberry Pi) — `X-Controller-Key: <préfixe>.<secret>`

| Méthode | Chemin | Corps | Réponse |
|---|---|---|---|
| GET | `/device/config` | — | appareils de l’armoire, place surveillée, intervalles |
| POST | `/device/heartbeat` | `{agent_version, uptime_s, cpu_temp_c, devices: [{code, status}]}` | `{ok, server_time}` |
| POST | `/device/readings` | `{readings: [{uid, device_code, is_occupied, plate?, confidence?, measured_at}]}` (max 500) | `{accepted, duplicates, rejected}` |
| POST | `/device/passages` | `{passages: [{uid, device_code, direction: in|out, plate, confidence, at}]}` | `{decisions: [{uid, open_barrier}]}` |
| POST | `/device/events` | `{events: [{uid, code, priority: alarm|warning|info, message_key, raised_at, cleared_at?}]}` | `{accepted, duplicates, rejected}` |
| POST | `/device/identifications` | `{identifications: [{uid, customer_id, vehicle_id?, bay_id?, rssi?, identified_at}]}` | `{decisions: [{uid, status, bay_id, bay_label, session_id}]}` |

`GET /device/config` renvoie aussi `ble: {enabled, public_key, site_name}` : la clé publique Ed25519 qui
permet à la Pi de vérifier hors ligne les jetons des téléphones.

Clés de message des armoires : `device.camera_faulty`, `device.front_sensor_faulty`,
`device.vertical_sensor_faulty`, `device.server_connection_lost`, `device.bluetooth_unavailable` (traduites en fr / en / de).
Renvoyer un événement avec le même `uid` et un `cleared_at` le fait passer à « Disparue ».

## Événements WebSocket

`bay.updated`, `payment_alarm.created`, `payment_alarm.updated`, `device_event`, `ble.identified`,
`controller.acknowledged`, et `ping` toutes les 25 s.
