from typing import Optional

from sqlmodel import Field, SQLModel


class PanoptoToken(SQLModel, table=True):
    """Panopto OAuth state, single row.

    Exists because Panopto rotates the refresh token on every use — an env
    var can't be the ongoing source of truth for that without every process
    that calls it stepping on every other one's value (this bit us once:
    local testing kept invalidating whatever token production was holding).
    Storing it here instead means each database (so, in practice, each
    deployment environment) is self-consistent: whichever process used the
    token last leaves the current one right where the next caller — in that
    same environment — will look for it. PANOPTO_REFRESH_TOKEN in .env is
    still used, but only as the one-time seed for a brand new environment
    that's never called Panopto yet.

    The *access* token is cached here too, and that matters more than it
    looks. Every serverless invocation is a fresh process with an empty
    in-memory cache, so without this each poll would spend the refresh token
    again and rotate it — dozens of rotations an hour, every one of them a
    chance for two overlapping invocations to race and leave the loser
    holding a dead token (which costs a human an interactive re-consent to
    recover from). Caching the access token until it actually expires drops
    that to roughly one rotation an hour.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    refresh_token: str
    updated_at: str
    # The current access token and its expiry (epoch seconds), so a cold
    # process can reuse a still-valid token instead of spending the refresh
    # token to mint a new one. See get_access_token().
    access_token: Optional[str] = None
    access_token_expires_at: Optional[float] = None
