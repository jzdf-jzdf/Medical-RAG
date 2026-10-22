from app.core.config import Settings
from app.schemas.models import ModelConfigRead
from app.services.crypto import decrypt_secret, encrypt_secret


def test_model_api_never_exposes_api_key() -> None:
    assert "api_key" not in ModelConfigRead.model_fields
    assert "api_key_encrypted" not in ModelConfigRead.model_fields


def test_model_api_key_round_trip(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://test:test@localhost/test",
        model_provider="deepseek",
        model_name="deepseek-chat",
        model_base_url="https://api.deepseek.com",
        model_encryption_key="test-encryption-key",
        workspace_root=tmp_path,
    )
    encrypted = encrypt_secret("secret-value", settings)
    assert encrypted != "secret-value"
    assert decrypt_secret(encrypted, settings) == "secret-value"
