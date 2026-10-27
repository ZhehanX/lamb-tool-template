"""Runtime settings, stored in the database, edited on the /admin page.

Four keys, set by the admin:
  lamb_api_base   — base URL of the LAMB instance
  lamb_api_key    — key this tool uses to call LAMB (server-side only)
  lti_consumer_key — must match the LMS External Tool config
  lti_secret      — shared secret, must match the LMS External Tool config

Two more keys belong to the AI Workshop and are optional for launches:
  engine_model          — the LAMB engine assistant ("lamb_assistant.<id>"), an
                          empty pass-through assistant (plan T1, T13)
  engine_context_tokens — how many tokens that model can read (plan T14)

Secrets (lamb_api_key, lti_secret) are stored as-is in the local SQLite
file — the same trust level as the .env would have been. They are never
sent to the browser (the admin form shows only whether they are set)."""

import os

from .db import get_conn

RUNTIME_KEYS = ("lamb_api_base", "lamb_api_key", "lti_consumer_key", "lti_secret")
SECRET_KEYS = ("lamb_api_key", "lti_secret")
WORKSHOP_KEYS = ("engine_model", "engine_context_tokens")

# Runtime key -> environment variable used to pre-seed it on first boot.
_ENV_SEED = {
    "lamb_api_base": "LAMB_API_BASE",
    "lamb_api_key": "LAMB_API_KEY",
    "lti_consumer_key": "LTI_CONSUMER_KEY",
    "lti_secret": "LTI_SECRET",
}


def get(key: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_value(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO settings (key, value) VALUES (?, ?)
               ON CONFLICT (key) DO UPDATE SET value = excluded.value""",
            (key, value),
        )


def is_configured() -> bool:
    """True when every runtime key has a value — i.e. the tool can serve
    launches and reach LAMB."""
    return all(get(k) for k in RUNTIME_KEYS)


def public_view() -> dict:
    """What the admin form may see: plain values for non-secrets, and a
    boolean 'configured' flag for secrets (never the secret itself)."""
    view = {}
    for key in RUNTIME_KEYS + WORKSHOP_KEYS:
        value = get(key)
        if key in SECRET_KEYS:
            view[key + "_set"] = bool(value)
        else:
            view[key] = value or ""
    return view


def seed_from_env() -> None:
    """First-boot convenience: fill any unset runtime key from its env var.
    Never overwrites a value already set (the admin page is authoritative
    once used)."""
    for key, env_name in _ENV_SEED.items():
        if get(key) is None:
            env_value = os.getenv(env_name, "").strip()
            if env_value:
                set_value(key, env_value)
