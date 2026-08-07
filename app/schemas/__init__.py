"""Schemas package — pydantic request/response models for the JSON API.

Each file groups one concern so the router code imports them close to
their use site (D5 boundary: routers import schemas, never models
directly).
"""
