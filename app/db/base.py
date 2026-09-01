"""SQLAlchemy 2.0 DeclarativeBase with a consistent naming convention.

All FKs, indexes, and unique constraints in the project use this convention so
Alembic autogenerate produces stable names and downgrade scripts remain
predictable.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm.decl_api import DeclarativeBaseNoMeta

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBaseNoMeta):
    """Project-wide declarative base.

    Kept as a non-meta subclass so we can wire the naming convention exactly
    once. All models import this and inherit from it.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
