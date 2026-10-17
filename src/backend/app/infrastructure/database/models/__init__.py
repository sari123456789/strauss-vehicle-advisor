"""ORM models. Importing this package registers every table on `Base.metadata`."""

from app.infrastructure.database.models import reference_data
from app.infrastructure.database.models.base import Base

__all__ = ["Base", "reference_data"]
