"""A2A probe worker settings tests (agent-score P2).

Spec: `sdd/agent-score/spec` agent-probes P2 · design D11. Uses a fresh
`Settings` per case (cache cleared) so the env surface is exercised exactly
as the runtime sees it — mirroring tests/test_config_x402.py.
"""

from __future__ import annotations

from app.config import Settings, _settings_cache


def _fresh() -> Settings:
    _settings_cache.cache_clear()
    return Settings()


# P2 — no PROBE_* env: worker enabled, 50 agents/cycle, 60-min interval.
def test_probe_defaults(monkeypatch):
    for var in ("PROBE_ENABLED", "PROBE_CHUNK_SIZE", "PROBE_INTERVAL_MIN"):
        monkeypatch.delenv(var, raising=False)
    s = _fresh()
    assert s.probe_enabled is True
    assert s.probe_chunk_size == 50
    assert s.probe_interval_min == 60


# P2 — explicit env overrides take precedence.
def test_probe_env_overrides(monkeypatch):
    monkeypatch.setenv("PROBE_ENABLED", "false")
    monkeypatch.setenv("PROBE_CHUNK_SIZE", "25")
    monkeypatch.setenv("PROBE_INTERVAL_MIN", "30")
    s = _fresh()
    assert s.probe_enabled is False
    assert s.probe_chunk_size == 25
    assert s.probe_interval_min == 30
