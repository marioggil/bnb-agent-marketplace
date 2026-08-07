"""Auth flow tests: EIP-191 verify, nonce, CSRF, HTMX redirects.

Spec: `sdd/marketplace-scaffold-tests/spec` auth-tests R1-R7.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app.db.models.agent import (
    AgentCache, BSC_CHAIN_ID, BSC_IDENTITY_REGISTRY, build_agent_id,
)
from app.db.models.auth_nonce import AuthNonce
from app.db.models.user import User
from app.services.auth import issue_csrf
from tests.conftest import _new_address_and_key, _sign_in, _sign_message


def _seed_agent(session, token_id: int = 1) -> str:
    aid = build_agent_id(56, BSC_IDENTITY_REGISTRY, token_id)
    session.add(AgentCache(
        agent_id=aid, chain_id=BSC_CHAIN_ID, token_id=token_id,
        registry_address=BSC_IDENTITY_REGISTRY, name=f"A{token_id}",
        supported_protocols=[], cross_chain_versions=[], raw={},
    ))
    session.commit()
    return aid


# R1 — happy verify sets cookie + persists user.
async def test_verify_happy(client, db):
    address, pk = _new_address_and_key()
    nonce = client.get(f"/auth/nonce?address={address}").json()["nonce"]
    _a, sig = _sign_message(pk, nonce)
    r = client.post("/auth/verify",
                    json={"address": address, "signature": sig, "nonce": nonce})
    assert r.status_code == 200
    assert "bnb_agent_session" in r.cookies
    assert r.json()["address"].lower() == address.lower()
    assert await db.get(User, address) is not None


# R1-ext — HTMX 200 happy path returns HX-Redirect: /.
async def test_verify_htmx_200_redirects_home(client):
    address, pk = _new_address_and_key()
    nonce = client.get(f"/auth/nonce?address={address}").json()["nonce"]
    _a, sig = _sign_message(pk, nonce)
    r = client.post("/auth/verify",
                    json={"address": address, "signature": sig, "nonce": nonce},
                    headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert r.headers.get("HX-Redirect") == "/"


# R2 — nonce single-use (replay rejected).
async def test_nonce_reuse_rejected(client):
    address, pk = _new_address_and_key()
    nonce = client.get(f"/auth/nonce?address={address}").json()["nonce"]
    _a, sig = _sign_message(pk, nonce)
    body = {"address": address, "signature": sig, "nonce": nonce}
    assert client.post("/auth/verify", json=body).status_code == 200
    assert client.post("/auth/verify", json=body).status_code == 401


# R3 — nonce TTL: expired row rejected.
async def test_expired_nonce_rejected(client, db):
    address, pk = _new_address_and_key()
    nonce = client.get(f"/auth/nonce?address={address}").json()["nonce"]
    _a, sig = _sign_message(pk, nonce)
    past = datetime.now(tz=timezone.utc) - timedelta(seconds=10)
    await db.execute(
        update(AuthNonce)
        .where(AuthNonce.address == address, AuthNonce.nonce == nonce)
        .values(expires_at=past)
    )
    await db.commit()
    assert client.post("/auth/verify",
                       json={"address": address, "signature": sig, "nonce": nonce}).status_code == 401


# R4 — wrong signer rejected.
async def test_wrong_signer_rejected(client):
    address, _ = _new_address_and_key()
    nonce = client.get(f"/auth/nonce?address={address}").json()["nonce"]
    _o, other_pk = _new_address_and_key()
    _r, bad_sig = _sign_message(other_pk, nonce)
    assert client.post("/auth/verify",
                       json={"address": address, "signature": bad_sig, "nonce": nonce}).status_code == 401


# R7 — HTMX 401 redirect on /auth/verify (W3 close).
async def test_htmx_401_redirect_to_auth(client):
    address, _ = _new_address_and_key()
    nonce = client.get(f"/auth/nonce?address={address}").json()["nonce"]
    _o, other_pk = _new_address_and_key()
    _r, bad_sig = _sign_message(other_pk, nonce)
    r = client.post("/auth/verify",
                    json={"address": address, "signature": bad_sig, "nonce": nonce},
                    headers={"HX-Request": "true"})
    assert r.headers.get("HX-Redirect") == "/auth"


# R5 — logout authenticated clears the cookie.
async def test_logout_clears_cookie(client):
    _a, cookie = _sign_in(client)
    r = client.post("/auth/logout", cookies={"bnb_agent_session": cookie})
    assert r.status_code == 204
    assert "Max-Age=0" in r.headers.get("set-cookie", "")


# R6 — CSRF header required on state-changing writes.
async def test_csrf_required_on_favorite_post(client, db):
    _a, cookie = _sign_in(client)
    aid = _seed_agent(db, 1)
    no_csrf = client.post(
        "/api/favorites", json={"agent_id": aid},
        cookies={"bnb_agent_session": cookie},
    )
    assert no_csrf.status_code == 403
    with_csrf = client.post(
        "/api/favorites", json={"agent_id": aid},
        cookies={"bnb_agent_session": cookie},
        headers={"X-CSRF-Token": issue_csrf(cookie)},
    )
    assert with_csrf.status_code == 201
