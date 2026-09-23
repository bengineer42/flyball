-- 0015: the discriminator is `type`, not `tag`.
--
-- Every entry that picks an implementation -- a link, a law, a feedforward,
-- a setpoint generator, a driver's nested config -- names it with `type:` now,
-- and the models forbid the old key, so a stored rig version written before
-- the rename would no longer load (`--resume`, a restart from the head, a
-- restore). The key is renamed wherever it appears in a document: the
-- documents are JSON written by `json.dumps`, where `"tag":` can only be a key
-- (a string value holding it would have its quotes escaped). A signal tag axis
-- a rig file happened to call `tag` is renamed with it.
--
-- A saved tuning's config is a law config, so it is renamed too. What a past
-- session recorded (`session.config`, a controller's `law` and `feedforward`)
-- is the record of what was built and is never loaded again, so it keeps the
-- spelling it was written with.

UPDATE rig_version
SET document = replace(document, '"tag":', '"type":')
WHERE instr(document, '"tag":') > 0;

UPDATE tuning
SET config = replace(config, '"tag":', '"type":')
WHERE instr(config, '"tag":') > 0;
