"""Top-level package. Re-exports `create_app` for `uvicorn app.main:app`."""
from app.main import app, create_app

__all__ = ["app", "create_app"]
