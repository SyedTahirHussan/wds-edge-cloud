# Methods

Parameter values quoted here are the defaults in `configs/default.yaml` and
`src/flowmine/config.py`. Results are in `results/summary.md`.

## Testbed (ground truth)

### Network

EPANET Net3 as shipped with WNTR 1.5 [R15]: 92 junctions, 117 pipes, 3
tanks, 2 reservoirs (River, Lake), 2 pumps. Net3 runs its Lake pump with absolute-time
controls that cover only the first week; they are converted to daily clock-time
controls (open 01:00, close 15:00). The simulation runs 8 weeks at a 5-minute
hydraulic, pattern, quality and report step in EPANET 2.2 through WNTR's
`EpanetSimulator`, starting on a Monday. Weeks 1-6 train, week 7 calibrates
thresholds and the fusion model, week 8 is the test week.

### Demand

Each junction gets its own multiplier series for the whole horizon:

    m_i(k) = D(tod, weekday) · (1 + β (T(k) − T_ref)) · (1 + c_i(k)) · (1 + a_i(k))

- D is a two-peak daily profile (07:30, 20:00) with amplitude 0.35, shifted 1.5 h later
  and scaled by 0.92 at weekends.
- T is the hourly air temperature, a daily cosine (±5 °C) plus a slow AR(1) synoptic
  term. β = 0.015 per °C and T_ref = 22 °C.
- a_i is AR(1) noise per node (φ = 0.97, sd 0.02).
- c_i is spatially correlated: four AR(1) latent series attached to random anchor
  nodes, mixed with Gaussian weights of the coordinate distance (length 0.3 × network
  extent, sd 0.04).

The product is normalised to mean one, and each base demand is the original base
times the mean of its original pattern. Net3 has one dominant consumer: node 203
carries about 41 % of the total base demand.

### Water quality

Chlorine is modelled as a single chemical with first-order bulk
decay k_b = −0.5 day⁻¹ and first-order wall decay k_w = −0.05 m/day. Both reservoirs
deliver 1.0 mg/L, and the tanks start at 0.6 mg/L. These are assumptions of ordinary
magnitude, not fitted to any network. Real chlorine kinetics (second-order or
temperature-dependent decay, as calibrated by Pérez et al. [R4]) are not modelled.

### Sensors

| class | count | period | noise sd | resolution |
|---|---|---|---|---|
| pressure | 12 | 5 min | 0.05 m | 0.01 m |
| flow | 5 (2 source, 3 tank inlets) | 5 min | 0.2 L/s + 0.5 % | 0.1 L/s |
| level | 3 | 15 min | 0.005 m | 0.01 m |
| chlorine | 5 | 15 min | 0.02 mg/L | 0.01 mg/L |

Pressure loggers are placed greedily, farthest point first, on the pipe-length
graph, starting from the two pump discharge nodes. The chlorine analysers sit at five
of the logger sites, chosen the same way.

The 25 sensors form 15 stations:

- two pump stations, each with a source meter and the discharge pressure;
- three tank stations, each with a level and the inlet meter;
- ten loggers, five of which also carry a chlorine analyser.

### Faults

Faults are injected into weeks 7 and 8 only; the training weeks are
treated as a commissioning period with clean data. Counts per week:

| fault | count/week | how it is injected |
|---|---|---|
| short gap | 6 | 1-6 samples missing |
| long gap | 3 | 2-24 h of samples missing |
| stuck | 3 | value frozen for 2-8 h |
| spike | 8 | one sample offset by ±0.3-0.8 × range |
| drift | 3 | linear drift over 1-3 days to ±0.2-0.5 × range |
| bias | 3 | step of ±0.15-0.35 × range for 4-24 h |
| noise burst | 3 | Gaussian noise of sd 0.05-0.15 × range for 1-4 h |

"Range" is the sensor's 5-95 % range over the training weeks. Half the drifts go to
chlorine analysers, and each drift ends with a recalibration record in the
maintenance log.

### Process events

These are real hydraulic changes, labelled so that the methods
can be scored on the opposite error:

- one pipe leak per test week: the pipe is split, and the leak is a demand
  Q = C_d A √(2 g p) with A = 0.0015 m², C_d = 0.75 and p from a leak-free first run,
  ramped in over 6 h;
- demand surges of 15-30 L/s for 2-5 h;
- valve closures and Lake-pump outages of 2-4 h, which the maintenance log records.

A sample counts as affected by an event when its true value differs from the
leak-free run by more than three noise standard deviations plus one resolution step.

### Other sources

- Laboratory grab samples: one per chlorine site per working day, between 08:00 and
  14:00, sd 0.01 mg/L, reported 24-48 h later.
- Maintenance log: recalibrations, replacements, valve operations, pump maintenance
  and routine checks.
- Hourly air temperature.

## Edge validation (MicroPython-compatible core)

The tests run in the order of the UPC methodology [R5, R6], restricted to what one
station can see. Each sample gets one integer quality code:

| bit | value | flag | set by |
|---|---|---|---|
| 0 | 1 | MISSING | slot without a sample |
| 1 | 2 | OUT_OF_RANGE | static bounds from sensor metadata |
| 2 | 4 | RATE | first-difference bound |
| 3 | 8 | FLATLINE | zero variance over a window |
| 4 | 16 | MODEL_RESIDUAL | one-step forecast residual |
| 5 | 32 | CROSSCHECK | tank level against inlet flow |
| 6 | 64 | RECONSTRUCTED | value is the forecast |
| 7 | 128 | SHIFT | accepted level shift, left to the cloud |
| 8-15 | | model version (mod 256) | |

1. Communication. Each timestamp is placed on the sensor's grid. The test detects
   duplicates and out-of-order samples (both dropped and counted) and gaps (the
   missing slots are reconstructed).
2. Range. lo ≤ x ≤ hi. Pressure −10…150 m, so that a vacuum reading at a stopped
   pump is not flagged. Flow ±1500 L/s. Level: tank minimum −0.5 m to maximum
   +0.5 m. Chlorine −0.1…4 mg/L.
3. Rate. |x − x_last good| ≤ r_max · Δt. r_max is 0.05 m/s (pressure), 2 L/s per s
   (flow), 0.002 m/s (level) and 0.0005 mg/L per s (chlorine). Pump switching moves
   pressures and flows quickly, so these bounds are wide.
4. Flatline. The last w raw values span at most one resolution step (w = 12
   samples for pressure and flow, 8 for level and chlorine). Flows within 1 L/s of
   zero are exempt, because a stopped pump reads a constant zero.
5. Residual. r = x − x̂ with x̂ the one-step forecast. The scale s is an
   exponentially weighted mean of |r| (λ = 0.01), each residual clipped at 3s before
   it enters. A sample is rejected when |r| > k · 1.2533 · max(s, s_min). k starts at 4
   and is calibrated per sensor on week 7: it is raised so that 0.5 % of clean
   calibration samples exceed it.
6. Cross-check (tank stations). Over each level interval:

       Δh_measured ≈ sign · ∫Q dt / A

   with Q integrated by the trapezoidal rule and tolerance 0.1 m. On failure both the
   level sample and the flow samples of the interval get CROSSCHECK. Neither is
   reconstructed, because the station cannot tell which instrument is wrong.

### Level shifts

Samples rejected by the residual test alone are held back, at most
two. If the next sample passes, they were a transient and are released as
reconstructed. If two residual-only rejections of the same sign follow each other,
the level has moved: a pump switching, a leak, or a bias fault. The station cannot
tell these apart, so it releases the samples with their raw values and SHIFT,
re-levels the forecaster and leaves the decision to the cloud. This choice is why
the edge alone detects almost no bias faults (E1).

### Reconstruction

A rejected or missing sample becomes the forecast, flagged
RECONSTRUCTED, with the raw value kept. The forecaster is then advanced with "no
observation", so rejected values never enter its state. For gaps, forecasts are
recursive. After 24 h without a valid sample the station stops reconstructing.

### Forecasters

All three share the interface `update(x, t)` / `predict(h)` / `state()` / `load()`.

- Seasonal naive: the value at the same slot one week earlier, from a ring buffer
  of 2016 (5-minute) or 672 (15-minute) floats.
- Double-seasonal Holt-Winters, after Taylor [R11], in additive form without a
  trend. Taylor uses multiplicative seasonality and a trend; pressures and flows sit
  near zero when a pump stops, and a trend diverges over long gaps.

      e_t = y_t − (l_{t−1} + d_{t−s1} + w_{t−s2})
      l_t = α (y_t − d_{t−s1} − w_{t−s2}) + (1 − α) l_{t−1}
      d_t = δ (y_t − l_t − w_{t−s2}) + (1 − δ) d_{t−s1}
      w_t = ω (y_t − l_t − d_{t−s1}) + (1 − ω) w_{t−s2}
      ŷ_{t+h} = l_t + d_{t+h−s1} + w_{t+h−s2} + φ^h e_t

  s1 is one day and s2 one week. α, δ and ω are chosen per sensor by grid search
  over {0.02, 0.05, 0.1, 0.2} on one-step error over the second half of training. φ
  is the grid value in {0, 0.5, 0.8, 0.95} closest to the lag-1 autocorrelation of the
  remaining error. A missing sample leaves level and seasonal indices unchanged and
  multiplies e by φ.
- Neural: a dense network, 17 inputs → 32 ReLU → 1, with 609 parameters. Inputs,
  z-scored per sensor: the last 12 values, the value one day earlier, and sin/cos of
  time of day and day of week. The output is the change from the last value.
  Recursive forecasts are clamped to ±8 training standard deviations; without the
  clamp, 12- and 24-hour recursions diverged. Training is numpy with manual
  backpropagation and Adam (30 epochs, learning rate 0.003, batch 256). The edge reads
  weights from a 2.4 kB float32 file and runs a pure-Python forward pass; a test checks
  it against the numpy forward to 1e-5.

### Uplink frame

A 6-byte header (station u8, record count and the low 3 bits of the model
version u8, base time u32) followed by records of 6 bytes each (sensor u8, Δt u16,
value i16 in resolution units, quality u8). One reading costs 12 bytes; summary
records cost 10. The payload format version would travel in the LoRaWAN FPort.
Frames are capped at 51 bytes, the EU868 application payload at DR0-DR2 with
repeaters allowed (LoRaWAN Regional Parameters RP002-1.0.4, Table 12; at DR4-DR7
the cap is 222 bytes).

### Uplink policies

- `raw`: every raw sample, unvalidated.
- `validated`: every record with its quality code.
- `delta`: send-on-delta with dual prediction.
- `summary`: 15-minute mean/min/max plus every flagged record.

Records are flushed every 15 minutes. A rejected record or a level shift forces an
immediate flush, except under `raw`.

### Send-on-delta

The station and the cloud hold the same Holt-Winters copy. A value
is sent when it differs from the shared forecast by more than ε, when it carries a
flag, or when an hour has passed. Otherwise both sides feed the forecast to their
copy. Transmitted values are quantised before entering the model, so both copies stay
identical. ε is 0.5 m (pressure), 2 L/s (flow), 0.05 m (level) and 0.05 mg/L
(chlorine). The cloud's error is at most ε between transmissions and half a
resolution step on transmitted slots; a property test checks this. On an ESP32,
MicroPython uses 32-bit floats while the cloud uses 64-bit ones, so the copies would
drift apart slightly. That is not handled here.

## Station v0.2 (Phase 1)

Three additions, fitted or calibrated in the cloud and carried in the model package
(`meta` of each sensor). A package without them runs as v0.1. The cloud side is
`edge/v2.py`; the station side is `edge/core/conformal.py`, `cusum.py`, `xmodel.py`
and `station.py`. All three run under MicroPython (tests in
`tests/micropython/core_tests.py`).

### Conformal residual thresholds

The nonconformity score of a sample is its residual in units of the running scale,

    z_t = r_t / (1.2533 · max(s_t, s_min))

the same quantity the v0.1 test compares with k. On week 7 the station runs with the
v0.1 parameters and records z for every clean sample (no fault and no process-event
label) that had a forecast and was not later released as a level shift. With n such
scores, the threshold at level α is the ⌈(n + 1)(1 − α)⌉-th smallest |z| [R18]; when
that rank exceeds n the threshold is infinite. It replaces k, and the station rejects
a sample when |z_t| > q. For exchangeable scores this bounds the rejection rate of
clean samples by α. Residuals are autocorrelated, so the bound is checked
empirically on the test week (E8a). The operating level is α = 0.005, the rate v0.1
was calibrated for; v0.1 also floored k at 4, which v0.2 does not. A variant with one
threshold per 6-hour bucket of the day is reported in E8a and not used: a 15-minute
sensor has about 670 week-7 samples, 168 per bucket, fewer than 1/α for α ≤ 0.005.

### Drift: two-sided CUSUM

On every accepted sample [R17]:

    S⁺_t = max(0, S⁺_{t−1} + z_t − k)      S⁻_t = max(0, S⁻_{t−1} − z_t − k)

with z clipped to ±4. While max(S⁺, S⁻) > h, accepted samples carry MODEL_RESIDUAL
without RECONSTRUCTED (the forecaster has followed the drift, so its value is no
better). A level shift resets both sums. Calibration uses a second week-7 pass with
the conformal thresholds installed: for each k in {0.25, 0.5, 0.75, 1}, h per sensor
is the 0.998 quantile of the statistic on clean samples, and the k with the best
week-7 drift recall is kept. Page-Hinkley was the alternative; the two-sided CUSUM
uses the known zero mean of the residual as its reference and treats both drift
directions alike.

### Cross-sensor model

At each station with two sensors (pump: flow and discharge pressure; tank: level and
inlet flow; chlorine logger: pressure and chlorine) each sensor y is predicted from
the other sensor o:

    f = [o, o − o_prev, o², sin 2πτ, cos 2πτ, sin 4πτ, cos 4πτ]
    ŷ = ȳ + σ_y · g((f − μ) / σ)

with τ the time of day in days, o the latest validated sample of the other sensor
strictly before t and o_prev the one before it. g is ridge regression (α = 1 on
standardised features) or an MLP with 8 ReLU units, fitted on weeks 1–6; the MLP is
used when its week-7 RMSE on clean samples is lower (E8 table "cross-sensor
models"). Its weights are stored as int8 with one scale per tensor, w = scale · q,
scale = max|w| / 127; E8d reports the RMSE change, which stays far inside the spread
between seeds on week 7.

The standardised residual e = (y − ŷ) / s, with s its training sd, gets two tests:
|e| > q_x with q_x the conformal threshold at α = 0.002, and a two-sided CUSUM on e
(k from {0.5, 1, 1.5, 2, 3} by week-7 bias and drift recall, h for 0.2 % of clean
samples in alarm). A failing test does not say which instrument is wrong. The
station flags y (CROSSCHECK, value kept) when

- y's CUSUM is in alarm and the CUSUM of its input is not (a structured-residual
  signature: a bias on y moves e_y by the bias and e_o only through o's model's
  sensitivity to y), or
- |e| > q_x, y released a level shift or a drift flag between 30 min and 72 h
  earlier, and o did not do so within 30 min of it (a pump switch moves both).

### Reconstruction intervals

The half-width of a reconstruction h seconds into a gap is q_h · s, with s the
sensor's training residual scale and q_h the conformal quantile (α = 0.1) of
|error| / s over week-7 artificial gaps, pooled over sensors, in horizon bins
of 15 min, 1 h, 3 h, 6 h, 12 h and 24 h; separate tables for the station's
Holt-Winters forecast and the spatial estimate. The "narrower interval" selector
uses whichever method has the smaller q_h at that horizon; v0.1's selector uses a
crossover gap length. Only the station's table would travel to the station;
`conformal.halfwidth` is the lookup. The codec does not carry the interval.

### Footprint

`flowmine experiments footprint` builds four stations from their packages
(`edge/core/memprobe.py`) and feeds them one day of week-7 samples under the
MicroPython Unix port. The live heap is `gc.mem_alloc()` after `gc.collect()`, minus
the heap before the station was built. MicroPython boxes every float result, so the
bytes allocated while one sample is processed are mostly garbage; they are reported
separately as an upper bound on the transient need.

## Central analytics (Phase 2)

### Homogenisation

Observations go onto a 5-minute grid. 15-minute streams are
carried forward to the next observation and never beyond it. Laboratory results are
used only after their result time.

### Spatial models

(pressure, flow and level; 20 streams). Both re-implement the
spatial stage of [R5, R6] with generic linear models.

- PCA on standardised training data keeps the components for 99 % of the variance.
  A sample is projected using only its usable entries:

      t = (P_oᵀP_o)⁻¹ P_oᵀ x_o,   SPE = |x_o − P_o t|²

  Entries the edge flagged count as missing, which is the "zero weight" rule.
- Ridge regression of each sensor on all others at lags 0-2 (α = 1). Flagged inputs
  are first replaced by their PCA reconstruction. The alarm statistic is
  z_i = (x_i − x̂_i) / sd_i, with sd_i from training residuals. Thresholds are the
  99.5 % quantile of |z_i| on clean week-7 samples.

### Chlorine with laboratory data

For each grab sample, d = analyser − lab, with the
analyser value at the sampling time. A two-sided tabular CUSUM runs on (d − μ₀)/σ,
with k = 0.5 and h = 5, where μ₀ and σ come from the training weeks. The alarm time is
the laboratory result time. The offset estimate is the mean of (d − μ₀) since the
alarming sum last left zero, and the proposed correction subtracts it. Recalibration
and replacement records reset the sums.

### Maintenance log

A valve-operation or pump-maintenance record suppresses spatial
alarms for 6 h on the sensors of the four stations nearest the asset (shortest pipe
path) and on any sensor on the asset itself.

### Temperature

A day-ahead hourly total-demand forecast uses ridge regression on
168 hour-of-week indicators and the demand 24 h earlier, fitted with and without the
hourly temperature. The temperature is taken as known, which favours it.

### Triage

The test week is split into 30-minute windows.

- A sensor is a sensor fault (reconstructed from the spatial estimate) when it is
  inconsistent with the others and at most two sensors are.
- A process event is raised when at least two sensors show an edge anomaly (residual
  rejection or level shift) and at least half of those stay spatially consistent.
- Windows in which a pump-station meter jumps by more than 30 % of its range are
  routine operation and raise no event.

### Fusion

(hybrid detection). A logistic model on four features: edge rejection,
edge level shift, the spatial ratio |z|/threshold (capped at 10), and shift × ratio.
It is fitted on week 7 with class weights balanced, and the decision threshold
maximises week-7 F1. Chlorine samples are flagged when the edge rejects them or the
laboratory CUSUM is in alarm.

## Edge forecaster training (E5)

The edge forecaster can be trained three ways (pressure loggers only):

- Local: each station trains its own network on its own data.
- Centralised: stations upload raw samples, 6 bytes each plus frame headers; the
  cloud trains one network on the pooled, per-sensor z-scored data.
- Federated averaging [R12]: 10 rounds, 3 local epochs per round, weights averaged
  in proportion to sample counts. An optional final variant fine-tunes one epoch at
  each station.

One station is a cold start with three days of history.

## Twin bridge (Outcome 2)

`flowmine.twin` steps EPANET 2.2 through WNTR's toolkit wrapper
(`wntr.epanet.toolkit.ENepanet`): one 5-minute period at a time, hydraulics and water
quality together. The model is the plain `.inp` with base demands and no patterns;
controls on pumps that have their own meter are removed.

Each period sets three kinds of boundary value:

- the status of the metered pump: open when its flow exceeds 1 L/s;
- one demand multiplier for all junctions:

      m = (Σ source flows − Σ sign · inlet flows) / Σ base demands

- tank levels, applied only when a new level observation exists. When a measured level
  differs from the model by more than 0.2 m, the model is re-initialised with the
  measured levels, carrying over node chlorine and link statuses. Pipes attached to a
  tank are excluded from the carry-over, because EPANET closes them temporarily when
  the tank is full. An earlier version carried those closures over, which shut the
  tanks for good. The 0.2 m tolerance was chosen by a sweep on the
  calibration week.

Flagged inputs are replaced by the edge reconstruction if one exists. Otherwise the
last value is held and the step is marked degraded.

With noise-free levels, true pump flows and true per-node demands, the bridge
reproduces the reference simulation within 0.01 m; a test checks this. With the
single multiplier, it cannot reproduce the node-to-node demand differences (node 203
in particular), and this sets the floor of the E6 error.
