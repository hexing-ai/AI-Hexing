"""Frozen additive task context history; baseline tasks need no backfill."""

statements = [
    """CREATE TABLE task_context_versions (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        created_at FLOAT NOT NULL,
        owner_id VARCHAR(36) NOT NULL REFERENCES creators(id),
        data_mode VARCHAR(8) NOT NULL,
        task_id VARCHAR(36) NOT NULL REFERENCES creation_tasks(id),
        revision INTEGER NOT NULL,
        payload JSON NOT NULL,
        UNIQUE (task_id, revision)
    )""",
    "CREATE INDEX ix_task_context_versions_owner_id ON task_context_versions (owner_id)",
    "CREATE INDEX ix_task_context_versions_task_id ON task_context_versions (task_id)",
]
