# Dictionnaire de données

Généré depuis la base PostgreSQL 16 (`backend/app/db/sql/0001_schema.sql`). Conventions : clés UUID, noms en snake_case, dates `timestamptz` en UTC (ISO 8601), montants en centimes + devise ISO 4217, langues BCP 47 (fr, en, de), plaques normalisées dans `plate_norm`.

## Énumérations

| Type | Valeurs |
|---|---|
| `alarm_reason` | no_payment, overstay, plate_not_recognised, lost_ticket, accessible_bay_misuse, reserved_bay_misuse |
| `alarm_status` | open, acknowledged, fined |
| `bay_admin_state` | open, blocked |
| `bay_kind` | standard, accessible, ev |
| `billing_mode` | started_hour, quarter_hour, minute |
| `booking_channel` | web, app, phone, desk |
| `device_kind` | camera, front_presence_sensor, vertical_presence_sensor, barrier, pay_terminal, display |
| `device_status` | ok, fault, offline |
| `event_priority` | alarm, warning, info |
| `fine_status` | issued, paid, cancelled |
| `identification_method` | bluetooth, camera, app, manual |
| `identification_status` | authorised, payment_required, reserved_by_other, wrong_bay, pending |
| `payment_method` | card, contactless, app, invoice, cash |
| `payment_status` | pending, succeeded, failed, refunded |
| `report_format` | pdf, xlsx |
| `reservation_status` | confirmed, active, completed, cancelled, no_show |
| `session_status` | open, closed |
| `staff_role` | admin, manager, agent, viewer |
| `subscription_status` | active, suspended, terminated, expired |
| `tariff_kind` | hourly, daily, monthly |

## Tables

| Table | Rôle |
|---|---|
| [`sites`](#sites) | Parkings (P1, P2, P3) : adresse, coordonnées GPS, fuseau horaire. |
| [`zones`](#zones) | Niveaux / zones d’un parking (EXT, L0, L-1, L-2). |
| [`bays`](#bays) | Places de stationnement : numéro, type (standard, PMR, VE), état administratif, position sur le plan. |
| [`staff_users`](#staff_users) | Équipe de gestion (comptes du site web et de l’application Android) : rôle, langue, mot de passe scrypt. |
| [`staff_site_access`](#staff_site_access) | Parkings visibles par un membre de l’équipe (les administrateurs voient tout). |
| [`refresh_tokens`](#refresh_tokens) | Jetons de rafraîchissement (seulement leur empreinte SHA-256), un par appareil connecté. |
| [`customers`](#customers) | Utilisateurs du parking (clients) : identité, coordonnées, langue, date d’anonymisation RGPD. |
| [`vehicles`](#vehicles) | Véhicules d’un client ; plate_norm sert à toutes les recherches de plaque. |
| [`tariffs`](#tariffs) | Prix horaire, journée et mensuel avec historique (valid_from / valid_to). |
| [`subscription_plans`](#subscription_plans) | Formules d’abonnement (noms en fr / en / de). |
| [`subscriptions`](#subscriptions) | Abonnements : client, place attitrée, période, renouvellement, retard de paiement. |
| [`reservations`](#reservations) | Réservations : place, début, fin, canal ; une contrainte d’exclusion interdit deux réservations qui se chevauchent sur la même place. |
| [`parking_sessions`](#parking_sessions) | Stationnements réels : entrée, sortie, place, plaque lue, lien abonnement / réservation. |
| [`payments`](#payments) | Paiements (stationnement, réservation, abonnement) en centimes, avec taux de TVA. |
| [`payment_alarms`](#payment_alarms) | Alarmes de paiement (voitures rouges) : motif, statut ouvert / acquitté / amende. |
| [`fines`](#fines) | Amendes envoyées depuis une alarme. |
| [`controllers`](#controllers) | Armoires de contrôle (une Raspberry Pi chacune) : clé API (empreinte), dernière connexion. |
| [`devices`](#devices) | Appareils d’une armoire : caméras, capteurs de présence frontal et vertical, barrières. |
| [`device_events`](#device_events) | Alarmes et warnings des armoires (« Caméra défectueuse »…), identifiés par event_uid pour l’idempotence. |
| [`bay_readings`](#bay_readings) | Mesures brutes des capteurs (télémétrie, BIGINT), idempotentes par reading_uid. |
| [`settings`](#settings) | Paramètres clé / valeur (JSON), globaux ou par parking. |
| [`notification_rules`](#notification_rules) | Qui est prévenu de quel événement, par e-mail, SMS ou application. |
| [`push_devices`](#push_devices) | Jetons Firebase Cloud Messaging des téléphones Android de l’équipe. |
| [`report_exports`](#report_exports) | Historique des rapports téléchargés. |
| [`audit_log`](#audit_log) | Journal de toutes les actions (qui, quoi, quand, avant / après). |

### sites

Parkings (P1, P2, P3) : adresse, coordonnées GPS, fuseau horaire.

| Colonne | Type | Obligatoire | Clé / défaut |
|---|---|---|---|
| `id` | uuid | oui | PK · défaut `gen_random_uuid()` |
| `code` | text | oui |  |
| `name` | text | oui |  |
| `address_line` | text | non |  |
| `postcode` | text | non |  |
| `city` | text | non |  |
| `country` | character | oui | défaut `'FR'` |
| `timezone` | text | oui | défaut `'Europe/Paris'` |
| `latitude` | numeric | non |  |
| `longitude` | numeric | non |  |
| `is_active` | boolean | oui | défaut `true` |
| `created_at` | timestamp with time zone | oui | défaut `now()` |
| `updated_at` | timestamp with time zone | oui | défaut `now()` |

### zones

Niveaux / zones d’un parking (EXT, L0, L-1, L-2).

| Colonne | Type | Obligatoire | Clé / défaut |
|---|---|---|---|
| `id` | uuid | oui | PK · défaut `gen_random_uuid()` |
| `site_id` | uuid | oui | → `sites` |
| `code` | text | oui |  |
| `name` | text | oui |  |
| `level` | smallint | oui | défaut `0` |
| `sort_order` | smallint | oui | défaut `0` |
| `created_at` | timestamp with time zone | oui | défaut `now()` |
| `updated_at` | timestamp with time zone | oui | défaut `now()` |

### bays

Places de stationnement : numéro, type (standard, PMR, VE), état administratif, position sur le plan.

| Colonne | Type | Obligatoire | Clé / défaut |
|---|---|---|---|
| `id` | uuid | oui | PK · défaut `gen_random_uuid()` |
| `zone_id` | uuid | oui | → `zones` |
| `number` | text | oui |  |
| `kind` | bay_kind | oui | défaut `'standard'` |
| `admin_state` | bay_admin_state | oui | défaut `'open'` |
| `blocked_reason` | text | non |  |
| `is_occupied` | boolean | oui | défaut `false` |
| `occupied_since` | timestamp with time zone | non |  |
| `plan_row` | smallint | non |  |
| `plan_col` | smallint | non |  |
| `photo_x` | numeric | non |  |
| `photo_y` | numeric | non |  |
| `created_at` | timestamp with time zone | oui | défaut `now()` |
| `updated_at` | timestamp with time zone | oui | défaut `now()` |

### staff_users

Équipe de gestion (comptes du site web et de l’application Android) : rôle, langue, mot de passe scrypt.

| Colonne | Type | Obligatoire | Clé / défaut |
|---|---|---|---|
| `id` | uuid | oui | PK · défaut `gen_random_uuid()` |
| `email` | text | oui |  |
| `full_name` | text | oui |  |
| `password_hash` | text | non |  |
| `role` | staff_role | oui | défaut `'viewer'` |
| `locale` | text | oui | défaut `'fr'` |
| `is_active` | boolean | oui | défaut `true` |
| `totp_secret` | text | non |  |
| `invited_at` | timestamp with time zone | non |  |
| `last_login_at` | timestamp with time zone | non |  |
| `created_at` | timestamp with time zone | oui | défaut `now()` |
| `updated_at` | timestamp with time zone | oui | défaut `now()` |

### staff_site_access

Parkings visibles par un membre de l’équipe (les administrateurs voient tout).

| Colonne | Type | Obligatoire | Clé / défaut |
|---|---|---|---|
| `staff_user_id` | uuid | oui | PK · → `staff_users` |
| `site_id` | uuid | oui | PK · → `sites` |

### refresh_tokens

Jetons de rafraîchissement (seulement leur empreinte SHA-256), un par appareil connecté.

| Colonne | Type | Obligatoire | Clé / défaut |
|---|---|---|---|
| `id` | uuid | oui | PK · défaut `gen_random_uuid()` |
| `staff_user_id` | uuid | oui | → `staff_users` |
| `token_hash` | text | oui |  |
| `client_name` | text | non |  |
| `expires_at` | timestamp with time zone | oui |  |
| `revoked_at` | timestamp with time zone | non |  |
| `created_at` | timestamp with time zone | oui | défaut `now()` |
| `updated_at` | timestamp with time zone | oui | défaut `now()` |

### customers

Utilisateurs du parking (clients) : identité, coordonnées, langue, date d’anonymisation RGPD.

| Colonne | Type | Obligatoire | Clé / défaut |
|---|---|---|---|
| `id` | uuid | oui | PK · défaut `gen_random_uuid()` |
| `reference` | text | oui |  |
| `first_name` | text | oui |  |
| `last_name` | text | oui |  |
| `email` | text | non |  |
| `phone` | text | non |  |
| `address_line` | text | non |  |
| `postcode` | text | non |  |
| `city` | text | non |  |
| `country` | character | oui | défaut `'FR'` |
| `locale` | text | oui | défaut `'fr'` |
| `company_name` | text | non |  |
| `anonymised_at` | timestamp with time zone | non |  |
| `created_at` | timestamp with time zone | oui | défaut `now()` |
| `updated_at` | timestamp with time zone | oui | défaut `now()` |

### vehicles

Véhicules d’un client ; plate_norm sert à toutes les recherches de plaque.

| Colonne | Type | Obligatoire | Clé / défaut |
|---|---|---|---|
| `id` | uuid | oui | PK · défaut `gen_random_uuid()` |
| `customer_id` | uuid | non | → `customers` |
| `plate_display` | text | oui |  |
| `plate_norm` | text | non | générée |
| `country` | character | non |  |
| `make` | text | non |  |
| `model` | text | non |  |
| `color` | text | non |  |
| `created_at` | timestamp with time zone | oui | défaut `now()` |
| `updated_at` | timestamp with time zone | oui | défaut `now()` |

### tariffs

Prix horaire, journée et mensuel avec historique (valid_from / valid_to).

| Colonne | Type | Obligatoire | Clé / défaut |
|---|---|---|---|
| `id` | uuid | oui | PK · défaut `gen_random_uuid()` |
| `site_id` | uuid | non | → `sites` |
| `kind` | tariff_kind | oui |  |
| `amount_cents` | integer | oui |  |
| `currency` | character | oui | défaut `'EUR'` |
| `valid_from` | timestamp with time zone | oui | défaut `now()` |
| `valid_to` | timestamp with time zone | non |  |
| `created_by` | uuid | non | → `staff_users` |
| `created_at` | timestamp with time zone | oui | défaut `now()` |
| `updated_at` | timestamp with time zone | oui | défaut `now()` |

### subscription_plans

Formules d’abonnement (noms en fr / en / de).

| Colonne | Type | Obligatoire | Clé / défaut |
|---|---|---|---|
| `id` | uuid | oui | PK · défaut `gen_random_uuid()` |
| `code` | text | oui |  |
| `name` | jsonb | oui |  |
| `price_cents` | integer | oui |  |
| `currency` | character | oui | défaut `'EUR'` |
| `period_months` | smallint | oui | défaut `1` |
| `access_hours` | text | non |  |
| `created_at` | timestamp with time zone | oui | défaut `now()` |
| `updated_at` | timestamp with time zone | oui | défaut `now()` |

### subscriptions

Abonnements : client, place attitrée, période, renouvellement, retard de paiement.

| Colonne | Type | Obligatoire | Clé / défaut |
|---|---|---|---|
| `id` | uuid | oui | PK · défaut `gen_random_uuid()` |
| `customer_id` | uuid | oui | → `customers` |
| `vehicle_id` | uuid | non | → `vehicles` |
| `site_id` | uuid | oui | → `sites` |
| `bay_id` | uuid | non | → `bays` |
| `plan_id` | uuid | oui | → `subscription_plans` |
| `price_cents` | integer | oui |  |
| `currency` | character | oui | défaut `'EUR'` |
| `starts_on` | date | oui |  |
| `ends_on` | date | oui |  |
| `auto_renew` | boolean | oui | défaut `true` |
| `status` | subscription_status | oui | défaut `'active'` |
| `payment_overdue` | boolean | oui | défaut `false` |
| `terminated_at` | timestamp with time zone | non |  |
| `created_at` | timestamp with time zone | oui | défaut `now()` |
| `updated_at` | timestamp with time zone | oui | défaut `now()` |

### reservations

Réservations : place, début, fin, canal ; une contrainte d’exclusion interdit deux réservations qui se chevauchent sur la même place.

| Colonne | Type | Obligatoire | Clé / défaut |
|---|---|---|---|
| `id` | uuid | oui | PK · défaut `gen_random_uuid()` |
| `customer_id` | uuid | oui | → `customers` |
| `vehicle_id` | uuid | non | → `vehicles` |
| `bay_id` | uuid | oui | → `bays` |
| `starts_at` | timestamp with time zone | oui |  |
| `ends_at` | timestamp with time zone | oui |  |
| `status` | reservation_status | oui | défaut `'confirmed'` |
| `channel` | booking_channel | oui | défaut `'web'` |
| `price_cents` | integer | oui | défaut `0` |
| `currency` | character | oui | défaut `'EUR'` |
| `cancelled_at` | timestamp with time zone | non |  |
| `created_at` | timestamp with time zone | oui | défaut `now()` |
| `updated_at` | timestamp with time zone | oui | défaut `now()` |

### parking_sessions

Stationnements réels : entrée, sortie, place, plaque lue, lien abonnement / réservation.

| Colonne | Type | Obligatoire | Clé / défaut |
|---|---|---|---|
| `id` | uuid | oui | PK · défaut `gen_random_uuid()` |
| `site_id` | uuid | oui | → `sites` |
| `bay_id` | uuid | non | → `bays` |
| `vehicle_id` | uuid | non | → `vehicles` |
| `plate_display` | text | non |  |
| `plate_norm` | text | non | générée |
| `reservation_id` | uuid | non | → `reservations` |
| `subscription_id` | uuid | non | → `subscriptions` |
| `entered_at` | timestamp with time zone | oui |  |
| `exited_at` | timestamp with time zone | non |  |
| `paid_until` | timestamp with time zone | non |  |
| `status` | session_status | oui | défaut `'open'` |
| `created_at` | timestamp with time zone | oui | défaut `now()` |
| `updated_at` | timestamp with time zone | oui | défaut `now()` |

### payments

Paiements (stationnement, réservation, abonnement) en centimes, avec taux de TVA.

| Colonne | Type | Obligatoire | Clé / défaut |
|---|---|---|---|
| `id` | uuid | oui | PK · défaut `gen_random_uuid()` |
| `site_id` | uuid | non | → `sites` |
| `customer_id` | uuid | non | → `customers` |
| `session_id` | uuid | non | → `parking_sessions` |
| `reservation_id` | uuid | non | → `reservations` |
| `subscription_id` | uuid | non | → `subscriptions` |
| `amount_cents` | integer | oui |  |
| `vat_rate_bp` | integer | oui | défaut `2000` |
| `currency` | character | oui | défaut `'EUR'` |
| `method` | payment_method | oui |  |
| `status` | payment_status | oui | défaut `'pending'` |
| `provider_ref` | text | non |  |
| `paid_at` | timestamp with time zone | non |  |
| `created_at` | timestamp with time zone | oui | défaut `now()` |
| `updated_at` | timestamp with time zone | oui | défaut `now()` |

### payment_alarms

Alarmes de paiement (voitures rouges) : motif, statut ouvert / acquitté / amende.

| Colonne | Type | Obligatoire | Clé / défaut |
|---|---|---|---|
| `id` | uuid | oui | PK · défaut `gen_random_uuid()` |
| `site_id` | uuid | oui | → `sites` |
| `bay_id` | uuid | non | → `bays` |
| `session_id` | uuid | non | → `parking_sessions` |
| `plate_display` | text | non |  |
| `reason` | alarm_reason | oui |  |
| `detail` | text | non |  |
| `detected_at` | timestamp with time zone | oui | défaut `now()` |
| `status` | alarm_status | oui | défaut `'open'` |
| `handled_by` | uuid | non | → `staff_users` |
| `handled_at` | timestamp with time zone | non |  |
| `created_at` | timestamp with time zone | oui | défaut `now()` |
| `updated_at` | timestamp with time zone | oui | défaut `now()` |

### fines

Amendes envoyées depuis une alarme.

| Colonne | Type | Obligatoire | Clé / défaut |
|---|---|---|---|
| `id` | uuid | oui | PK · défaut `gen_random_uuid()` |
| `alarm_id` | uuid | oui | → `payment_alarms` |
| `amount_cents` | integer | oui |  |
| `currency` | character | oui | défaut `'EUR'` |
| `status` | fine_status | oui | défaut `'issued'` |
| `issued_by` | uuid | non | → `staff_users` |
| `issued_at` | timestamp with time zone | oui | défaut `now()` |
| `paid_at` | timestamp with time zone | non |  |
| `created_at` | timestamp with time zone | oui | défaut `now()` |
| `updated_at` | timestamp with time zone | oui | défaut `now()` |

### controllers

Armoires de contrôle (une Raspberry Pi chacune) : clé API (empreinte), dernière connexion.

| Colonne | Type | Obligatoire | Clé / défaut |
|---|---|---|---|
| `id` | uuid | oui | PK · défaut `gen_random_uuid()` |
| `site_id` | uuid | oui | → `sites` |
| `code` | text | oui |  |
| `name` | text | oui |  |
| `api_key_prefix` | text | oui |  |
| `api_key_hash` | text | oui |  |
| `hardware` | text | non |  |
| `agent_version` | text | non |  |
| `ip_address` | inet | non |  |
| `last_seen_at` | timestamp with time zone | non |  |
| `is_active` | boolean | oui | défaut `true` |
| `created_at` | timestamp with time zone | oui | défaut `now()` |
| `updated_at` | timestamp with time zone | oui | défaut `now()` |

### devices

Appareils d’une armoire : caméras, capteurs de présence frontal et vertical, barrières.

| Colonne | Type | Obligatoire | Clé / défaut |
|---|---|---|---|
| `id` | uuid | oui | PK · défaut `gen_random_uuid()` |
| `controller_id` | uuid | oui | → `controllers` |
| `bay_id` | uuid | non | → `bays` |
| `code` | text | oui |  |
| `kind` | device_kind | oui |  |
| `label` | text | non |  |
| `address` | text | non |  |
| `status` | device_status | oui | défaut `'ok'` |
| `last_seen_at` | timestamp with time zone | non |  |
| `created_at` | timestamp with time zone | oui | défaut `now()` |
| `updated_at` | timestamp with time zone | oui | défaut `now()` |

### device_events

Alarmes et warnings des armoires (« Caméra défectueuse »…), identifiés par event_uid pour l’idempotence.

| Colonne | Type | Obligatoire | Clé / défaut |
|---|---|---|---|
| `id` | uuid | oui | PK · défaut `gen_random_uuid()` |
| `controller_id` | uuid | oui | → `controllers` |
| `device_id` | uuid | non | → `devices` |
| `event_uid` | text | oui |  |
| `code` | text | oui |  |
| `priority` | event_priority | oui |  |
| `message_key` | text | oui |  |
| `raised_at` | timestamp with time zone | oui |  |
| `cleared_at` | timestamp with time zone | non |  |
| `acknowledged_at` | timestamp with time zone | non |  |
| `acknowledged_by` | uuid | non | → `staff_users` |
| `created_at` | timestamp with time zone | oui | défaut `now()` |
| `updated_at` | timestamp with time zone | oui | défaut `now()` |

### bay_readings

Mesures brutes des capteurs (télémétrie, BIGINT), idempotentes par reading_uid.

| Colonne | Type | Obligatoire | Clé / défaut |
|---|---|---|---|
| `id` | bigint | oui | PK |
| `bay_id` | uuid | oui | → `bays` |
| `device_id` | uuid | non | → `devices` |
| `reading_uid` | text | oui |  |
| `is_occupied` | boolean | oui |  |
| `plate_display` | text | non |  |
| `confidence` | numeric | non |  |
| `measured_at` | timestamp with time zone | oui |  |
| `received_at` | timestamp with time zone | oui | défaut `now()` |

### settings

Paramètres clé / valeur (JSON), globaux ou par parking.

| Colonne | Type | Obligatoire | Clé / défaut |
|---|---|---|---|
| `id` | uuid | oui | PK · défaut `gen_random_uuid()` |
| `site_id` | uuid | non | → `sites` |
| `key` | text | oui |  |
| `value` | jsonb | oui |  |
| `updated_by` | uuid | non | → `staff_users` |
| `created_at` | timestamp with time zone | oui | défaut `now()` |
| `updated_at` | timestamp with time zone | oui | défaut `now()` |

### notification_rules

Qui est prévenu de quel événement, par e-mail, SMS ou application.

| Colonne | Type | Obligatoire | Clé / défaut |
|---|---|---|---|
| `id` | uuid | oui | PK · défaut `gen_random_uuid()` |
| `event` | text | oui |  |
| `channel` | text | oui |  |
| `enabled` | boolean | oui | défaut `true` |
| `created_at` | timestamp with time zone | oui | défaut `now()` |
| `updated_at` | timestamp with time zone | oui | défaut `now()` |

### push_devices

Jetons Firebase Cloud Messaging des téléphones Android de l’équipe.

| Colonne | Type | Obligatoire | Clé / défaut |
|---|---|---|---|
| `id` | uuid | oui | PK · défaut `gen_random_uuid()` |
| `staff_user_id` | uuid | oui | → `staff_users` |
| `platform` | text | oui | défaut `'android'` |
| `token` | text | oui |  |
| `last_used_at` | timestamp with time zone | non |  |
| `created_at` | timestamp with time zone | oui | défaut `now()` |
| `updated_at` | timestamp with time zone | oui | défaut `now()` |

### report_exports

Historique des rapports téléchargés.

| Colonne | Type | Obligatoire | Clé / défaut |
|---|---|---|---|
| `id` | uuid | oui | PK · défaut `gen_random_uuid()` |
| `report_type` | text | oui |  |
| `site_id` | uuid | non | → `sites` |
| `period_from` | date | oui |  |
| `period_to` | date | oui |  |
| `format` | report_format | oui |  |
| `file_size` | integer | non |  |
| `created_by` | uuid | non | → `staff_users` |
| `created_at` | timestamp with time zone | oui | défaut `now()` |
| `updated_at` | timestamp with time zone | oui | défaut `now()` |

### audit_log

Journal de toutes les actions (qui, quoi, quand, avant / après).

| Colonne | Type | Obligatoire | Clé / défaut |
|---|---|---|---|
| `id` | bigint | oui | PK |
| `actor_id` | uuid | non | → `staff_users` |
| `actor_kind` | text | oui | défaut `'staff'` |
| `action` | text | oui |  |
| `entity` | text | oui |  |
| `entity_id` | text | non |  |
| `data` | jsonb | non |  |
| `created_at` | timestamp with time zone | oui | défaut `now()` |

## Schéma v2 : application conducteur et Bluetooth (`0002_driver_app_bluetooth.sql`)

| Changement | Rôle |
|---|---|
| `customers.password_hash`, `phone_verified_at`, `last_login_at` | Compte de l’application ; e-mail unique parmi les comptes (`customers_app_email_uq`). |
| `customer_reference_seq` | Références `C-10000`, `C-10001`… des conducteurs inscrits dans l’application. |
| `customer_refresh_tokens` | Jetons de rafraîchissement des conducteurs (empreinte SHA-256, rotation). |
| `customer_phone_codes` | Codes SMS (empreinte, 10 min, 5 essais). |
| `parking_sessions.customer_id`, `identified_by`, `identified_at` | Qui est garé et comment il a été reconnu (Bluetooth > application > caméra). |
| `payments.provider` | Prestataire du paiement (`demo` dans le prototype). |
| `ble_identifications` | Téléphones vérifiés par une armoire : client, véhicule, place, RSSI, décision ; idempotent par `identification_uid`. |
| `alarm_evidence` | Photos de plaque prises par les agents avant une amende (fichier dans `EVIDENCE_DIR`, empreinte SHA-256, plaque lue). |
| `audit_log.actor_kind` | Accepte aussi `customer`. |
| Vue `v_customer_rights` | Droits actuels d’un client : abonnement actif, réservation en cours (± 30 min) ou stationnement payé, par parking et par place. |

## Vues

* `v_site_occupancy` : places libres / occupées / en alarme par parking (tableau de bord).
* `v_bay_status` : état calculé de chaque place (free, occupied, alarm, reserved, blocked), utilisé par le plan et la vue aérienne.
* `v_customer_rights` : ce à quoi un client a droit maintenant (décision « bon conducteur » du Bluetooth).
