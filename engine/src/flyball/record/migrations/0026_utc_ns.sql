-- 0026: the wall-clock columns say so (D-084): a bare `_ns` is the rig's clock.
ALTER TABLE audit RENAME COLUMN time_ns TO time_utc_ns;
ALTER TABLE latch RENAME COLUMN at_ns TO at_utc_ns;
ALTER TABLE live_value RENAME COLUMN written_ns TO written_utc_ns;
