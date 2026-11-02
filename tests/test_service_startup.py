from packages_py.config import Settings
from packages_py.services import Service


def test_default_database_matches_documented_homebrew_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql+psycopg://laosun@localhost:5432/postgres"
    assert settings.database_schema == "app"
    assert settings.checkpoint_schema == "checkpoint"


def test_service_can_start_without_retention_env_for_local_cli(monkeypatch):
    class Storage:
        def __init__(self, settings):
            self.settings = settings

    monkeypatch.setattr("packages_py.services.PostgresStorage", Storage)
    settings = Settings(
        service_ttl_seconds=None,
        token_ttl_seconds=None,
        asset_ttl_seconds=None,
        checkpoint_ttl_seconds=None,
        deletion_receipt_ttl_seconds=None,
        backup_ttl_seconds=None,
    )

    service = Service(settings)

    assert service.storage.settings is settings
