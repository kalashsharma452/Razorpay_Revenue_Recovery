-- Action-level model identity.
-- New actions record the loaded model key ('gb' | 'lr') at decision time.
-- Existing/historical actions stay NULL (no inferred backfill); the UI
-- renders a generic "ML Model" label for NULL rather than guessing.
ALTER TABLE recovery_actions ADD COLUMN IF NOT EXISTS model VARCHAR(16);