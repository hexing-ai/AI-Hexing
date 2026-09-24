"""Frozen additive lifecycle schema. Existing tasks default to active without data rewrites."""

statements = [
    """CREATE TABLE task_lifecycles (
        task_id VARCHAR(36) NOT NULL PRIMARY KEY REFERENCES creation_tasks(id),
        state VARCHAR(16) NOT NULL,
        previous_state VARCHAR(16) NOT NULL,
        deleted_at FLOAT, purge_after FLOAT, purged_at FLOAT, cleanup_status VARCHAR(50),
        cleanup_checked_at FLOAT NOT NULL,
        CHECK (state IN ('active','archived','trashed','purged')),
        CHECK (previous_state IN ('active','archived'))
    )""",
    """CREATE TABLE task_asset_links (
        task_id VARCHAR(36) NOT NULL REFERENCES creation_tasks(id),
        asset_id VARCHAR(36) NOT NULL REFERENCES assets(id),
        PRIMARY KEY (task_id, asset_id)
    )""",
]
