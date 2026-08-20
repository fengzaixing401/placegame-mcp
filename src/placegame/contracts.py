from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy.dialects.postgresql import BYTEA
from sqlalchemy.types import TypeDecorator

from placegame.security.crypto import EncryptedSecret


ActorKind = Literal["scheduler", "webui", "mcp"]


@dataclass(frozen=True)
class Actor:
    kind: ActorKind
    actor_id: str
    scopes: frozenset[str] = frozenset()


@dataclass(frozen=True)
class AccountTarget:
    account_id: UUID | None = None
    account_ids: tuple[UUID, ...] = ()
    all_enabled: bool = False

    def validate(self) -> None:
        if sum(bool(value) for value in (self.account_id, self.account_ids, self.all_enabled)) != 1:
            raise ValueError("exactly one account selector is required")


SECRET_FORMAT_VERSION = 1
_NONCE_BYTES = 12
_GCM_TAG_BYTES = 16


def encrypted_aad(table: str, record_id: UUID | str, column: str) -> str:
    return f"{table}/{record_id}/{column}"


def encode_encrypted_secret(secret: EncryptedSecret) -> bytes:
    return bytes((SECRET_FORMAT_VERSION,)) + secret.nonce + secret.ciphertext


def decode_encrypted_secret(value: bytes) -> EncryptedSecret:
    if len(value) < 1 + _NONCE_BYTES + _GCM_TAG_BYTES or value[0] != SECRET_FORMAT_VERSION:
        raise ValueError("unsupported encrypted secret format")
    return EncryptedSecret(nonce=value[1 : 1 + _NONCE_BYTES], ciphertext=value[1 + _NONCE_BYTES :])


class EncryptedSecretFrame(TypeDecorator[EncryptedSecret]):
    """Persist only encapsulated encrypted values, never caller-supplied frames."""

    impl = BYTEA
    cache_ok = True

    def process_bind_param(self, value: EncryptedSecret | None, dialect) -> bytes | None:
        if value is None:
            return None
        if not isinstance(value, EncryptedSecret):
            raise ValueError("encrypted secret must be an EncryptedSecret")
        return encode_encrypted_secret(value)

    def process_result_value(self, value: bytes | None, dialect) -> EncryptedSecret | None:
        if value is None:
            return None
        return decode_encrypted_secret(value)
