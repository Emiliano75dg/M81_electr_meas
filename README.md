# electrical_measurements

A Python framework for automating complex electrical measurements on semiconductor samples. It manages temperature and magnetic field control, contact switching via relay matrices (e.g., Keithley/Tektronix DAQ6510 + 7709), and data acquisition from instruments like the Lake Shore M81-SSM.

For a much more detailed operational manual, see [USER_GUIDE.md](/home/emiliano/Documents/Automazione/M81_electr_meas/USER_GUIDE.md).

## Features

- **Flexible Geometries**: Define your sample's pinout (Van der Pauw, Hall bar, custom) in a YAML file, with no hard-coding.
- Software interlocks to protect sources and relays.
- Mock/dry-run mode for offline development and testing.
- Protocols for magnetoresistance, Hall, Van der Pauw, second harmonic, and reciprocity.
- Data saving in CSV, Parquet, and JSON metadata formats.

## Installation

This project requires Python 3.11 or higher.

```bash
# 1. Clone the repository
git clone <YOUR_REPOSITORY_URL>
cd <YOUR_REPOSITORY_DIR>

# 2. Install the package
pip install -e .

# 3. Install optional extras
pip install -e .[test]
pip install -e .[hardware]

# 4. Optional runtime backends
pip install lakeshore
pip install pyvisa-py # or another pyvisa backend
```

Required Lake Shore references:

- Official driver: https://github.com/lakeshorecryotronics/python-driver
- Documentation: https://lake-shore-python-driver.readthedocs.io/en/latest/
- Driver installation: `pip install lakeshore`

The wrapper uses `from lakeshore import SSMSystem` as the primary interface for the M81.

## Instrument Configuration

Example [configs/instruments.yaml](/home/emiliano/Documents/Automazione/M81_electr_meas/configs/instruments.yaml):

```yaml
sample_id: demo-sample
instruments:
  m81:
    enabled: true
    connection:
      kind: mock
    measure_modes:
      M1: lockin
      M2: dc
      M3: auto
    measure_harmonics:
      M1: 1
      M2: 2
      M3: 3
    measure_nplc:
      M1: 1.0
      M2: 2.0
      M3: 1.0
    measure_time_constants_s:
      M1: 0.3
      M2: 1.0
      M3: 0.3
    measure_rolloffs:
      M1: R24
      M2: R12
      M3: R24
  daq6510:
    enabled: true
    resource: MOCK::DAQ6510
  environment:
    kind: mock
    mode: integrated
    endpoint: http://localhost:8000
output:
  directory: data
```

`measure_modes` is optional and defines the preferred acquisition mode for each M81 measurement channel:

- `lockin`: reads `x/y/r/theta`
- `dc`: reads `value`
- `auto`: uses the channel configuration if already set, otherwise falls back automatically

`measure_harmonics` is optional and selects which harmonic is read in `lockin` mode:

- `1`: first harmonic
- `2`, `3`, ...: higher harmonics

`measure_nplc` is optional and defines the DC integration time:

- positive values such as `0.1`, `1.0`, `5.0`

`measure_time_constants_s` is optional and defines the lock-in time constant:

- positive values in seconds such as `0.1`, `0.3`, `1.0`

`measure_rolloffs` is optional and defines the per-channel lock-in rolloff:

- `R6`, `R12`, `R18`, `R24`

The GUI `Live` tab can override `mode`, `harmonic`, `NPLC`, `time constant`, and `rolloff` interactively for `M1/M2/M3`, while automated protocols use the YAML preferences when available.

For a real HTTP environment backend:

```yaml
instruments:
  environment:
    kind: teslatron
    allow_control: false
    endpoint: http://127.0.0.1:8000
    state_path: /state
    set_temperature_path: /temperature/set
    set_field_path: /field/set
    start_field_ramp_path: /field/ramp/start
    stop_field_ramp_path: /field/ramp/stop
    start_temperature_ramp_path: /temperature/ramp/start
    stop_temperature_ramp_path: /temperature/ramp/stop
    poll_interval_s: 0.5
    timeout_s: 5.0
```

`allow_control: false` is the recommended laboratory mode. For `kind: teslatron`, this makes the effective default environment mode `async-poll`, so the software only performs `GET /state` readback and never sends setpoint or ramp commands. To let this project drive Teslatron explicitly, set both:

- `allow_control: true`
- `mode: integrated`

Supported environment modes:

- `integrated`: the app both reads and controls temperature/field
- `async-poll`: the app only polls environment state and does not send setpoints or ramp commands
- `standalone`: the app also works without an environment backend; `T` and `B` remain local to the session

## Contact map YAML

The package works in terms of logical sample contacts and maps them onto the physical 7709 relay channels.

Use contact maps to answer:

- how the sample contacts are wired
- which M81 channels are connected
- which named relay-matrix states exist
- which states are reciprocal to each other

Use measurement sequences to answer:

- which named states should be measured
- in which order
- with which M81 source/measurement settings

This separation means you do not need to duplicate a contact map just to try a different electrical measurement order.

Sequence YAML is the preferred measurement path for new work. Legacy protocol commands remain available as quick-start compatibility paths.

Van der Pauw example: [configs/contact_maps/vdp_4contacts_7709.yaml](/home/emiliano/Documents/Automazione/M81_electr_meas/configs/contact_maps/vdp_4contacts_7709.yaml)

Hall bar example: [configs/contact_maps/hallbar_6contacts_7709.yaml](/home/emiliano/Documents/Automazione/M81_electr_meas/configs/contact_maps/hallbar_6contacts_7709.yaml)

Sequence examples:

- [configs/sequences/vdp_ac_reciprocity.yaml](/home/emiliano/Documents/Automazione/M81_electr_meas/configs/sequences/vdp_ac_reciprocity.yaml)
- [configs/sequences/vdp_dc_reverse_bias.yaml](/home/emiliano/Documents/Automazione/M81_electr_meas/configs/sequences/vdp_dc_reverse_bias.yaml)
- [configs/sequences/hallbar_ac_rxx_rxy.yaml](/home/emiliano/Documents/Automazione/M81_electr_meas/configs/sequences/hallbar_ac_rxx_rxy.yaml)
- [configs/sequences/second_harmonic_ac.yaml](/home/emiliano/Documents/Automazione/M81_electr_meas/configs/sequences/second_harmonic_ac.yaml)
- [configs/sequences/example_hallbar_dc_sequence.yaml](/home/emiliano/Documents/Automazione/M81_electr_meas/configs/sequences/example_hallbar_dc_sequence.yaml)
- [configs/sequences/example_second_harmonic_ac_sequence.yaml](/home/emiliano/Documents/Automazione/M81_electr_meas/configs/sequences/example_second_harmonic_ac_sequence.yaml)

Safety note:

- all relay switching still goes through `Matrix7709.apply_state()`
- sequence runs never bypass the matrix safety interlock
- `Matrix7709.apply_state()` always disables sources before switching and never re-enables arbitrary M81 sources
- dry-run preview prints the resolved plan without touching hardware
- the dry-run table includes resolved relays, excitation parameters, outputs, repeats, tags, and reciprocity links
- legacy protocol classes remain compatibility helpers while sequence YAML is the preferred path

## Mock mode

```bash
python examples/run_vdp_hall.py --mock
electrical-measure run --mock --config configs/instruments.yaml --protocol hall --contact-map configs/contact_maps/hallbar_6contacts_7709.yaml
electrical-measure run --mock --config configs/instruments.yaml --protocol vdp_hall --contact-map configs/contact_maps/vdp_4contacts_7709.yaml --include-reciprocity
electrical-measure run --mock --config configs/instruments.yaml --protocol vdp --contact-map configs/contact_maps/vdp_4contacts_7709.yaml --include-anisotropy
```

`vdp_hall` runs a Hall measurement using Van der Pauw states. With `--include-reciprocity`, the sequence also includes reciprocal states and stores reciprocity errors in the derived fields.

In the `vdp` protocol, `--include-anisotropy` adds an anisotropy check between the two orthogonal Van der Pauw families and saves family averages, absolute/relative difference, and ratio in `derived`.

Sequence-mode examples:

```bash
electrical-measure run \
  --config configs/instruments.yaml \
  --contact-map configs/contact_maps/vdp_4contacts_7709.yaml \
  --sequence configs/sequences/vdp_ac_reciprocity.yaml \
  --temperatures 300 \
  --fields -1,0,1 \
  --environment-mode async-poll
```

```bash
electrical-measure run \
  --config configs/instruments.yaml \
  --contact-map configs/contact_maps/vdp_4contacts_7709.yaml \
  --sequence configs/sequences/vdp_ac_reciprocity.yaml \
  --dry-run
```

Sequence concepts:

- Sequence files now use an explicit `MeasurementSequence` model with `source_mode`, `source_quantity`, `source_value`, `measure_mode`, `readout`, `harmonic`, `frequency_hz`, and `reverse_policy`
- DC reverse bias is DC-only and is resolved by reversing current polarity on the same matrix state with `reverse_policy: auto` or `reverse_policy: dc_source_inversion`
- AC lock-in sequences use `source_mode: ac`, `frequency_hz`, and `harmonic`; `reverse_policy: auto` intentionally resolves to a single measurement
- ordinary AC Van der Pauw, Hall, magnetoresistance, and second-harmonic sequences normally do not need reverse-bias steps
- `reverse_current` contact-map relations are no longer used as automatic reverse bias; keep them only for explicit diagnostic states
- reciprocity is a different matrix state with exchanged current and voltage contacts
- magnetic-field reversal belongs to the outer field loop, not the matrix sequence
- temperature ramps belong to the outer environment loop, not the matrix sequence
- for Teslatron or LabVIEW driven ramps, prefer `stream-observe`; the environment stays read-only unless `allow_control: true` and `mode: integrated` are both enabled

Other useful subcommands:

- `electrical-measure list-instruments`
- `electrical-measure check-contacts --mock`
- `electrical-measure emergency-stop --mock`

## GUI

The package also includes a lightweight desktop GUI based on `tkinter`:

```bash
electrical-measure gui --mock
```

From the GUI you can:

- choose instrument configuration files and a contact map
- select the protocol and either `stable`, `stream-observe`, or `stream-ramp` mode
- choose the environment mode `integrated`, `async-poll`, or `standalone`
- select measurement states dynamically from the contact map
- inspect relay details, reciprocal states, and instrument-to-contact bindings
- set currents, frequency, harmonic, fields, and temperatures
- launch mock or real measurements through the same CLI runner
- use `Emergency Stop` from the window
- inspect logs, execution state, and previews of generated CSV files in dedicated tabs
- connect live instruments from the GUI and monitor `T`, `B`, active sources, and closed relays
- view the effective environment mode badge in the `Live` tab and whether the backend is read-only/local
- use the `Environment Controls` panel in the `Live` tab for `Set T/B` and `Start/Stop Ramp` only when the mode is `integrated` and control is explicitly enabled
- configure `S1`/`S2`/`S3` manually in current or voltage mode, in DC or AC lock-in, with setpoint, frequency, and harmonic
- use guided mini-sequences such as `Safe Switch Then Enable`, `Safe Switch Then Read`, and `Disable + Open All`
- apply a matrix state manually, open all relays, and quickly read `M1` or `M2`
- start continuous live acquisition with a circular buffer and a real-time plot directly from the `Live` tab
- with a Hall bar equipped with `vxx_meter` and `vxy_meter`, acquire both live channels and overlay them in the plot with `x_dual` or `r_dual`
- draw quick plots directly in the GUI by choosing `X/Y` columns from a CSV
- use the dedicated `Sequence` tab to load/save YAML or JSON sequences, inspect resolved relays and bias policy, validate, dry-run, reorder steps, and launch the same shared sequence runner used by the CLI

## Streaming During Ramps

Recommended read-only mode for externally controlled Teslatron or LabVIEW ramps:

```bash
electrical-measure run \
  --config configs/instruments.yaml \
  --protocol hallbar_mr \
  --contact-map configs/contact_maps/hallbar_6contacts_7709.yaml \
  --mode stream-observe \
  --fields 0 \
  --temperatures 300 \
  --stream-samples 100 \
  --stream-interval 0.1
```

`stream-observe` continuously acquires M81 data and polls environment state, but it never starts or stops a field or temperature ramp. Sequence streaming currently supports AC/lock-in steps only.

To acquire synchronized data during a software-controlled field or temperature ramp:

```bash
electrical-measure run \
  --mock \
  --environment-mode integrated \
  --config configs/instruments.yaml \
  --protocol hallbar_mr \
  --contact-map configs/contact_maps/hallbar_6contacts_7709.yaml \
  --mode stream-ramp \
  --fields 0 \
  --temperatures 300 \
  --ramp-quantity field \
  --ramp-target 0.5 \
  --ramp-rate 60 \
  --stream-samples 100 \
  --stream-interval 0.1
```

`stream-ramp` is preserved for explicit control mode only. When using a Teslatron backend, enable it with `allow_control: true`. With `allow_control: false`, Teslatron stays read-only and this software will not send setpoint or ramp commands. The runner now checks control capability before any relay closure, trace setup, source enable, or acquisition start.

The resulting dataset stores `trace_index`, `trace_channel`, `field_t`, `temperature_k`, `environment_timestamp`, and per-step sequence metadata such as `sequence_id`, `step_index`, `state_name`, `source_mode`, `source_value`, `source_polarity`, `timestamp_matrix_applied`, `timestamp_measure_start`, and `timestamp_measure_end`.

## Notifications

Optional completion/failure notifications can be sent through a webhook:

```yaml
notifications:
  kind: http-webhook
  endpoint: http://127.0.0.1:9000/measurements
  timeout_s: 5.0
  headers:
    Authorization: Bearer secret-token
```

On success the runner sends `measurement_completed` with protocol, `sample_id`, `output_dir`, row count, timestamps, last temperature, and last field. On failure it sends `measurement_failed`.

When the official Lake Shore driver exposes `SSMSystem.get_data()`, the wrapper uses it as the first choice for streaming data. The SCPI fallback remains isolated in [m81_scpi_fallback.py](/home/emiliano/Documents/Automazione/M81_electr_meas/src/electrical_measurements/instruments/m81_scpi_fallback.py).

## Safety

- never switch relays with sources enabled, unless explicitly overridden
- before switching relays, the software disables the M81 sources
- every state change first opens all relays and then closes only validated channels
- on error, `SafeMeasurementSession` executes `emergency_stop()`

## Reciprocity

Reciprocity is treated as a first-class concept:

- at `B = 0`: direct comparison `R_ij,kl` vs `R_kl,ij`
- at `B != 0`: comparison `R_ij,kl(+B)` vs `R_kl,ij(-B)`

Saved results include the state, reciprocal state, field-pairing type, and absolute/relative errors.
