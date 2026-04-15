import os
import base64
import hashlib
import secrets as py_secrets
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from typing import Optional

from codara.config import get_config_dir

class SecretStore:
    def __init__(self, master_key: Optional[str] = None, key_path: Optional[str] = None):
        key_str = master_key or os.getenv("UAG_MASTER_KEY")
        self.key_path = Path(key_path) if key_path else get_config_dir() / "master.key"
        if key_str:
            self.key = self._normalize_key(key_str)
        else:
            self.key = self._load_or_create_persistent_key()

        self.aesgcm = AESGCM(self.key)

    def _normalize_key(self, key_str: str) -> bytes:
        raw = key_str.encode()
        if len(raw) == 32:
            return raw
        try:
            decoded = base64.b64decode(key_str, validate=True)
            if len(decoded) == 32:
                return decoded
        except Exception:
            pass
        return raw.ljust(32, b"\0")[:32]

    def _load_or_create_persistent_key(self) -> bytes:
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        if self.key_path.exists():
            encoded = self.key_path.read_text().strip()
            key = base64.b64decode(encoded)
            if len(key) != 32:
                raise ValueError(f"Invalid master key stored at {self.key_path}")
            return key

        key = AESGCM.generate_key(bit_length=256)
        self.key_path.write_text(base64.b64encode(key).decode())
        try:
            self.key_path.chmod(0o600)
        except OSError:
            pass
        return key

    def encrypt(self, plaintext: str) -> str:
        nonce = os.urandom(12)
        ciphertext = self.aesgcm.encrypt(nonce, plaintext.encode(), None)
        return base64.b64encode(nonce + ciphertext).decode()

    def decrypt(self, encoded_ciphertext: str) -> str:
        data = base64.b64decode(encoded_ciphertext)
        nonce = data[:12]
        ciphertext = data[12:]
        return self.aesgcm.decrypt(nonce, ciphertext, None).decode()

    def mask_credential(self, credential: str) -> str:
        if len(credential) <= 8:
            return "****"
        return f"{credential[:4]}...{credential[-4:]}"


def generate_api_key(prefix: str = "uagk_live_") -> str:
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    secret_part = "".join(py_secrets.choice(alphabet) for _ in range(32))
    return f"{prefix}{secret_part}"


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()

# Global instance
secrets = SecretStore()
