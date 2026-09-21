-- A source's display name, alongside its actuator config: recorded so a
-- session's history can show "Zone 1 heater" without the rig file that made it.
ALTER TABLE source ADD COLUMN label TEXT;
