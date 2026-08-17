import base64
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from placegame.errors import InvalidSecret


@dataclass(frozen=True)
class EncryptedSecret:
    nonce: bytes
    ciphertext: bytes


class SecretBox:
    def __init__(self, key_b64: str):
        key = base64.urlsafe_b64decode(key_b64 + "=" * (-len(key_b64) % 4))
        if len(key) != 32:
            raise ValueError("PLACEGAME_MASTER_KEY_B64 must decode to 32 bytes")
        self._key = key

    def encrypt(self, value: str, *, aad: str) -> EncryptedSecret:
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._key).encrypt(nonce, value.encode(), aad.encode())
        return EncryptedSecret(nonce=nonce, ciphertext=ciphertext)

    def decrypt(self, blob: EncryptedSecret, *, aad: str) -> str:
        try:
            return AESGCM(self._key).decrypt(blob.nonce, blob.ciphertext, aad.encode()).decode()
        except (InvalidTag, UnicodeDecodeError) as exc:
            raise InvalidSecret("encrypted value cannot be decrypted") from exc
