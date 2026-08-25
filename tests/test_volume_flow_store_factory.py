import pytest

import volume_flow_store


def test_store_factory_retries_after_schema_initialization_failure(monkeypatch):
    attempts = []

    class FlakyPostgresStore:
        def init_schema(self):
            attempts.append("init")
            if len(attempts) == 1:
                raise volume_flow_store.VolumeFlowStoreUnavailable(
                    "managed database is still starting"
                )

    monkeypatch.setenv(
        "VOLUME_FLOW_DATABASE_URL",
        "postgresql://render-internal/locphats_rrg",
    )
    monkeypatch.setattr(volume_flow_store, "PostgresVolumeFlowStore", FlakyPostgresStore)
    monkeypatch.setattr(volume_flow_store, "_STORE", None)

    with pytest.raises(
        volume_flow_store.VolumeFlowStoreUnavailable,
        match="still starting",
    ):
        volume_flow_store.get_volume_flow_store(required=True)

    assert volume_flow_store._STORE is None

    recovered = volume_flow_store.get_volume_flow_store(required=True)
    assert isinstance(recovered, FlakyPostgresStore)
    assert volume_flow_store.get_volume_flow_store(required=True) is recovered
    assert attempts == ["init", "init"]
