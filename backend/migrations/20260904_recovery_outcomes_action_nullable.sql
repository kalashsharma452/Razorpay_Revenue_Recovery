-- Native/manual recoveries have no AI action to reference.
ALTER TABLE recovery_outcomes
    ALTER COLUMN recovery_action_id DROP NOT NULL;
