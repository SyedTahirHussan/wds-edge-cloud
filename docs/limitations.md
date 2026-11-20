# Limitations

### Simulated data only

Every number comes from an EPANET simulation of one benchmark
network with generated demands, injected faults and labelled events. Nothing here has
touched utility data, and the faults follow simple parametric shapes. Real faults (a
fouled chlorine membrane, a transmitter losing power slowly, a telemetry clock that
jumps) are messier and are not labelled.

### The station still misses most biases

Version 0.2 gives the station a model of each sensor from its neighbour at the same
station. On Net3 that neighbour is often a poor witness: at the five chlorine loggers
pressure says little about chlorine, and at the pump and tank stations the
cross-sensor residual has a spread close to the size of the injected biases. Five
loggers have one sensor and no cross-sensor test. The E1 and E8c tables give the
recall: better than v0.1, still far below the central spatial model, and bought with
a false-alarm rate about three times v0.1's. The drift CUSUM on the forecast
residual adds false alarms and almost no detections, because Holt-Winters follows a
slow drift. A station with a third, physically related sensor (a second pressure
point on the same main, or a pump's power draw) would be the next thing to try.

The conformal guarantee assumes exchangeable scores. Residuals are autocorrelated
and week 8 holds a leak that week 7 does not, so E8a is an empirical check, not a
proof. The reconstruction intervals cover less than their nominal level on the test
week for the same reason and because a week holds few gaps of each length.

### Leak localisation works on clean data, much less on measured data

With noise-free inputs the leak's node is among the first five of 92 candidates for
0.63 of the test leaks, and the first candidate is a median 518 m of pipe from the
leak. With edge-validated inputs these become 0.34 and 2414 m, about what raw inputs
give (`e9_leak.csv`, `results/summary.md`). Validation as built here does not close
the gap: it removes spikes and frozen values, but most biases and drifts, and all
meter noise, stay in the residual vector. Which of those costs more was not
separated. Even the clean input stops short of the method's ideal, because the twin
spreads demand with one multiplier while the testbed's demand varies node by node.

The test leaks are mostly synthetic. They are added to the measurements by
superposition, so the stations' reaction to a leak (level-shift flags, reconstructed
values) and the triage's reaction are not simulated; only the testbed's own leak, one
per seed, goes through the full pipeline. Leak onset is taken as known: E9 scores
localisation, not detection. The live twin's alarm rule (`LeakWatcher`) was not
evaluated.

### The model-based level cannot tell a leak from a sensor fault

The twin-residual check flags pressure faults with a false-alarm rate of 0.0029
against 0.040 for the spatial model (`e1_pressure.csv`), but a leak moves the
residual near the leak in the same way a bias does. It also covers only the pressure
sensors, and it relies on the boundary meters: the common-mode correction helps when
a boundary meter is wrong, but a fault that bends the pressure pattern unevenly
still reaches the individual residuals. It is reported on its own; the fusion model
does not use it yet.

### One small network

Net3 has 92 junctions and one consumer (node 203) that takes
about 41 % of the demand. Results that depend on the network may not transfer:

- spatial-model accuracy;
- the twin's state-estimation floor;
- the temporal/spatial crossover.

The optional run on BattLeDIM L-Town data [R14] was not done.

### Laptop timings, no ESP32

The edge core passes its tests under the MicroPython
1.29 Unix port on macOS. Timings in `results/edge_footprint.csv` were measured on a
laptop. No microcontroller, radio or battery was involved. Three differences on an
ESP32 matter:

- MicroPython uses 32-bit floats there, so the station's and the cloud's
  send-on-delta copies would drift apart slightly; this is not handled.
- The live heap of a v0.2 station under the MicroPython Unix port is a few percent of
  the ESP32's 520 KB SRAM (`results/edge_footprint_stations.csv`; Espressif ESP32
  Series Datasheet v5.3). MicroPython on an ESP32 gets only part of that SRAM, and
  nothing was run on one.
- The SQLite store would need a flat-file replacement.

### Simplified chlorine kinetics

The testbed uses first-order bulk and wall decay with
assumed coefficients, one source concentration and no temperature dependence. The
laboratory results are the simulated concentration plus Gaussian noise. Real
laboratory and analyser disagreement includes sampling-point and holding-time effects.

E10's model is bulk decay only, so its fitted kb (about −0.8 per day against the true
−0.5, `e10_chlorine.csv`) is an effective value that absorbs the wall decay. It fits
these five sampling points; whether one effective kb holds across a network with
different pipe diameters and residence times was not tested. The fit runs on the
twin's demand model offline, with the true total demand, not on the stepping bridge.
A real calibration would follow [R4]: temperature-dependent kb, wall decay per pipe
group, and the utility's own sampling programme.

### Synthetic maintenance log

The log is generated from the injected faults and
operations, so every recalibration matches a drift exactly and every operation is
recorded. A real log is incomplete and its timestamps are approximate. The central
drift flags end at the recalibration record, so the exact log favours the central and
hybrid methods on chlorine drift.

### Decisions taken for this prototype

- The training weeks are fault-free (a commissioning period).
- Thresholds use week-7 labels, which a utility would not have in that form.
- The twin uses one demand multiplier for the whole network.
- The live demo trains the central spatial model from the cached testbed rather than
  from SensorThings history.
- LoRaWAN airtime and duty cycle are not modelled. Bytes are counted, and backfill
  uses an assumed frame rate.

### No security layer

MQTT and HTTP run without TLS or authentication. The
FROST-Server database password sits in `docker-compose.yml`. Model packages are
checked with a SHA-256 hash, which catches corruption but not a malicious registry.

### PyTorch not used

The neural forecaster is trained in numpy, because PyTorch did
not install in this environment within the time set for it.

## What a utility deployment would need next

- Utility data with an incident log to score against, and fault labels from
  operators.
- Station firmware on real hardware: ESP32 with MicroPython, the radio stack, power
  budget, flash wear for the local store, and over-the-air model updates with signed
  packages.
- Calibrated chlorine kinetics and demand allocation in the hydraulic model, per DMA,
  with the large consumers metered.
- Authentication and TLS on MQTT and HTTP, per-station credentials and FROST-Server's
  access control.
- Operator workflow: who sees an event, how a correction proposed from laboratory data
  is approved, and how a rollback of a model version is triggered.
