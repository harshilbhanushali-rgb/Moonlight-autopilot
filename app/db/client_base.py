from sqlalchemy.orm import DeclarativeBase


class ClientBase(DeclarativeBase):
    """Separate from app.db.base.Base on purpose — these tables are owned
    and migrated by Koushik's side. Our Alembic setup must never see or
    manage this metadata."""

    pass
