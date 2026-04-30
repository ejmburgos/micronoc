from app.repositories.monitor_snapshot_repository import MonitorSnapshotRepository


class FakeSession:
    def __init__(self) -> None:
        self.added = []
        self.flush_calls = 0
        self.refresh_calls = 0

    def add(self, snapshot) -> None:
        self.added.append(snapshot)

    def flush(self) -> None:
        self.flush_calls += 1

    def refresh(self, _snapshot) -> None:
        self.refresh_calls += 1


def test_save_snapshot_does_not_force_flush_or_refresh() -> None:
    session = FakeSession()
    repository = MonitorSnapshotRepository(session=session)

    snapshot = repository.save_snapshot(
        source="mikrotik",
        metric_name="mikrotik_cpu",
        metric_value=42,
        meta_json={"router_name": "r1"},
    )

    assert snapshot in session.added
    assert session.flush_calls == 0
    assert session.refresh_calls == 0
