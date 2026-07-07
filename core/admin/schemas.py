"""Public DTOs of core/admin (Pydantic frozen)."""

from pydantic import BaseModel, ConfigDict


class AdminScreenInfo(BaseModel):
    """One entry of the admin menu (``GET /api/admin/screens``): what the current
    user is allowed to see. The router lives at ``path``; ``title_key`` is resolved
    against the i18n catalog by the caller/UI."""

    model_config = ConfigDict(frozen=True)

    slug: str
    title_key: str
    permission: str
    path: str
