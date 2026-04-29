import pytest

from ocs_deploy.config import OCSConfig

DEFAULT_ENV = {
    "APP_NAME": "ocs",
    "CDK_ACCOUNT": "111111111111",
    "CDK_REGION": "us-east-1",
    "EMAIL_DOMAIN": "chat.example.com",
    "DOMAIN_NAME": "ocs.example.com",
    "DJANGO_ALLOWED_HOSTS": "ocs.example.com",
}


@pytest.fixture
def ocs_config(tmp_path, monkeypatch):
    """Construct an OCSConfig from a temp `.env.test` file."""
    env_file = tmp_path / ".env.test"
    env_file.write_text("\n".join(f"{k}={v}" for k, v in DEFAULT_ENV.items()))
    monkeypatch.chdir(tmp_path)
    return OCSConfig("test")


@pytest.fixture
def ocs_config_factory(tmp_path, monkeypatch):
    """Factory for constructing OCSConfig with overridden env values."""

    def _make(**overrides):
        env = {**DEFAULT_ENV, **overrides}
        env_file = tmp_path / ".env.test"
        env_file.write_text("\n".join(f"{k}={v}" for k, v in env.items()))
        monkeypatch.chdir(tmp_path)
        return OCSConfig("test")

    return _make
