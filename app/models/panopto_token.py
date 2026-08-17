from typing import Optional

from sqlmodel import Field, SQLModel


class PanoptoToken(SQLModel, table=True):
    """The current Panopto refresh token, single row.

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
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    refresh_token: str
    updated_at: str
