# electrical_measurements

Framework Python per misure elettriche su campioni in funzione di temperatura e campo magnetico, con geometrie descritte da YAML e switching tramite Keithley/Tektronix DAQ6510 + matrice 7709.

## Caratteristiche

- geometrie non hardcodate: Van der Pauw, Hall bar 6/8 contatti e configurazioni custom
- interlock software per proteggere sorgenti e relay
- modalità mock/dry-run per sviluppo e test offline
- protocolli per magnetoresistenza, Hall, Van der Pauw, seconda armonica e reciprocità
- salvataggio dati in CSV, Parquet e metadata JSON

## Installazione

```bash
pip install -e .[test]
pip install lakeshore
```

Riferimenti Lake Shore obbligatori:

- Driver ufficiale: https://github.com/lakeshorecryotronics/python-driver
- Documentazione: https://lake-shore-python-driver.readthedocs.io/en/latest/
- Installazione driver: `pip install lakeshore`

Il wrapper usa `from lakeshore import SSMSystem` come interfaccia primaria per l'M81.

## Configurazione strumenti

Esempio [configs/instruments.yaml](/home/emiliano/Documents/Automazione/M81_electr_meas/configs/instruments.yaml):

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

`measure_modes` e facoltativo e permette di impostare il tipo di acquisizione preferito per ciascun canale di misura `M1/M2/M3`:

- `lockin`: lettura `x/y/r/theta`
- `dc`: lettura `value`
- `auto`: usa la configurazione del canale se presente, altrimenti fallback automatico

`measure_harmonics` e facoltativo e definisce, per ciascun canale in modalita `lockin`, quale armonica misurare:

- `1`: fondamentale
- `2`, `3`, ...: armoniche superiori

La GUI Live permette di cambiare sia `mode` sia `harmonic` per `M1/M2/M3`, mentre i protocolli automatici usano le preferenze configurate nel file YAML quando presenti.

Per un backend ambiente reale HTTP:

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

Modalita ambiente supportate:

- `integrated`: la app legge e comanda temperatura/campo
- `async-poll`: la app legge solo lo stato ambiente con polling, ma non invia setpoint o ramp
- `standalone`: la app lavora anche senza backend ambiente; `T` e `B` restano locali alla sessione

## Contact map YAML

Il package ragiona in termini di contatti logici del campione e traduce i contatti nei relay fisici della 7709.

Esempio Van der Pauw: [configs/contact_maps/vdp_4contacts_7709.yaml](/home/emiliano/Documents/Automazione/M81_electr_meas/configs/contact_maps/vdp_4contacts_7709.yaml)

Esempio Hall bar: [configs/contact_maps/hallbar_6contacts_7709.yaml](/home/emiliano/Documents/Automazione/M81_electr_meas/configs/contact_maps/hallbar_6contacts_7709.yaml)

## Mock mode

```bash
python examples/run_vdp_hall.py --mock
electrical-measure run --mock --config configs/instruments.yaml --protocol hall --contact-map configs/contact_maps/hallbar_6contacts_7709.yaml
electrical-measure run --mock --config configs/instruments.yaml --protocol vdp_hall --contact-map configs/contact_maps/vdp_4contacts_7709.yaml --include-reciprocity
electrical-measure run --mock --config configs/instruments.yaml --protocol vdp --contact-map configs/contact_maps/vdp_4contacts_7709.yaml --include-anisotropy
```

`vdp_hall` usa gli stati Van der Pauw per una misura Hall in geometria VdP. Con `--include-reciprocity` la routine aggiunge anche gli stati reciproci alla stessa sequenza e salva i relativi errori di reciprocita nei dati derivati.

Nel protocollo `vdp`, `--include-anisotropy` aggiunge un check di anisotropia tra le due famiglie ortogonali Van der Pauw e salva nei `derived` le medie delle due famiglie, la differenza assoluta/relativa e il rapporto tra esse.

## GUI

Il package include anche una GUI desktop leggera basata su `tkinter`:

```bash
electrical-measure gui --mock
```

Dalla GUI puoi:

- scegliere file di configurazione strumenti e contact map
- selezionare protocollo, modalità `stable` o `stream-ramp`
- scegliere dalla GUI la modalità ambiente `integrated`, `async-poll` o `standalone`
- scegliere dinamicamente gli stati di misura dal contact map
- vedere dettagli dei relay, stati reciproci e binding strumento-contatto
- impostare correnti, frequenza, armonica, campi e temperature
- lanciare misure mock o reali riusando lo stesso runner della CLI
- usare `Emergency Stop` dalla finestra
- vedere log, stato esecuzione e preview dei CSV generati in tab dedicate
- collegare strumenti live dalla GUI e monitorare `T`, `B`, sorgenti attive e relay chiusi
- vedere nella tab `Live` il badge della modalità ambiente effettiva e se il backend è read-only/local
- usare nella tab `Live` un pannello `Environment Controls` per `Set T/B` e `Start/Stop Ramp` solo quando la modalità è `integrated`
- configurare manualmente `S1`/`S2`/`S3` in corrente o tensione, in DC o AC lock-in, con setpoint, frequenza e armonica
- usare mini-sequenze guidate come `Safe Switch Then Enable`, `Safe Switch Then Read` e `Disable + Open All`
- applicare manualmente uno stato della matrice, aprire tutti i relay e leggere rapidamente `M1` o `M2`
- avviare un'acquisizione live continua con buffer circolare e grafico aggiornato in tempo reale direttamente dalla tab `Live`
- con un Hall bar dotato di `vxx_meter` e `vxy_meter`, acquisire live entrambi i canali e sovrapporli nel plot con `x_dual` o `r_dual`
- disegnare plot rapidi direttamente in GUI scegliendo colonne `X/Y` dal CSV

## Streaming durante rampa

Per acquisire dati sincronizzati durante una rampa di campo o temperatura:

```bash
electrical-measure run \
  --mock \
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

Il dataset risultante salva `trace_index`, `trace_channel`, `field_t`, `temperature_k` ed `environment_timestamp` per sincronizzare il trace M81 con il backend ambiente.

Quando il driver ufficiale Lake Shore espone `SSMSystem.get_data()`, il wrapper usa quello come prima scelta per lo streaming dati. Il fallback SCPI rimane confinato a [m81_scpi_fallback.py](/home/emiliano/Documents/Automazione/M81_electr_meas/src/electrical_measurements/instruments/m81_scpi_fallback.py).

## Sicurezza

- mai cambiare relay con sorgenti abilitate, salvo override esplicito
- prima del cambio relay il software spegne le sorgenti M81
- ogni cambio stato apre prima tutti i relay e poi chiude solo i canali validati
- in caso di errore `SafeMeasurementSession` esegue `emergency_stop()`

## Reciprocità

La reciprocità è trattata come concetto di primo livello:

- a `B = 0`: confronto diretto `R_ij,kl` vs `R_kl,ij`
- a `B != 0`: confronto `R_ij,kl(+B)` vs `R_kl,ij(-B)`

I risultati salvano stato, stato reciproco, tipo di pairing di campo ed errori assoluti/relativi.
