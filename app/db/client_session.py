from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

client_engine = create_engine(settings.client_db_url)
ClientSessionLocal = sessionmaker(bind=client_engine)


def get_client_session() -> Session:
    return ClientSessionLocal()
