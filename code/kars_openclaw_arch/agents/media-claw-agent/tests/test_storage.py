from unittest.mock import Mock

from media_claw_agent import storage


def test_blob_client_uses_bounded_network_retries(monkeypatch) -> None:
    service = Mock()
    client = Mock(return_value=service)
    monkeypatch.setattr(storage, "BlobServiceClient", client)

    storage.MediaStorage(
        account_url="https://example.blob.core.windows.net",
        container_name="media",
        credential=Mock(),
    )

    client.assert_called_once_with(
        account_url="https://example.blob.core.windows.net",
        credential=client.call_args.kwargs["credential"],
        max_single_put_size=storage.MediaStorage.MAX_SINGLE_PUT_SIZE,
        max_block_size=storage.MediaStorage.MAX_BLOCK_SIZE,
        connection_timeout=10,
        read_timeout=30,
        retry_total=2,
    )
    service.get_container_client.assert_called_once_with("media")