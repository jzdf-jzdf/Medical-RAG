import base64
import hashlib
from pathlib import Path

from cryptography.fernet import Fernet

from agent_api.core.config import Settings, get_settings


def _fernet(settings: Settings | None = None) -> Fernet:
    settings = settings or get_settings()
    configured = settings.model_encryption_key.strip()
    if configured:
        key = base64.urlsafe_b64encode(hashlib.sha256(configured.encode()).digest())
        return Fernet(key)

    key_path = Path(settings.workspace_root) / ".model_key"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if not key_path.exists():
        key_path.write_bytes(Fernet.generate_key())
    return Fernet(key_path.read_bytes().strip())


def encrypt_secret(value: str | None, settings: Settings | None = None) -> str | None:
    if not value:
        return None
    return _fernet(settings).encrypt(value.encode()).decode()


def decrypt_secret(value: str | None, settings: Settings | None = None) -> str | None:
    if not value:
        return None
    return _fernet(settings).decrypt(value.encode()).decode()
