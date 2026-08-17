from uuid import UUID

from placegame.security.crypto import EncryptedSecret


SECRET_FORMAT_VERSION = 1
_NONCE_BYTES = 12


def encrypted_aad(table: str, record_id: UUID | str, column: str) -> str:
    return f"{table}/{record_id}/{column}"


def encode_encrypted_secret(secret: EncryptedSecret) -> bytes:
    return bytes((SECRET_FORMAT_VERSION,)) + secret.nonce + secret.ciphertext


def decode_encrypted_secret(value: bytes) -> EncryptedSecret:
    if len(value) <= 1 + _NONCE_BYTES or value[0] != SECRET_FORMAT_VERSION:
        raise ValueError("unsupported encrypted secret format")
    return EncryptedSecret(nonce=value[1 : 1 + _NONCE_BYTES], ciphertext=value[1 + _NONCE_BYTES :])
