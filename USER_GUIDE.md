# User Guide

This guide is a practical manual for using `electrical_measurements` from first installation to routine measurement runs. It is intentionally more detailed than the README and is meant to be the main onboarding document for daily work.

---

## 1. What The Software Does

`electrical_measurements` automates electrical characterization workflows where you need to coordinate:

- a source/measurement instrument such as the Lake Shore M81-SSM
- a relay matrix such as a DAQ6510 + 7709
- an optional environment controller for temperature and magnetic field
- one or more measurement protocols such as Hall, magnetoresistance, Van der Pauw, reciprocity, or contact checks

The software supports:

- command-line execution
- a desktop GUI
- mock/offline development
- real hardware workflows
- synchronized acquisition during ramps
- YAML-driven sample/contact definitions

---

## 2. Core Concepts

Before using the software, it helps to understand five concepts.

### 2.1 Instrument Configuration

The instrument configuration file defines:

- which instruments are enabled
- whether the M81 is real or mock
- how the relay matrix is addressed
- how the environment controller is connected
- where output files are written
- optional preferred measurement modes for each M81 channel

The default example is [configs/instruments.yaml](/home/emiliano/Documents/Automazione/M81_electr_meas/configs/instruments.yaml).

### 2.2 Contact Map

A contact map defines how logical sample contacts map onto relay channels and measurement states.

Examples:

- Hall bar: [configs/contact_maps/hallbar_6contacts_7709.yaml](/home/emiliano/Documents/Automazione/M81_electr_meas/configs/contact_maps/hallbar_6contacts_7709.yaml)
- Van der Pauw: [configs/contact_maps/vdp_4contacts_7709.yaml](/home/emiliano/Documents/Automazione/M81_electr_meas/configs/contact_maps/vdp_4contacts_7709.yaml)
- Second harmonic: [configs/contact_maps/second_harmonic_7709.yaml](/home/emiliano/Documents/Automazione/M81_electr_meas/configs/contact_maps/second_harmonic_7709.yaml)

### 2.3 Protocol

A protocol is the measurement logic to run on top of the configured instruments and selected contact-map states.

Supported protocols:

- `hall`
- `hallbar_mr`
- `vdp`
- `vdp_hall`
- `second_harmonic`
- `reciprocity`
- `check_contacts`

### 2.4 Environment Mode

The software supports three environment modes:

- `integrated`: reads and controls temperature/field
- `async-poll`: reads temperature/field but does not control them
- `standalone`: keeps temperature/field as local software context only

### 2.5 Run Mode

Two run modes exist:

- `stable`: move to a target temperature/field and then take measurements
- `stream-ramp`: stream data while a field or temperature ramp is in progress

---

## 3. Installation And First Check

Install the package in editable mode:

```bash
pip install -e .
```

Useful extras:

```bash
pip install -e .[test]
pip install -e .[hardware]
```

Optional runtime packages:

```bash
pip install lakeshore
pip install pyvisa-py
```

After installation, verify that the CLI is available:

```bash
electrical-measure --help
```

List configured instruments:

```bash
electrical-measure list-instruments --config configs/instruments.yaml --mock
```

---

## 4. Repository Layout

The parts most users will interact with are:

- [README.md](/home/emiliano/Documents/Automazione/M81_electr_meas/README.md)
- [USER_GUIDE.md](/home/emiliano/Documents/Automazione/M81_electr_meas/USER_GUIDE.md)
- [configs/instruments.yaml](/home/emiliano/Documents/Automazione/M81_electr_meas/configs/instruments.yaml)
- [configs/contact_maps/hallbar_6contacts_7709.yaml](/home/emiliano/Documents/Automazione/M81_electr_meas/configs/contact_maps/hallbar_6contacts_7709.yaml)
- [configs/contact_maps/vdp_4contacts_7709.yaml](/home/emiliano/Documents/Automazione/M81_electr_meas/configs/contact_maps/vdp_4contacts_7709.yaml)
- [src/electrical_measurements/runners/run_measurement.py](/home/emiliano/Documents/Automazione/M81_electr_meas/src/electrical_measurements/runners/run_measurement.py)
- [src/electrical_measurements/gui/app.py](/home/emiliano/Documents/Automazione/M81_electr_meas/src/electrical_measurements/gui/app.py)

---

## 5. Understanding `configs/instruments.yaml`

The configuration file is the starting point for almost every run.

Minimal mock-oriented example:

```yaml
sample_id: demo-sample
instruments:
  m81:
    enabled: true
    connection:
      kind: mock
  daq6510:
    enabled: true
    resource: MOCK::DAQ6510
  environment:
    kind: mock
    mode: integrated
output:
  directory: data
logging:
  level: INFO
```

### 5.1 M81 Section

The `m81` section controls:

- connection type
- optional preferred measure modes
- optional harmonics
- optional DC `nplc`
- optional lock-in time constants
- optional lock-in rolloffs

Example:

```yaml
instruments:
  m81:
    connection:
      kind: mock
    measure_modes:
      M1: lockin
      M2: dc
      M3: auto
    measure_harmonics:
      M1: 1
      M2: 2
    measure_nplc:
      M2: 2.0
    measure_time_constants_s:
      M1: 0.3
    measure_rolloffs:
      M1: R24
```

### 5.2 DAQ6510 Section

The `daq6510` section mainly controls:

- real vs mock resource
- relay settle time

Example:

```yaml
instruments:
  daq6510:
    resource: MOCK::DAQ6510
    settle_s: 0.05
```

### 5.3 Environment Section

Mock example:

```yaml
instruments:
  environment:
    kind: mock
    mode: integrated
```

HTTP backend example:

```yaml
instruments:
  environment:
    kind: teslatron
    mode: integrated
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

---

## 6. Understanding Contact Maps

Contact maps define named states such as:

- current injection pair
- voltage pickup pair
- reciprocal state
- reverse-current state
- logical grouping

The software uses these definitions to compute actual relay closures.

Typical workflow:

1. Choose the sample geometry.
2. Open the corresponding YAML map.
3. Verify state names and reciprocal relationships.
4. Use those states either automatically through a protocol or manually through the GUI.

---

## 7. CLI Overview

The main CLI entry point is:

```bash
electrical-measure
```

Key subcommands:

- `run`
- `gui`
- `list-instruments`
- `check-contacts`
- `analyze`
- `emergency-stop`

Show help:

```bash
electrical-measure --help
electrical-measure run --help
```

---

## 8. First Mock Run

The safest way to learn the software is with mock hardware.

Example:

```bash
electrical-measure run \
  --mock \
  --config configs/instruments.yaml \
  --contact-map configs/contact_maps/hallbar_6contacts_7709.yaml \
  --protocol hallbar_mr \
  --temperatures 300 \
  --fields 0,0.1,-0.1
```

What this does:

- loads the default instrument configuration
- loads the Hall bar contact map
- builds mock M81, matrix, and environment controllers
- runs a stable magnetoresistance sweep
- saves CSV, Parquet, and metadata output

---

## 9. Output Files

A typical run writes files into the configured output directory:

- `<protocol>.csv`
- `<protocol>.parquet`
- `<protocol>.metadata.json`

For `stream-ramp`, the stem becomes `<protocol>_stream`.

### 9.1 CSV

The CSV is the easiest file to inspect manually or import into analysis notebooks.

### 9.2 Parquet

Parquet is more convenient for larger datasets and programmatic workflows. If Parquet support is unavailable in the environment, the software writes a placeholder file indicating that export was not available.

### 9.3 Metadata JSON

The metadata file contains:

- sample ID
- geometry
- contact-map path
- contact mapping
- relay mapping
- instrument config snapshot
- git commit if available
- timestamps

---

## 10. Stable Measurement Runs

Stable mode is the default. The workflow is:

1. position environment to the requested `T` and `B`
2. configure the selected protocol
3. switch relay state(s)
4. enable source
5. acquire readings
6. save output files

### 10.1 Hall Bar Magnetoresistance

```bash
electrical-measure run \
  --mock \
  --config configs/instruments.yaml \
  --contact-map configs/contact_maps/hallbar_6contacts_7709.yaml \
  --protocol hallbar_mr \
  --temperatures 300 \
  --fields -1,-0.5,0,0.5,1
```

### 10.2 Hall

```bash
electrical-measure run \
  --mock \
  --config configs/instruments.yaml \
  --contact-map configs/contact_maps/hallbar_6contacts_7709.yaml \
  --protocol hall \
  --temperatures 300 \
  --fields -1,1
```

### 10.3 Van der Pauw

```bash
electrical-measure run \
  --mock \
  --config configs/instruments.yaml \
  --contact-map configs/contact_maps/vdp_4contacts_7709.yaml \
  --protocol vdp \
  --temperatures 300 \
  --fields 0
```

Optional anisotropy check:

```bash
electrical-measure run \
  --mock \
  --config configs/instruments.yaml \
  --contact-map configs/contact_maps/vdp_4contacts_7709.yaml \
  --protocol vdp \
  --temperatures 300 \
  --fields 0 \
  --include-anisotropy
```

### 10.4 Van der Pauw Hall

```bash
electrical-measure run \
  --mock \
  --config configs/instruments.yaml \
  --contact-map configs/contact_maps/vdp_4contacts_7709.yaml \
  --protocol vdp_hall \
  --temperatures 300 \
  --fields 0.1
```

Optional reciprocity expansion:

```bash
electrical-measure run \
  --mock \
  --config configs/instruments.yaml \
  --contact-map configs/contact_maps/vdp_4contacts_7709.yaml \
  --protocol vdp_hall \
  --temperatures 300 \
  --fields 0.1 \
  --include-reciprocity
```

### 10.5 Reciprocity

```bash
electrical-measure run \
  --mock \
  --config configs/instruments.yaml \
  --contact-map configs/contact_maps/vdp_4contacts_7709.yaml \
  --protocol reciprocity \
  --temperatures 300 \
  --fields 0
```

### 10.6 Contact Check

```bash
electrical-measure check-contacts \
  --mock \
  --config configs/instruments.yaml \
  --contact-map configs/contact_maps/hallbar_6contacts_7709.yaml
```

Equivalent explicit form:

```bash
electrical-measure run \
  --mock \
  --config configs/instruments.yaml \
  --contact-map configs/contact_maps/hallbar_6contacts_7709.yaml \
  --protocol check_contacts
```

---

## 11. Streaming During Ramps

Streaming mode is for acquiring continuously while the environment is moving.

Requirements:

- `--mode stream-ramp`
- `--ramp-target`
- `--ramp-rate`
- environment mode must support control, typically `integrated`
- protocol must be single-state for current streaming implementation

Example:

```bash
electrical-measure run \
  --mock \
  --config configs/instruments.yaml \
  --contact-map configs/contact_maps/hallbar_6contacts_7709.yaml \
  --protocol hallbar_mr \
  --mode stream-ramp \
  --temperatures 300 \
  --fields 0 \
  --ramp-quantity field \
  --ramp-target 0.5 \
  --ramp-rate 60 \
  --stream-samples 100 \
  --stream-interval 0.1
```

Notes:

- in `async-poll`, stream-ramp is intentionally rejected because the backend is read-only
- the resulting stream file includes synchronized environment values

---

## 12. Useful CLI Options

Common run options:

- `--config`
- `--contact-map`
- `--mock`
- `--environment-mode`
- `--sample-id`
- `--output`
- `--protocol`
- `--temperatures`
- `--fields`
- `--current`
- `--frequency`
- `--harmonic`
- `--settle`
- `--dry-run`

Multi-state / protocol-specific options:

- `--state-name`
- `--selected-states`
- `--include-reciprocity`
- `--include-anisotropy`

Streaming options:

- `--mode stream-ramp`
- `--ramp-quantity`
- `--ramp-target`
- `--ramp-rate`
- `--stream-samples`
- `--stream-interval`

---

## 13. Choosing The Right Environment Mode

### Use `integrated` when:

- the software should send setpoints
- the software should start or stop ramps
- you want stream-ramp mode

### Use `async-poll` when:

- the environment is controlled externally
- you still want live readback in GUI or CLI runs
- you do not want the software to modify `T` or `B`

### Use `standalone` when:

- you want fully offline or local-only runs
- you want to treat `T` and `B` as contextual labels rather than controlled hardware

---

## 14. GUI Guide

Launch the GUI:

```bash
electrical-measure gui --mock
```

The GUI is organized into tabs:

- `Setup`
- `States`
- `Live`
- `Log`
- `Preview`

### 14.1 Setup Tab

Use this tab to define:

- config file
- contact map
- output directory
- sample ID
- protocol
- mode
- environment mode
- target temperatures and fields
- current, frequency, harmonic
- settle time
- ramp settings
- stream settings
- mock / dry-run flags
- reciprocity / anisotropy options when applicable

Main actions:

- `Run Measurement`
- `Emergency Stop`
- `Reload Contact Map`
- `Refresh Preview`

### 14.2 States Tab

Use this tab to:

- inspect available states
- select the active state for single-state protocols
- select multiple states for multi-state protocols
- inspect reciprocal relationships
- inspect logical bindings and relay channels

Helpful buttons:

- `Select Recommended`
- `Select All`
- `Clear`

### 14.3 Live Tab

The Live tab is split into several sections.

#### Live Summary

Shows:

- temperature
- field
- enabled sources
- closed relays
- resolved measure configuration
- environment mode
- acquisition status
- last reading

#### Environment Controls

Available only when:

- live instruments are connected
- the environment mode supports control

Actions:

- `Set T`
- `Set B`
- `Start B Ramp`
- `Stop B Ramp`
- `Start T Ramp`
- `Stop T Ramp`

#### Manual Controls

Use this section to:

- configure sources manually
- apply preferred M81 measure modes
- enable or disable sources
- apply a matrix state
- open all relays
- read `M1` or `M2`
- run guided safe actions

Useful guided actions:

- `Safe Switch Then Enable`
- `Safe Switch Then Read`
- `Disable + Open All`

#### Continuous Live Acquisition

Use this section to:

- pick a measurement channel
- set acquisition interval
- define live buffer size
- choose `X` and `Y` plot columns
- start and stop live acquisition

Buttons:

- `Start Live`
- `Stop Live`
- `Refresh Plot`

### 14.4 Log Tab

Shows:

- command output
- runtime logs
- errors and tracebacks

### 14.5 Preview Tab

Use this tab to:

- open the latest CSV output
- inspect a text preview
- choose `X`/`Y` columns
- draw a quick plot without leaving the application

---

## 15. Recommended Beginner Workflow

If you are new to the software, use this sequence.

1. Start with `--mock`.
2. Open the GUI and load:
   - [configs/instruments.yaml](/home/emiliano/Documents/Automazione/M81_electr_meas/configs/instruments.yaml)
   - [configs/contact_maps/hallbar_6contacts_7709.yaml](/home/emiliano/Documents/Automazione/M81_electr_meas/configs/contact_maps/hallbar_6contacts_7709.yaml)
3. Run `hallbar_mr` in stable mode at one temperature and a few fields.
4. Inspect the generated CSV in the Preview tab.
5. Try `vdp` and `vdp_hall` with the Van der Pauw map.
6. Only after that, move to real hardware configuration.

---

## 16. Recommended Real-Hardware Workflow

1. Verify that `configs/instruments.yaml` points to real resources.
2. Confirm environment mode:
   - `integrated` if this software controls the environment
   - `async-poll` if another system controls it
3. Run:

```bash
electrical-measure list-instruments --config configs/instruments.yaml
```

4. Perform a contact check first.
5. Start with a small Hall or MR run at one temperature.
6. Confirm outputs and wiring before scaling up to longer sweeps or ramps.
7. Keep `emergency-stop` available in another terminal if desired.

---

## 17. Safety Guidance

The software already enforces safety-oriented behavior, but you should still follow a cautious workflow.

Recommended practices:

- start with mock mode after editing config or contact-map files
- check the selected state carefully before applying it
- do a contact check before longer runs on a new sample
- use conservative current amplitudes initially
- keep relay switching separate from enabled sources
- verify environment mode before attempting ramps

Emergency stop:

```bash
electrical-measure emergency-stop --config configs/instruments.yaml --contact-map configs/contact_maps/hallbar_6contacts_7709.yaml
```

---

## 18. Typical Troubleshooting

### 18.1 "Connect live instruments first"

Cause:

- you used a Live-tab action before connecting instruments

Fix:

- go to `Live`
- click `Connect`
- retry the action

### 18.2 "Unknown --state-name"

Cause:

- the requested state name is not present in the chosen contact map

Fix:

- inspect the contact map YAML
- use a valid state name
- or switch to the correct contact map

### 18.3 Stream-ramp rejected in `async-poll`

Cause:

- the environment backend is read-only in `async-poll`

Fix:

- switch to `integrated` if the software should control ramps
- or run in `stable` mode instead

### 18.4 Contact map load failure

Cause:

- malformed YAML
- missing required keys such as `name`, `contacts`, or `states`

Fix:

- validate the YAML structure
- compare with the example files in `configs/contact_maps`

### 18.5 Live plot shows no data

Cause:

- no acquisition running
- selected columns are not numeric
- chosen measure channel is not producing values yet

Fix:

- start live acquisition
- choose simpler `X/Y` pairs such as `elapsed_s` and `x`
- confirm the M81 measurement mode

---

## 19. Example Command Cookbook

### Small Hall run

```bash
electrical-measure run \
  --mock \
  --config configs/instruments.yaml \
  --contact-map configs/contact_maps/hallbar_6contacts_7709.yaml \
  --protocol hall \
  --temperatures 300 \
  --fields -0.5,0.5
```

### Small MR run

```bash
electrical-measure run \
  --mock \
  --config configs/instruments.yaml \
  --contact-map configs/contact_maps/hallbar_6contacts_7709.yaml \
  --protocol hallbar_mr \
  --temperatures 300 \
  --fields -1,0,1
```

### Contact check

```bash
electrical-measure check-contacts \
  --mock \
  --config configs/instruments.yaml \
  --contact-map configs/contact_maps/hallbar_6contacts_7709.yaml
```

### Standalone mode run

```bash
electrical-measure run \
  --mock \
  --environment-mode standalone \
  --config configs/instruments.yaml \
  --contact-map configs/contact_maps/hallbar_6contacts_7709.yaml \
  --protocol hallbar_mr \
  --temperatures 300 \
  --fields 0
```

### Integrated stream-ramp

```bash
electrical-measure run \
  --mock \
  --environment-mode integrated \
  --config configs/instruments.yaml \
  --contact-map configs/contact_maps/hallbar_6contacts_7709.yaml \
  --protocol hallbar_mr \
  --mode stream-ramp \
  --temperatures 300 \
  --fields 0 \
  --ramp-quantity field \
  --ramp-target 0.5 \
  --ramp-rate 60 \
  --stream-samples 50 \
  --stream-interval 0.1
```

---

## 20. Suggested Daily Workflow

For routine use, this is a good default pattern:

1. confirm config file and contact map
2. run `list-instruments`
3. do a `check-contacts`
4. run a short stable protocol
5. inspect CSV and metadata
6. move to a full sweep or stream-ramp
7. archive config, contact map, and output together

---

## 21. Where To Extend Next

If you plan to extend the software, start by reading:

- [src/electrical_measurements/runners/run_measurement.py](/home/emiliano/Documents/Automazione/M81_electr_meas/src/electrical_measurements/runners/run_measurement.py)
- [src/electrical_measurements/protocols/base.py](/home/emiliano/Documents/Automazione/M81_electr_meas/src/electrical_measurements/protocols/base.py)
- [src/electrical_measurements/gui/app.py](/home/emiliano/Documents/Automazione/M81_electr_meas/src/electrical_measurements/gui/app.py)
- [CODE_REVIEW.md](/home/emiliano/Documents/Automazione/M81_electr_meas/CODE_REVIEW.md)

Useful extension areas:

- more detailed troubleshooting docs
- more protocol-specific examples
- richer plots and analysis presets
- broader end-to-end test coverage

---

## 22. Final Notes

The quickest path to success with this software is:

- start in mock mode
- understand contact maps before touching hardware
- use stable mode before stream-ramp
- verify outputs after every config change
- keep the GUI Preview and Log tabs open while learning

If you follow that workflow, the package is already usable for both offline development and structured lab automation.
