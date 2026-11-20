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

## Twin-based diagnosis (Phase 2, v0.3)

Version 0.3 uses the twin's output for three diagnostic tasks. The code is in
`central/leak.py` (sensitivity matrix, ranking, live watcher),
`experiments/leak_loc.py` (E9), `experiments/model_validation.py` (the model level of
validation, in E1) and `experiments/chlorine_cal.py` (E10).

### Leak localisation with a sensitivity matrix

This is the method of [R1], applied to the twin's residuals. The twin closes the water
balance with the measured inflows, so a leak raises its total demand as well as the
real one. What the residual sees is where the extra outflow goes. The sensitivity of
monitored node i to a leak at junction j is therefore computed against a run with the
same total demand spread over all junctions:

    S[h, i, j] = (p_i(h; q at j, minus q · b_k / Σb at every junction k) − p_i(h; no leak)) / q

with q = 10 L/s, b_k the base demands and h the hour of day. Each column comes from a
48-hour extended-period run of the twin's own model (base demands times one daily
multiplier profile, taken from the total demand of weeks 1–6); the second day is
kept. There are 92 candidate junctions and 12 monitored pressure nodes. The matrix
for one seed takes about 4 s and is cached under `data/`.

During a leak with onset t₀ the residual vector is

    r_i = mean over t in [t₀ + 6 h, t₀ + 12 h) of (y_i(t) − ŷ_i(t))  −  baseline_i

(the leak ramps up over 6 h). Three baselines were compared: none; the mean over the
same hours one day earlier; the mean over the same hours of the three previous days.
Each candidate gets a score against the window's mean sensitivity column
s̄_j = mean over the window's hours of S[h, :, j]:

    angle:        cos θ_j = rᵀ s̄_j / (|r| |s̄_j|)
    correlation:  the same after subtracting the mean over sensors from r and from s̄_j

Sensors without a usable residual are left out of both vectors. The operating variant
is the one with the highest share of week-7 test leaks in the top five (edge-validated
input), then the lower median distance; week 7 chose no baseline and the angle.
Both baselines did worse on week 7 and on week 8 (every variant is in
`results/summary.md`); I did not separate why.

The metrics are the rank of the leak's node among the 92 candidates and the length of
the shortest pipe path between the first candidate and the leak. For a leak in the middle
of a pipe (the testbed's own) the rank is that of the better-ranked end node, and the
distance adds half the pipe length.

In the live twin, `LeakWatcher` keeps the last 6 h of residuals. Every hour, when the
norm of their mean vector reaches 0.5 m, it ranks the candidates and the service
posts the first five on the `LEAK-CANDIDATES` Datastream. This alarm rule is a demo
setting and was not evaluated. Its matrix uses multiplier 1, because the live service
has no training history.

### Model-based validation level

The model level of the validation methodology [R5, R6]: the twin, driven by the
edge-validated tank levels and flows, predicts each pressure sensor, and the sample as
measured is compared with the prediction:

    r_i(t)  = y_i(t) − ŷ_i(t)
    r̃_i(t) = r_i(t) − median_k r_k(t)            (common mode removed)
    s_i(t)  = |r̃_i(t) − μ_i(h_t)|,   μ_i(h) = week-7 median of r̃_i at hour h

A sample is flagged when s_i(t) > q_i, with q_i the ⌈(n + 1)(1 − α)⌉-th smallest
score among the n week-7 clean samples of sensor i [R18] and α = 0.005, the station's
level. The common mode is removed because a fault on a boundary meter, or an
operation the model does not know about, moves every pressure residual together;
without the correction the median threshold was 5.1 m, against 2.0 m with it
(`e1_model_bands.csv`). The choice between the two residuals was made on the recall
of the labelled week-7 faults. The level covers the 12 pressure sensors only. A leak
also moves the residual near the leak, so this level cannot by itself tell a leak
from a sensor fault. The v0.1 fusion model is unchanged; the model level is reported
on its own (`central_model` in E1).

### Chlorine decay calibrated from laboratory data

The quality model is EPANET's first-order bulk reaction [R20] on the twin's demand
model, run as one extended-period simulation over the eight weeks: base demands
times one multiplier, the total demand divided by the sum of base demands (what the
water balance gives with clean meters). The process events are not in the model.
Net3.inp has no reaction coefficients, so the model's wall coefficient is zero, while
the testbed uses kb = −0.5 per day and a wall coefficient of −0.05 m per day. The
fitted kb is therefore an effective coefficient that carries the wall decay too.

EPANET runs once for each kb on a grid of 13 values between 0 and −3 per day. For an
observation i at node n_i and time t_i, Ĉ_i(kb) is the simulated concentration,
interpolated linearly in kb between grid points. On day d:

    kb_d = argmin over kb in {0, −0.01, …, −3} of  Σ_{i ∈ W_d} (y_i − Ĉ_i(kb))²
    W_d  = { i : t_i ≥ d − 14 days and a_i ≤ d }

where a_i is the time the observation became available: the reporting time for a
laboratory result (24–48 h after sampling) and the end of the hour for an analyser's
hourly mean. Two variants: laboratory results only, and laboratory results plus
analyser hourly means (raw samples in weeks 1–6, edge-validated unflagged samples in
week 7). A moving-window fit was chosen over recursive least squares because the
model is nonlinear in kb through transport and mixing; the grid with interpolation is
exact at the grid points, cheap and deterministic, and the window does the
forgetting.

The value on the first day of week 8 is scored. Week 8 is simulated with kb fixed at:
Net3.inp's value (no decay), the two estimates, the true kb with the model's zero
wall coefficient, and both true coefficients (the floor set by the hydraulic model).
The error is the mean absolute difference from the true concentration at the five
analyser nodes and from the week-8 laboratory results.

Simplified relative to [R4]: first-order bulk decay only, no temperature dependence
(the testbed has none), one known source concentration, simulated laboratory noise
(sd 0.01 mg/L), and a hydraulic model whose only error is the demand distribution.

## Stacked fusion with a false-alarm budget (Phase 3, v0.4)

`hybrid/stacking.py`, `experiments/stacking.py`. One L2 logistic regression
(scikit-learn, C = 1, balanced class weights) per seed, trained on week-7 samples
with their labels; gap faults and missing samples are left out of training and stay
flagged. Features per sample, all available when the sample is scored:

- from the v0.2 station: rejected by any test (including missing), released as a level
  shift, forecast residual score (|r| over the current threshold), absolute
  cross-sensor residual |xz|, and the CUSUM statistic over its decision interval h
  (recomputed from the station's residual trace with the sensor's own k and h on
  accepted samples; unlike the station, the recomputation does not reset after a
  level shift);
- from the centre: spatial ratio |z| / threshold on the edge-validated stream, the
  twin-residual ratio (pressure sensors only) and the laboratory CUSUM drift flag
  (chlorine analysers only);
- sensor kind (one-hot over pressure, flow, level, chlorine);
- maintenance context: 1 when a valve or pump operation affecting the sensor (same
  rule as the maintenance-log suppression) was logged in the last 6 h.

A missing value is set to 0 and a 0/1 indicator says it is missing (forecast score,
cross residual, CUSUM, spatial, twin). Continuous scores are clipped to [0, 10] and
every feature is standardised with its week-7 mean and sd. With p the model's
probability, the threshold for a budget B is

    t_B = the (n - floor(B n))-th smallest p among the n clean week-7 samples,

and a sample is flagged when p > t_B. Budgets: 0.5 %, 1 % (headline) and 2 %. The
week-7 rates are in-sample, because the model, the station's thresholds and the
spatial thresholds are all fitted on week 7; week 8 tests the claim. Coefficients are
written to `e11_coef.csv`.
