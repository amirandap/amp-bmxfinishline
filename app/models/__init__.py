"""SQLAlchemy database engine & session helpers."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker, DeclarativeBase

from app.config import get_config


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        cfg = get_config()
        connect_args = {}
        if cfg.database.url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        _engine = create_engine(cfg.database.url, connect_args=connect_args, echo=False)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    return _SessionLocal


def init_db() -> None:
    """Create all tables."""
    from app.models import tables  # noqa: F401 – ensure models are imported
    Base.metadata.create_all(bind=get_engine())


def get_db():
    """FastAPI dependency yielding a DB session."""
    factory = get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()
