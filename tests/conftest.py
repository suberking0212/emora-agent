import pytest


# Isolate storage tests from developer and deployment retention configuration.
@pytest.fixture(autouse=True)
def explicit_test_retention_policy(monkeypatch):
    values = {
        "SERVICE_TTL_SECONDS": "86400",
        "TOKEN_TTL_SECONDS": "3600",
        "ASSET_TTL_SECONDS": "3600",
        "CHECKPOINT_TTL_SECONDS": "3600",
        "DELETION_RECEIPT_TTL_SECONDS": "3600",
        "BACKUP_TTL_SECONDS": "3600",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
