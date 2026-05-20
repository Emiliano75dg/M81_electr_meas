from electrical_measurements.instruments.m81 import M81Controller


class FakeSourceModule:
    def enable(self):
        return None

    def disable(self):
        return None


class FakeMeasureModule:
    pass


class FakeMnemonic:
    RELATIVE_TIME = "RELATIVE_TIME"
    MEASURE_X = "MEASURE_X"
    MEASURE_Y = "MEASURE_Y"
    MEASURE_R = "MEASURE_R"
    MEASURE_THETA = "MEASURE_THETA"
    MEASURE_DC = "MEASURE_DC"


class FakeSSMSystem:
    DataSourceMnemonic = FakeMnemonic

    def __init__(self) -> None:
        self.get_data_calls = []
        self.initiated = False
        self.aborted = False

    def get_source_module(self, idx: int):
        return FakeSourceModule()

    def get_measure_module(self, idx: int):
        return FakeMeasureModule()

    def get_data(self, sample_rate_hz: int, count: int, *data_sources):
        self.get_data_calls.append((sample_rate_hz, count, data_sources))
        return [
            (0.0, 1.0, 2.0, 3.0, 4.0, 5.0),
            (0.1, 1.1, 2.1, 3.1, 4.1, 5.1),
        ]

    def initiate_sweeps(self):
        self.initiated = True

    def abort_sweeps(self):
        self.aborted = True


def test_m81_controller_uses_driver_get_data_for_trace():
    system = FakeSSMSystem()
    controller = M81Controller(system=system)
    controller.configure_trace_stream("M1", points=2, interval_s=0.1)
    controller.start_trace()
    records = controller.fetch_trace()
    controller.abort_sweep()
    assert system.initiated is True
    assert system.aborted is True
    assert len(records) == 2
    assert records[0]["x"] == 1.0
    assert records[1]["theta_deg"] == 4.1
    assert system.get_data_calls[0][0] == 10
