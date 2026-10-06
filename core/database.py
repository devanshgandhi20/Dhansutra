from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from core.config import settings

# If connect_args includes options, ensure search_path is set via SQL execution instead
# to avoid PgBouncer startup packet rejection
engine = create_engine(
    settings.DB_URL,
    pool_pre_ping=True
)

@event.listens_for(engine, "connect")
def set_search_path(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("SET search_path TO public;")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()