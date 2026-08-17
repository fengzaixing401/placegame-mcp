from uuid import UUID

from sqlalchemy.dialects.postgresql import BYTEA
from sqlalchemy.types import TypeDecorator

from placegame.security.crypto import EncryptedSecret


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


class EncryptedSecretFrame(TypeDecorator[bytes]):
    """Only persist versioned AES-GCM frames in encrypted database columns."""

    impl = BYTEA
    cache_ok = True

    def process_bind_param(self, value: bytes | None, dialect) -> bytes | None:
        if value is None:
            return None
        if not isinstance(value, bytes):
            raise ValueError("encrypted secret must be bytes")
        decode_encrypted_secret(value)
        return value
