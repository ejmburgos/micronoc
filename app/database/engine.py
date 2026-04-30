from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings

settings = get_settings()
database_url = settings.database_url or settings.postgres_dsn
is_sqlite = database_url.startswith("sqlite")

engine_kwargs = {"pool_pre_ping": True}
if is_sqlite:
    engine_kwargs["connect_args"] = {
        "check_same_thread": False,
        "timeout": 30,
    }
    engine_kwargs["poolclass"] = StaticPool
else:
    engine_kwargs["pool_size"] = 5
    engine_kwargs["max_overflow"] = 5
    engine_kwargs["pool_timeout"] = 30

engine = create_engine(database_url, **engine_kwargs)


if is_sqlite:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout = 30000")
        cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.close()
