"""Frozen append-only billing reconciliation schema."""

statements = [
    """CREATE TABLE cost_reconciliations (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        created_at FLOAT NOT NULL,
        owner_id VARCHAR(36) NOT NULL REFERENCES creators(id),
        data_mode VARCHAR(8) NOT NULL,
        job_id VARCHAR(36) NOT NULL REFERENCES jobs(id),
        revision INTEGER NOT NULL,
        previous_id VARCHAR(36) REFERENCES cost_reconciliations(id),
        resolution VARCHAR(20) NOT NULL,
        actual_micros INTEGER,
        hold_micros INTEGER NOT NULL,
        budget_month VARCHAR(7) NOT NULL,
        evidence_hash VARCHAR(64),
        payload JSON NOT NULL,
        UNIQUE (job_id, revision),
        CHECK (resolution IN ('billed','not_charged','reopened')),
        CHECK (actual_micros IS NULL OR actual_micros >= 0),
        CHECK (hold_micros >= 0)
    )""",
    "CREATE INDEX ix_cost_reconciliations_owner_id ON cost_reconciliations (owner_id)",
    "CREATE INDEX ix_cost_reconciliations_job_id ON cost_reconciliations (job_id)",
    "CREATE INDEX ix_cost_reconciliations_evidence_hash ON cost_reconciliations (evidence_hash)",
]
