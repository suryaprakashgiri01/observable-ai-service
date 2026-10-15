from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MeOut(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "5eff4256-f312-42f9-86ad-139135e37c2a",
                "email": "demo@example.com",
                "name": "Demo User",
                "role": "user",
                "key_prefix": "oas_local_",
            }
        }
    )

    id: uuid.UUID
    email: str
    name: str
    role: Literal["admin", "user"]
    key_prefix: str = Field(
        description="First characters of the key used; the key is never echoed."
    )


class AdminUserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    role: Literal["admin", "user"]
    is_active: bool
    created_at: datetime
    api_keys: int = Field(description="Active (not revoked) keys.")
    last_used_at: datetime | None = Field(description="Most recent use of any of the user's keys.")


class AdminUserList(BaseModel):
    items: list[AdminUserOut]
    total: int
