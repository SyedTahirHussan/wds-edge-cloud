# Experiments

## Protocol

Every experiment runs on five seeds (`seeds: [1, 2, 3, 4, 5]`). A seed fixes:

- the demand multipliers and the temperature;
- the times and places of the process events;
- the faults;
- the sensor noise;
- the laboratory and maintenance sources.

Each component draws from its own random stream (`numpy.random.default_rng([seed,
stream])`), so changing one component leaves the others unchanged.

Per seed:

1. Generate the 8-week testbed (`flowmine.testbed.build`).
2. Fit the edge models on weeks 1-6 using observed data only: Holt-Winters
   parameters and state, the neural network, and the initial residual scale.
3. Calibrate the residual threshold per sensor on week 7 (target: 0.5 % of clean
   samples above it).
4. Run every station over weeks 7-8. Frames are decoded by the gateway, and the
   resulting Observation bodies are parsed into the same tidy frame that
   `StaClient.load_frame` returns. This is the cloud's view. The HTTP hop is left out
   for speed; the contract tests cover it against FROST-Server.
5. Fit the spatial models on weeks 1-6. Set their thresholds on week 7: once on raw
   data (for the central-only architecture) and once on edge-validated data (for the
   hybrid).
6. Run E1-E7 and the two checks below. Everything is scored on week 8.

Week-7 labels are used for three things only: the edge and spatial thresholds, the
fusion model, and the temporal/spatial crossover in E2. Week-8 labels are used for
scoring only. The bridge's level tolerance (0.2 m) was chosen by a sweep on seed 1's
calibration week.

Results are reported as mean ± sample standard deviation over the five seeds.
`results/raw/*.csv` holds the per-seed rows; `results/summary.md` holds every table.

## Experiments and metrics

### E1 detection

Five methods:

- rule tests only (range, rate, flatline, cross-check);
- edge with Holt-Winters;
- edge with the neural forecaster;
- central (spatial on raw data, plus the laboratory CUSUM for chlorine);
- hybrid fusion.

Metrics:

- *Sample-based.* TP and FN over labelled samples. FP are flagged samples carrying
  neither a fault nor a process-event label. Precision per fault type is
  TP_type / (TP_type + FP).
- *Event-based.* A fault event is detected when one of its samples is flagged. Its
  delay runs from its first sample to the first flagged sample. An alarm episode (a
  run of flagged samples of one sensor) counts as correct when it overlaps a fault.
- *Alarms on process events.* The share of event-affected samples that were flagged
  (wrong, because they are not faults).

Gaps appear in the per-type table but stay out of "overall": every method sees a
missing sample.

### E2 reconstruction

Gaps of 15 min to 24 h are placed on clean stretches,
cycling through pressure, flow and level, 12 per length and seed. Methods: last value,
linear interpolation (offline: it needs the next sample), seasonal naive,
Holt-Winters, neural, spatial (ridge on the other sensors) and hybrid (temporal below
the crossover, spatial above it). The crossover is found by the same comparison on
week 7. Metrics are MAE and RMSE against the noise-free truth, in physical units.

### E3 communication

Each station runs the test week under each policy. Metrics:

- binary and SensorThings-JSON bytes per station per day;
- frames per day;
- mean frame size relative to 51 bytes;
- median latency from a faulty sample to its arrival in the cloud.

For `delta`, ε is scaled by 0.25-4 and the cloud's reconstructed signal is compared
with the edge-validated value and with the truth, as a fraction of the sensor's range.

### E4 resilience

The link is down for 6 h and for 24 h, starting 36 h into the test
week. Two designs are compared: edge validation with store-and-forward, and a
centralised design that buffers raw samples. Metrics:

- fault events in the window;
- events flagged during the outage (at the station; the centralised design has no
  station validation and flags none);
- backlog;
- backfill time at an assumed 30 frames per hour.

Neither design loses samples: the outbox has no size limit and the station keeps
samples longer than the outage.

This is an assumption, not a LoRaWAN airtime calculation.

### E5 training paradigms

Pressure loggers, local / centralised / federated /
federated with fine-tuning, one cold-start station with 3 days of history. Metrics:
one-step RMSE against the truth on clean test samples, day-ahead RMSE from each test
midnight, and bytes moved.

### E6 twin

The bridge runs over the test week fed with raw, edge-validated,
hybrid-validated and noise-free inputs. Metric: RMSE of simulated against true
pressure at the 12 monitored nodes and at 6 unmonitored junctions.

### E7 process events

For the edge-only, central-only and hybrid architectures:

- the share of leak and surge events for which an event was raised;
- the share of event-affected samples that were reconstructed away;
- the share of non-gap sensor faults that were raised as events.

### E8 the v0.2 station

- E8a, conformal coverage. For α in {0.01, 0.005, 0.001} and with one or four
  time-of-day buckets, the station runs with only the conformal thresholds. On clean
  samples with a forecast, no rule-test flag and no level-shift release: the share
  rejected by the residual test, on week 7 (in-sample) and week 8, and the station's
  false-alarm rate over all tests.
- E8b, reconstruction intervals. Artificial gaps on clean stretches as in E2
  (12 per length per week, other random draws), calibration on week-7 gaps, scores on
  week-8 gaps: coverage, mean width and MAE by gap length, for the Holt-Winters and
  spatial estimates and two selectors.
- E8c, ablation: v0.1, conformal only, conformal + CUSUM, conformal + cross-sensor
  model, all three (= `edge_v2` in E1), with the E1 metrics.
- E8d, int8: RMSE of the cross-sensor MLPs and of the neural forecasters with
  float32 and int8 weights, on clean samples of weeks 7 and 8.
- The cross-sensor model choice (ridge or MLP) per sensor and the CUSUM parameter
  choice are written to `e8_xmodels.csv` and `e8_cusum.csv`.

`edge_v2` also appears in E1 as a method. The station footprint
(`results/edge_footprint_stations.csv`) comes from `flowmine experiments footprint`
and, like the forecaster timings, is not part of `make reproduce`.

### Checks

False spatial alarms with and without the maintenance log. Day-ahead
total-demand MAE with and without temperature.

## Reproduce

    make setup        # uv sync --frozen
    make reproduce    # testbed, all experiments, results/summary.md, README block

`make reproduce` runs the seeds in parallel processes with single-threaded BLAS and
writes CSVs with a fixed float format. A second run gives byte-identical files.

`results/edge_footprint.csv` (parameter count, model size, buffer memory, laptop
timings under CPython and MicroPython) contains timings and is therefore not
reproducible byte for byte. It is produced separately:

    uv run flowmine experiments footprint

`configs/smoke.yaml` runs the same pipeline with one seed, two weeks, fewer sensors
and gaps (`make smoke`, under a minute).

## Not done

Optional E8 (the validators on the BattLeDIM L-Town SCADA data [R14], dataset
doi:10.5281/zenodo.4017659, CC-BY-4.0) was not run.
