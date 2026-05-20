from electrical_measurements.instruments.daq6510_7709 import DAQ6510Controller


class FakeConnection:
    def __init__(self, response: str = "(@101,102)") -> None:
        self.response = response
        self.writes: list[str] = []
        self.queries: list[str] = []

    def write(self, command: str) -> None:
        self.writes.append(command)

    def query(self, command: str) -> str:
        self.queries.append(command)
        return self.response


def test_daq6510_closed_channels_list_parses_numbers():
    controller = DAQ6510Controller(resource_name="TEST", connection=FakeConnection("(@101,102,205)"))
    assert controller.closed_channels_list() == [101, 102, 205]


def test_daq6510_status_snapshot_handles_query_failure():
    class BrokenController(DAQ6510Controller):
        def closed_channels_list(self):
            raise RuntimeError("boom")

    controller = BrokenController(resource_name="TEST", connection=FakeConnection())
    snapshot = controller.status_snapshot()
    assert snapshot["resource_name"] == "TEST"
    assert snapshot["closed_channels"] == []
