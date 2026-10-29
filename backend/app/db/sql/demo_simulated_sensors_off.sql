-- Removes the simulated sensors: their bays become free again and the sensors "not configured".
UPDATE parking_sessions p SET status = 'closed', exited_at = now()
FROM devices d WHERE d.bay_id = p.bay_id AND d.address LIKE 'sim:%' AND p.status = 'open';
UPDATE bays b SET is_occupied = false, occupied_since = NULL
FROM devices d WHERE d.bay_id = b.id AND d.address LIKE 'sim:%';
UPDATE devices SET address = NULL, label = NULL, status = 'offline', bay_id = NULL, last_seen_at = NULL
WHERE address LIKE 'sim:%';
