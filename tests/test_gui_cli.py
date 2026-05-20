from electrical_measurements.runners.run_measurement import build_parser, build_run_namespace


def test_build_run_namespace_sets_expected_defaults():
    args = build_run_namespace(protocol="hall", mock=True, temperatures="300", fields="0,1", environment_mode="async-poll")
    assert args.command == "run"
    assert args.protocol == "hall"
    assert args.mock is True
    assert args.temperatures == "300"
    assert args.fields == "0,1"
    assert args.environment_mode == "async-poll"


def test_parser_supports_gui_command():
    parser = build_parser()
    args = parser.parse_args(["gui", "--mock", "--environment-mode", "standalone"])
    assert args.command == "gui"
    assert args.mock is True
    assert args.environment_mode == "standalone"
    assert callable(args.func)
