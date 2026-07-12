"""System credential storage for provider API keys."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import keyring
from keyring.errors import KeyringError, PasswordDeleteError


SERVICE_NAME = "WenShape.LLM"


class CredentialVaultError(RuntimeError):
    pass


class CredentialVault(Protocol):
    def get(self, reference: str) -> str | None: ...

    def set(self, reference: str, secret: str) -> None: ...

    def delete(self, reference: str) -> None: ...


@dataclass(frozen=True)
class SystemCredentialVault:
    service_name: str = SERVICE_NAME

    def get(self, reference: str) -> str | None:
        try:
            return keyring.get_password(self.service_name, reference)
        except KeyringError as exc:
            raise CredentialVaultError("credential_vault_read_failed") from exc

    def set(self, reference: str, secret: str) -> None:
        if not reference or not secret:
            raise ValueError("credential_reference_and_secret_required")
        try:
            keyring.set_password(self.service_name, reference, secret)
            if keyring.get_password(self.service_name, reference) != secret:
                raise CredentialVaultError("credential_vault_verification_failed")
        except KeyringError as exc:
            raise CredentialVaultError("credential_vault_write_failed") from exc

    def delete(self, reference: str) -> None:
        if not reference:
            return
        try:
            keyring.delete_password(self.service_name, reference)
        except PasswordDeleteError:
            return
        except KeyringError as exc:
            raise CredentialVaultError("credential_vault_delete_failed") from exc


def profile_secret_reference(profile_id: str) -> str:
    return f"llm-profile:{profile_id}:api-key"


def masked_secret(secret: str | None) -> str:
    value = str(secret or "")
    if not value:
        return ""
    suffix = value[-4:] if len(value) >= 4 else value
    return f"****{suffix}"
