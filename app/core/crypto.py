"""Symmetric encryption for secrets stored in the database (BYOM API keys)."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.exceptions import AppError, ErrorCode


class SecretBox:
    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode()) if key else None

    @property
    def enabled(self) -> bool:
        return self._fernet is not None

    def encrypt(self, plaintext: str) -> str:
        if self._fernet is None:
            raise AppError(
                ErrorCode.ENCRYPTION_NOT_CONFIGURED,
                "APP_ENCRYPTION_KEY is not set; cannot store an API key",
            )
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str | None:
        """None when the key is missing or rotated: the model is then unavailable."""
        if self._fernet is None:
            return None
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken:
            return None
