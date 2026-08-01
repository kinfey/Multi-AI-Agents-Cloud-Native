from media_claw_agent import config


def test_federated_token_uses_workload_identity(monkeypatch) -> None:
    settings = object.__new__(config.Settings)
    object.__setattr__(settings, "azure_client_id", "client-id")
    captured = {}
    transport = object()

    monkeypatch.setenv("AZURE_FEDERATED_TOKEN_FILE", "/var/run/secrets/azure/tokens/token")
    monkeypatch.setenv("AZURE_TENANT_ID", "tenant-id")
    monkeypatch.setattr(config, "RequestsTransport", lambda **kwargs: transport)
    monkeypatch.setattr(
        config,
        "WorkloadIdentityCredential",
        lambda **kwargs: captured.update(kwargs) or "workload-credential",
    )

    assert settings.credential() == "workload-credential"
    assert captured == {
        "tenant_id": "tenant-id",
        "client_id": "client-id",
        "token_file_path": "/var/run/secrets/azure/tokens/token",
        "transport": transport,
    }


def test_client_id_without_federated_token_uses_managed_identity(monkeypatch) -> None:
    settings = object.__new__(config.Settings)
    object.__setattr__(settings, "azure_client_id", "client-id")
    monkeypatch.delenv("AZURE_FEDERATED_TOKEN_FILE", raising=False)
    monkeypatch.setattr(
        config,
        "ManagedIdentityCredential",
        lambda **kwargs: ("managed-credential", kwargs),
    )

    assert settings.credential() == (
        "managed-credential",
        {"client_id": "client-id"},
    )