"""Pydantic schemas for the auth surface (re-exported from routers/auth).

Kept here so tests and other routers can import the canonical shapes
without crossing into the router module.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class NonceResponse(BaseModel):
    nonce: str = Field(..., description="Hex nonce; the wallet must sign `message`.")
    message: str = Field(..., description="Canonical EIP-191 message string.")


class VerifyRequest(BaseModel):
    address: str = Field(..., description="0x + 40 hex wallet address.")
    signature: str = Field(..., description="0x-prefixed signature from personal_sign.")
    nonce: str = Field(..., description="Nonce previously returned by /auth/nonce.")


class VerifyResponse(BaseModel):
    address: str
    created_at: str
