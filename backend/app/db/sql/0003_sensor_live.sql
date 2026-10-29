-- Live view of a presence sensor (e.g. the camera plate reader of the prototype):
-- base URL of the sensor's own HTTP service, reached by the server (GET /status.json, GET /stream.mjpg).
ALTER TABLE devices ADD COLUMN IF NOT EXISTS live_url TEXT;
