from alembic import context

from backend.app.core.config import get_settings
from backend.app.core.db import make_engine
from backend.app.models import Base

with make_engine(get_settings()).connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
