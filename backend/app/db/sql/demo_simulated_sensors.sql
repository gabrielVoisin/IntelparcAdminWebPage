-- Simulated presence sensors for testing the dashboard (CarDetector sensor_simulator/).
-- CTRL-01 sensors CP-002..CP-056 watch the other outdoor bays of P1 (EXT 01..56 except 05, which is
-- the real camera CP-001). Their address "sim:<bay>" is not sent to the real agent (GET /device/config),
-- only to the simulator (GET /device/config?simulated=true).
-- python -m app.cli simulated-sensors on|off
WITH free_bays AS (
  SELECT b.id, b.number, row_number() OVER (ORDER BY b.number) AS n
  FROM bays b JOIN zones z ON z.id = b.zone_id JOIN sites s ON s.id = z.site_id
  WHERE s.code = 'P1' AND z.code = 'EXT' AND b.number <> '05'
)
UPDATE devices d
SET address = 'sim:' || f.number, label = 'Capteur simulé', status = 'ok', bay_id = f.id, last_seen_at = now()
FROM free_bays f, controllers c
WHERE c.id = d.controller_id AND c.code = 'CTRL-01' AND d.code = 'CP-' || lpad((f.n + 1)::text, 3, '0');
