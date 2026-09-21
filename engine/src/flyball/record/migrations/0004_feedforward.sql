-- A loop's demand is feedforward(setpoint) + the law's correction; record which.
ALTER TABLE loop ADD COLUMN feedforward TEXT;  -- JSON: the feedforward config
