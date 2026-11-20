# Results summary

Configuration `default`, seeds 1, 2, 3, 4, 5; mean ± sample standard deviation over seeds; test week only unless stated.

## Key numbers

- Overall sample F1 (non-gap faults): Hybrid fusion is highest (0.427); the edge-only methods reach 0.153 (Holt-Winters) and 0.153 (neural), the central methods 0.393.
- bias: recall 0.01 at the edge (Holt-Winters) against 0.62 in the cloud; the cloud detects it better.
- drift: recall 0.01 at the edge (Holt-Winters) against 0.28 in the cloud; the cloud detects it better.
- spike: recall 0.82 at the edge (Holt-Winters) against 0.53 in the cloud; the edge detects it better.
- False-alarm rate on clean samples: hybrid 0.0406, edge Holt-Winters 0.0055, central 0.0354.
- v0.2 station against v0.1: F1 0.248 against 0.153, false-alarm rate 0.0151 against 0.0055, bias recall 0.098 against 0.005, drift recall 0.113 against 0.014.
- Conformal residual thresholds (one table per sensor): on clean test-week samples the residual test rejects 0.0065 at nominal 0.01, 0.0037 at nominal 0.005, 0.0007 at nominal 0.001.
- Reconstruction intervals at nominal coverage 0.90 cover 0.85 (Holt-Winters), 0.89 (spatial) and 0.85 (selector by interval width) of test-week gap samples.
- Reconstruction: the calibration week puts the temporal/spatial crossover at 276 ± 277 min. For 1440-min pressure gaps the MAE is 0.499 m (Holt-Winters), 0.281 m (spatial) and 4.930 m (linear interpolation, which needs the future).
- Uplink: delta sends 0.36 of the bytes of the validated policy; SensorThings JSON is 34 times the binary frame size for the same records.
- Cold-start station, one-step RMSE: local 2.220 m, centralised 1.705 m, federated 2.136 m, federated with fine-tuning 2.102 m.
- Twin state error at unmonitored nodes: raw 0.92 m, edge-validated 0.67 m, hybrid 0.64 m, clean 0.47 m.
- Process events: share of event samples reconstructed away central_only 0.016, edge_only 0.010, hybrid 0.011; share of sensor faults raised as events central_only 0.52, edge_only 0.42, hybrid 0.54.
- Leak localisation (E9, sensitivity matrix on the twin's residuals): top-5 share raw 0.34, edge_validated 0.34, hybrid_validated 0.36, clean 0.63; median distance from the top candidate to the leak raw 2534 m, edge_validated 2414 m, hybrid_validated 2368 m, clean 518 m.

## E1 detection (overall, non-gap faults)

| method | precision | recall | F1 | event recall | event precision | delay (min) | false-alarm rate | alarms on process events |
|---|---|---|---|---|---|---|---|---|
| Rule tests only | 0.745 ± 0.082 | 0.078 ± 0.008 | 0.142 ± 0.014 | 0.300 ± 0.079 | 0.146 ± 0.047 | 94.308 ± 119.008 | 0.002 ± 0.001 | 0.002 ± 0.001 |
| Edge, Holt-Winters | 0.523 ± 0.041 | 0.090 ± 0.009 | 0.153 ± 0.014 | 0.760 ± 0.065 | 0.107 ± 0.020 | 189.218 ± 84.023 | 0.005 ± 0.001 | 0.008 ± 0.002 |
| Edge, neural | 0.513 ± 0.058 | 0.090 ± 0.010 | 0.153 ± 0.017 | 0.740 ± 0.102 | 0.108 ± 0.024 | 109.134 ± 58.190 | 0.006 ± 0.001 | 0.007 ± 0.002 |
| Edge v0.2 | 0.428 ± 0.060 | 0.181 ± 0.087 | 0.248 ± 0.099 | 0.790 ± 0.089 | 0.099 ± 0.016 | 172.988 ± 69.529 | 0.015 ± 0.005 | 0.025 ± 0.006 |
| Central (spatial + lab) | 0.499 ± 0.231 | 0.367 ± 0.138 | 0.393 ± 0.140 | 0.440 ± 0.195 | 0.129 ± 0.034 | 126.414 ± 219.748 | 0.035 ± 0.039 | 0.067 ± 0.025 |
| Hybrid fusion | 0.470 ± 0.209 | 0.428 ± 0.078 | 0.427 ± 0.103 | 0.650 ± 0.137 | 0.094 ± 0.029 | 197.483 ± 138.301 | 0.041 ± 0.029 | 0.082 ± 0.051 |

### E1 by fault type

| method | fault_type | recall | F1 | delay (min) |
|---|---|---|---|---|
| Rule tests only | gap_short | 1.00 ± 0.00 | 0.54 ± 0.04 | 0.00 ± 0.00 |
| Rule tests only | gap_long | 1.00 ± 0.00 | 0.93 ± 0.03 | 0.00 ± 0.00 |
| Rule tests only | stuck | 0.82 ± 0.03 | 0.77 ± 0.06 | 54.67 ± 12.55 |
| Rule tests only | spike | 0.23 ± 0.16 | 0.07 ± 0.05 | 0.00 ± 0.00 |
| Rule tests only | drift | 0.01 ± 0.02 | 0.02 ± 0.04 | 1275.00 |
| Rule tests only | bias | 0.00 ± 0.00 | 0.00 ± 0.00 | 247.50 ± 251.02 |
| Rule tests only | noise_burst | 0.11 ± 0.10 | 0.11 ± 0.11 | 5.00 ± 8.66 |
| Edge, Holt-Winters | gap_short | 1.00 ± 0.00 | 0.27 ± 0.05 | 0.00 ± 0.00 |
| Edge, Holt-Winters | gap_long | 1.00 ± 0.00 | 0.83 ± 0.04 | 0.00 ± 0.00 |
| Edge, Holt-Winters | stuck | 0.81 ± 0.03 | 0.58 ± 0.05 | 61.67 ± 4.08 |
| Edge, Holt-Winters | spike | 0.82 ± 0.14 | 0.10 ± 0.01 | 0.00 ± 0.00 |
| Edge, Holt-Winters | drift | 0.01 ± 0.02 | 0.02 ± 0.03 | 1087.00 ± 463.42 |
| Edge, Holt-Winters | bias | 0.01 ± 0.00 | 0.01 ± 0.01 | 390.00 ± 411.97 |
| Edge, Holt-Winters | noise_burst | 0.24 ± 0.10 | 0.14 ± 0.04 | 7.17 ± 9.82 |
| Edge, neural | gap_short | 1.00 ± 0.00 | 0.26 ± 0.06 | 0.00 ± 0.00 |
| Edge, neural | gap_long | 1.00 ± 0.00 | 0.82 ± 0.08 | 0.00 ± 0.00 |
| Edge, neural | stuck | 0.82 ± 0.03 | 0.58 ± 0.04 | 54.67 ± 12.55 |
| Edge, neural | spike | 0.93 ± 0.11 | 0.11 ± 0.03 | 0.00 ± 0.00 |
| Edge, neural | drift | 0.01 ± 0.02 | 0.02 ± 0.03 | 947.50 ± 547.06 |
| Edge, neural | bias | 0.00 ± 0.00 | 0.01 ± 0.01 | 595.00 ± 220.34 |
| Edge, neural | noise_burst | 0.25 ± 0.12 | 0.14 ± 0.05 | 7.50 ± 11.18 |
| Edge v0.2 | gap_short | 1.00 ± 0.00 | 0.13 ± 0.04 | 0.00 ± 0.00 |
| Edge v0.2 | gap_long | 1.00 ± 0.00 | 0.65 ± 0.09 | 0.00 ± 0.00 |
| Edge v0.2 | stuck | 0.82 ± 0.01 | 0.37 ± 0.08 | 55.67 ± 11.76 |
| Edge v0.2 | spike | 0.88 ± 0.15 | 0.04 ± 0.01 | 0.00 ± 0.00 |
| Edge v0.2 | drift | 0.11 ± 0.10 | 0.14 ± 0.11 | 925.17 ± 513.52 |
| Edge v0.2 | bias | 0.10 ± 0.13 | 0.10 ± 0.13 | 343.12 ± 331.99 |
| Edge v0.2 | noise_burst | 0.25 ± 0.14 | 0.07 ± 0.03 | 8.50 ± 11.94 |
| Central (spatial + lab) | gap_short | 1.00 ± 0.00 | 0.09 ± 0.05 | 0.00 ± 0.00 |
| Central (spatial + lab) | gap_long | 1.00 ± 0.00 | 0.53 ± 0.28 | 0.00 ± 0.00 |
| Central (spatial + lab) | stuck | 0.15 ± 0.21 | 0.06 ± 0.11 | 134.17 ± 18.43 |
| Central (spatial + lab) | spike | 0.53 ± 0.32 | 0.01 ± 0.01 | 0.00 ± 0.00 |
| Central (spatial + lab) | drift | 0.28 ± 0.18 | 0.27 ± 0.19 | 735.00 ± 1144.04 |
| Central (spatial + lab) | bias | 0.62 ± 0.41 | 0.36 ± 0.30 | 0.00 ± 0.00 |
| Central (spatial + lab) | noise_burst | 0.24 ± 0.26 | 0.05 ± 0.05 | 19.72 ± 15.42 |
| Hybrid fusion | gap_short | 1.00 ± 0.00 | 0.07 ± 0.06 | 0.00 ± 0.00 |
| Hybrid fusion | gap_long | 1.00 ± 0.00 | 0.48 ± 0.26 | 0.00 ± 0.00 |
| Hybrid fusion | stuck | 0.61 ± 0.21 | 0.19 ± 0.16 | 72.33 ± 13.00 |
| Hybrid fusion | spike | 0.50 ± 0.29 | 0.02 ± 0.02 | 0.00 ± 0.00 |
| Hybrid fusion | drift | 0.31 ± 0.09 | 0.28 ± 0.08 | 793.00 ± 321.10 |
| Hybrid fusion | bias | 0.66 ± 0.39 | 0.35 ± 0.28 | 176.67 ± 382.13 |
| Hybrid fusion | noise_burst | 0.31 ± 0.24 | 0.05 ± 0.05 | 26.17 ± 19.49 |

## E2 reconstruction MAE by gap length

### pressure (m)

| gap (min) | last_value | linear_offline | seasonal_naive | holt_winters | mlp | spatial | hybrid |
|---|---|---|---|---|---|---|---|
| 15 | 0.222 ± 0.207 | 0.192 ± 0.283 | 1.816 ± 3.445 | 0.261 ± 0.184 | 0.193 ± 0.063 | 0.441 ± 0.285 | 0.261 ± 0.184 |
| 60 | 0.598 ± 0.908 | 0.508 ± 0.949 | 0.434 ± 0.237 | 0.446 ± 0.456 | 0.504 ± 0.277 | 0.582 ± 0.481 | 0.640 ± 0.474 |
| 180 | 1.097 ± 0.625 | 0.491 ± 0.427 | 0.553 ± 0.221 | 0.629 ± 0.401 | 0.952 ± 0.245 | 0.232 ± 0.206 | 0.471 ± 0.469 |
| 360 | 4.323 ± 3.753 | 2.437 ± 1.692 | 0.813 ± 0.979 | 1.372 ± 0.957 | 1.983 ± 1.641 | 0.402 ± 0.301 | 0.618 ± 0.728 |
| 720 | 4.345 ± 2.337 | 3.085 ± 1.331 | 0.718 ± 0.423 | 1.410 ± 0.398 | 4.733 ± 3.077 | 0.179 ± 0.095 | 0.179 ± 0.095 |
| 1440 | 4.867 ± 5.383 | 4.930 ± 5.403 | 0.574 ± 0.282 | 0.499 ± 0.163 | 3.462 ± 4.807 | 0.281 ± 0.227 | 0.281 ± 0.227 |

### flow (L/s)

| gap (min) | last_value | linear_offline | seasonal_naive | holt_winters | mlp | spatial | hybrid |
|---|---|---|---|---|---|---|---|
| 15 | 2.570 ± 1.995 | 0.745 ± 0.445 | 26.100 ± 31.831 | 14.431 ± 10.703 | 4.142 ± 1.353 | 13.303 ± 9.793 | 14.431 ± 10.703 |
| 60 | 8.363 ± 7.097 | 3.694 ± 3.140 | 7.731 ± 4.518 | 8.611 ± 6.803 | 9.568 ± 6.722 | 9.656 ± 8.091 | 9.935 ± 7.920 |
| 180 | 34.315 ± 31.870 | 14.860 ± 13.192 | 8.620 ± 6.085 | 13.788 ± 6.274 | 15.099 ± 8.783 | 8.071 ± 6.642 | 13.053 ± 9.406 |
| 360 | 86.962 ± 45.134 | 46.460 ± 27.758 | 20.622 ± 16.302 | 13.850 ± 6.657 | 22.624 ± 21.349 | 5.475 ± 2.567 | 6.844 ± 2.901 |
| 720 | 93.811 ± 41.843 | 67.154 ± 24.342 | 11.431 ± 10.299 | 11.889 ± 10.361 | 13.292 ± 8.875 | 7.193 ± 10.341 | 7.193 ± 10.341 |
| 1440 | 103.268 ± 7.652 | 103.476 ± 7.473 | 11.883 ± 20.523 | 3.996 ± 2.563 | 15.882 ± 16.196 | 2.294 ± 2.051 | 2.294 ± 2.051 |

### level (m)

| gap (min) | last_value | linear_offline | seasonal_naive | holt_winters | mlp | spatial | hybrid |
|---|---|---|---|---|---|---|---|
| 15 | 0.081 ± 0.057 | 0.006 ± 0.001 | 0.321 ± 0.166 | 0.022 ± 0.013 | 0.021 ± 0.014 | 0.320 ± 0.205 | 0.022 ± 0.013 |
| 60 | 0.435 ± 0.274 | 0.045 ± 0.020 | 0.585 ± 0.267 | 0.047 ± 0.020 | 0.053 ± 0.028 | 0.350 ± 0.429 | 0.298 ± 0.462 |
| 180 | 0.440 ± 0.429 | 0.110 ± 0.066 | 0.238 ± 0.159 | 0.097 ± 0.024 | 0.073 ± 0.024 | 0.233 ± 0.164 | 0.195 ± 0.166 |
| 360 | 1.212 ± 0.335 | 0.645 ± 0.282 | 0.673 ± 0.284 | 0.203 ± 0.056 | 0.244 ± 0.166 | 0.237 ± 0.108 | 0.202 ± 0.094 |
| 720 | 1.632 ± 0.998 | 0.910 ± 0.609 | 0.332 ± 0.159 | 0.201 ± 0.081 | 0.257 ± 0.167 | 0.206 ± 0.076 | 0.206 ± 0.076 |
| 1440 | 0.860 ± 0.374 | 0.895 ± 0.430 | 0.243 ± 0.252 | 0.314 ± 0.055 | 0.120 ± 0.039 | 0.132 ± 0.001 | 0.132 ± 0.001 |

## E3 uplink policies

| policy | binary B/station/day | JSON B/station/day | frames/station/day | frame fill (of 51 B) | latency (min) |
|---|---|---|---|---|---|
| raw | 2820.0 ± 9.6 | 96020.9 ± 392.7 | 95.9 ± 0.3 | 0.6 ± 0.0 | 14.0 ± 2.2 |
| validated | 2878.2 ± 5.9 | 98116.7 ± 101.2 | 102.1 ± 1.0 | 0.6 ± 0.0 | 13.0 ± 2.7 |
| delta | 1039.1 ± 73.3 | 28669.5 ± 2346.3 | 65.6 ± 3.3 | 0.3 ± 0.0 | 5.0 ± 0.0 |
| summary | 2322.3 ± 27.9 | 56197.3 ± 1050.2 | 101.0 ± 0.8 | 0.5 ± 0.0 | 30.0 ± 0.0 |

### E3 delta policy versus epsilon

| eps_factor | binary B/station/day | error vs edge (fraction of range) | error vs truth (fraction of range) |
|---|---|---|---|
| 0.25 | 1719.8840 ± 92.1021 | 0.0022 ± 0.0001 | 0.0156 ± 0.0008 |
| 0.5 | 1345.2000 ± 96.1819 | 0.0049 ± 0.0001 | 0.0169 ± 0.0009 |
| 1.0 | 1039.0514 ± 73.2831 | 0.0097 ± 0.0006 | 0.0197 ± 0.0014 |
| 2.0 | 827.7142 ± 47.8910 | 0.0153 ± 0.0016 | 0.0238 ± 0.0022 |
| 4.0 | 710.8570 ± 33.0896 | 0.0200 ± 0.0030 | 0.0280 ± 0.0033 |

## E4 outage

| outage_h | architecture | fault events in outage | flagged at the station during outage | backlog (frames) | backfill (h) | cloud up to date after (h) |
|---|---|---|---|---|---|---|
| 6 | edge_store_forward | 2.60 ± 1.82 | 0.60 ± 0.89 | 24.64 ± 0.43 | 0.82 ± 0.01 | 6.82 ± 0.01 |
| 6 | centralised_raw | 2.60 ± 1.82 | 0.00 ± 0.00 | 24.00 ± 0.00 | 0.80 ± 0.00 | 6.80 ± 0.00 |
| 24 | edge_store_forward | 4.80 ± 2.17 | 2.80 ± 1.30 | 101.23 ± 1.16 | 3.37 ± 0.04 | 27.37 ± 0.04 |
| 24 | centralised_raw | 4.80 ± 2.17 | 0.00 ± 0.00 | 96.01 ± 0.03 | 3.20 ± 0.00 | 27.20 ± 0.00 |

## E5 training paradigms (pressure loggers)

| paradigm | one-step RMSE (m) | day-ahead RMSE (m) | cold start one-step (m) | cold start day-ahead (m) | bytes up | bytes down |
|---|---|---|---|---|---|---|
| local | 0.642 ± 0.060 | 6.848 ± 1.160 | 2.220 ± 0.320 | 5.838 ± 1.864 | 0.000 ± 0.000 | 0.000 ± 0.000 |
| centralised | 0.654 ± 0.050 | 5.237 ± 1.188 | 1.705 ± 0.091 | 24.872 ± 17.610 | 828630.000 ± 0.000 | 29592.000 ± 0.000 |
| federated | 0.695 ± 0.036 | 3.495 ± 0.361 | 2.136 ± 0.141 | 10.284 ± 0.609 | 295920.000 ± 0.000 | 295920.000 ± 0.000 |
| federated_finetuned | 0.650 ± 0.039 | 5.665 ± 2.511 | 2.102 ± 0.150 | 8.216 ± 0.846 | 295920.000 ± 0.000 | 295920.000 ± 0.000 |

## E6 twin state estimation

| input | RMSE monitored (m) | RMSE unmonitored (m) | 95th pct |error| (m) | degraded steps | resyncs |
|---|---|---|---|---|---|
| raw | 1.03 ± 0.46 | 0.92 ± 0.63 | 0.82 ± 0.52 | 0.06 ± 0.06 | 58.00 ± 32.95 |
| edge_validated | 0.86 ± 0.06 | 0.67 ± 0.16 | 0.74 ± 0.34 | 0.00 ± 0.01 | 51.80 ± 39.33 |
| hybrid_validated | 1.12 ± 0.40 | 0.64 ± 0.19 | 0.72 ± 0.22 | 0.00 ± 0.00 | 50.40 ± 45.91 |
| clean | 0.59 ± 0.07 | 0.47 ± 0.14 | 0.37 ± 0.11 | 0.00 ± 0.00 | 36.20 ± 42.10 |

## E7 process events versus sensor faults

| architecture | process events raised | event samples reconstructed | events reconstructed away | faults raised as events |
|---|---|---|---|---|
| edge_only | 0.950 ± 0.112 | 0.010 ± 0.008 | 0.000 ± 0.000 | 0.424 ± 0.100 |
| central_only | 0.500 ± 0.306 | 0.016 ± 0.017 | 0.000 ± 0.000 | 0.518 ± 0.238 |
| hybrid | 0.850 ± 0.137 | 0.011 ± 0.010 | 0.000 ± 0.000 | 0.537 ± 0.215 |

## Maintenance log (spatial alarms, test week)

| spatial false alarms, no log | with log | fault alarms suppressed |
|---|---|---|
| 1134.0 ± 871.2 | 1046.4 ± 827.0 | 5.8 ± 13.0 |

## External data (day-ahead total demand)

| demand MAE without temperature (L/s) | with temperature (L/s) | mean demand (L/s) |
|---|---|---|
| 25.4 ± 9.8 | 25.3 ± 8.3 | 693.1 ± 27.2 |

## E8a conformal residual thresholds: nominal level against empirical rate

| buckets | alpha | period | residual-test rejections on clean samples | station false-alarm rate (all tests) | sensors x buckets with no finite threshold |
|---|---|---|---|---|---|
| 1 | 0.01 | calib | 0.0073 ± 0.0008 | 0.0086 ± 0.0013 | 0.0000 ± 0.0000 |
| 1 | 0.01 | test | 0.0065 ± 0.0012 | 0.0075 ± 0.0012 | 0.0000 ± 0.0000 |
| 1 | 0.005 | calib | 0.0040 ± 0.0002 | 0.0055 ± 0.0010 | 0.0000 ± 0.0000 |
| 1 | 0.005 | test | 0.0037 ± 0.0011 | 0.0051 ± 0.0011 | 0.0000 ± 0.0000 |
| 1 | 0.001 | calib | 0.0004 ± 0.0001 | 0.0028 ± 0.0014 | 8.0000 ± 0.0000 |
| 1 | 0.001 | test | 0.0007 ± 0.0003 | 0.0025 ± 0.0006 | 8.0000 ± 0.0000 |
| 4 | 0.01 | calib | 0.0038 ± 0.0005 | 0.0054 ± 0.0010 | 1.4000 ± 1.3416 |
| 4 | 0.01 | test | 0.0040 ± 0.0008 | 0.0053 ± 0.0010 | 1.4000 ± 1.3416 |
| 4 | 0.005 | calib | 0.0012 ± 0.0001 | 0.0036 ± 0.0015 | 32.0000 ± 0.0000 |
| 4 | 0.005 | test | 0.0017 ± 0.0004 | 0.0034 ± 0.0007 | 32.0000 ± 0.0000 |
| 4 | 0.001 | calib | 0.0000 ± 0.0000 | 0.0024 ± 0.0015 | 100.0000 ± 0.0000 |
| 4 | 0.001 | test | 0.0000 ± 0.0000 | 0.0018 ± 0.0006 | 100.0000 ± 0.0000 |

## E8b reconstruction intervals (nominal coverage 0.90), pressure

| gap_min | method | MAE (m) | coverage | mean width (m) |
|---|---|---|---|---|
| 15 | holt_winters | 0.708 ± 1.009 | 0.850 ± 0.224 | 1.230 ± 0.682 |
| 15 | hybrid_crossover | 0.722 ± 0.999 | 0.850 ± 0.224 | 1.626 ± 1.063 |
| 15 | hybrid_width | 0.708 ± 1.009 | 0.850 ± 0.224 | 1.230 ± 0.682 |
| 15 | spatial | 0.580 ± 0.833 | 0.983 ± 0.037 | 6.826 ± 5.837 |
| 60 | holt_winters | 0.193 ± 0.143 | 0.971 ± 0.035 | 2.010 ± 0.670 |
| 60 | hybrid_crossover | 0.144 ± 0.167 | 0.996 ± 0.009 | 2.839 ± 0.389 |
| 60 | hybrid_width | 0.193 ± 0.143 | 0.971 ± 0.035 | 2.010 ± 0.670 |
| 60 | spatial | 0.222 ± 0.236 | 0.996 ± 0.009 | 4.322 ± 1.766 |
| 180 | holt_winters | 0.611 ± 0.483 | 0.954 ± 0.068 | 4.150 ± 1.909 |
| 180 | hybrid_crossover | 0.226 ± 0.261 | 0.969 ± 0.068 | 5.214 ± 3.124 |
| 180 | hybrid_width | 0.622 ± 0.468 | 0.954 ± 0.068 | 3.926 ± 1.886 |
| 180 | spatial | 0.218 ± 0.243 | 0.950 ± 0.112 | 5.532 ± 2.866 |
| 360 | holt_winters | 0.852 ± 0.574 | 0.847 ± 0.193 | 4.874 ± 1.799 |
| 360 | hybrid_crossover | 0.434 ± 0.352 | 0.908 ± 0.120 | 4.681 ± 1.548 |
| 360 | hybrid_width | 0.651 ± 0.293 | 0.850 ± 0.114 | 3.844 ± 1.318 |
| 360 | spatial | 0.434 ± 0.352 | 0.908 ± 0.120 | 4.681 ± 1.548 |
| 720 | holt_winters | 1.058 ± 0.840 | 0.890 ± 0.138 | 6.368 ± 2.332 |
| 720 | hybrid_crossover | 0.351 ± 0.343 | 0.957 ± 0.071 | 5.056 ± 1.481 |
| 720 | hybrid_width | 0.638 ± 0.438 | 0.920 ± 0.060 | 4.439 ± 1.335 |
| 720 | spatial | 0.351 ± 0.343 | 0.957 ± 0.071 | 5.056 ± 1.481 |
| 1440 | holt_winters | 0.773 ± 0.442 | 0.970 ± 0.031 | 6.358 ± 2.880 |
| 1440 | hybrid_crossover | 0.342 ± 0.364 | 0.951 ± 0.079 | 4.699 ± 3.496 |
| 1440 | hybrid_width | 0.438 ± 0.333 | 0.948 ± 0.070 | 4.302 ± 3.012 |
| 1440 | spatial | 0.342 ± 0.364 | 0.951 ± 0.079 | 4.699 ± 3.496 |

### E8b coverage over all sensor kinds

| method | coverage, all kinds |
|---|---|
| holt_winters | 0.850 ± 0.053 |
| hybrid_crossover | 0.872 ± 0.039 |
| hybrid_width | 0.847 ± 0.028 |
| spatial | 0.886 ± 0.065 |

## E8c station ablation (test week)

| variant | F1 | false-alarm rate | spike recall | stuck recall | bias recall | drift recall |
|---|---|---|---|---|---|---|
| v0.1 station | 0.153 ± 0.014 | 0.005 ± 0.001 | 0.825 ± 0.143 | 0.805 ± 0.027 | 0.005 ± 0.004 | 0.014 ± 0.021 |
| conformal thresholds only | 0.153 ± 0.010 | 0.005 ± 0.001 | 0.875 ± 0.153 | 0.805 ± 0.027 | 0.004 ± 0.003 | 0.015 ± 0.020 |
| conformal + CUSUM | 0.143 ± 0.008 | 0.012 ± 0.005 | 0.875 ± 0.153 | 0.817 ± 0.009 | 0.004 ± 0.003 | 0.015 ± 0.021 |
| conformal + cross-sensor model | 0.272 ± 0.116 | 0.008 ± 0.004 | 0.875 ± 0.153 | 0.812 ± 0.030 | 0.098 ± 0.135 | 0.119 ± 0.102 |
| v0.2 station (all three) | 0.248 ± 0.099 | 0.015 ± 0.005 | 0.875 ± 0.153 | 0.823 ± 0.009 | 0.098 ± 0.135 | 0.113 ± 0.100 |

### E8 cross-sensor models per station

| sensor_id | share of seeds MLP chosen | MLP RMSE reduction vs ridge (week 7) | share of seeds with a finite threshold |
|---|---|---|---|
| F-10 | 1.00 ± 0.00 | 0.20 ± 0.02 | 1.00 ± 0.00 |
| P-10 | 1.00 ± 0.00 | 0.11 ± 0.02 | 1.00 ± 0.00 |
| F-60 | 1.00 ± 0.00 | 0.09 ± 0.02 | 1.00 ± 0.00 |
| P-61 | 1.00 ± 0.00 | 0.06 ± 0.01 | 1.00 ± 0.00 |
| L-1 | 1.00 ± 0.00 | 0.11 ± 0.03 | 1.00 ± 0.00 |
| F-40 | 1.00 ± 0.00 | 0.14 ± 0.04 | 1.00 ± 0.00 |
| L-2 | 1.00 ± 0.00 | 0.11 ± 0.03 | 0.80 ± 0.45 |
| F-50 | 1.00 ± 0.00 | 0.12 ± 0.03 | 1.00 ± 0.00 |
| L-3 | 1.00 ± 0.00 | 0.10 ± 0.04 | 0.80 ± 0.45 |
| F-20 | 1.00 ± 0.00 | 0.20 ± 0.03 | 1.00 ± 0.00 |
| P-131 | 1.00 ± 0.00 | 0.19 ± 0.08 | 1.00 ± 0.00 |
| C-131 | 0.80 ± 0.45 | 0.02 ± 0.02 | 0.40 ± 0.55 |
| P-15 | 1.00 ± 0.00 | 0.17 ± 0.07 | 1.00 ± 0.00 |
| C-15 | 1.00 ± 0.00 | 0.17 ± 0.02 | 0.60 ± 0.55 |
| P-197 | 1.00 ± 0.00 | 0.17 ± 0.11 | 1.00 ± 0.00 |
| C-197 | 1.00 ± 0.00 | 0.08 ± 0.02 | 0.60 ± 0.55 |
| P-219 | 1.00 ± 0.00 | 0.10 ± 0.04 | 1.00 ± 0.00 |
| C-219 | 1.00 ± 0.00 | 0.07 ± 0.01 | 1.00 ± 0.00 |
| P-253 | 1.00 ± 0.00 | 0.19 ± 0.05 | 1.00 ± 0.00 |
| C-253 | 1.00 ± 0.00 | 0.21 ± 0.04 | 0.80 ± 0.45 |

### E8d int8 weights (mean RMSE over sensors, clean samples; physical units)

| model | period | RMSE float32 | RMSE int8 | relative change |
|---|---|---|---|---|
| cross_mlp | calib | 8.2147 ± 0.7351 | 8.2150 ± 0.7352 | 0.0001 ± 0.0012 |
| cross_mlp | test | 7.6845 ± 2.1928 | 7.6847 ± 2.1812 | 0.0003 ± 0.0033 |
| forecast_mlp | calib | 0.5799 ± 0.0474 | 0.5804 ± 0.0470 | 0.0008 ± 0.0015 |
| forecast_mlp | test | 0.6388 ± 0.0631 | 0.6393 ± 0.0633 | 0.0006 ± 0.0012 |

## E9 leak localisation (test week)

Operating variant chosen on week 7 (edge-validated input, synthetic cases): baseline `none`, ranking by angle. 92 candidate junctions; 21 cases per seed.

| input | median rank | median distance (m) | top-5 share | top-10 share |
|---|---|---|---|---|
| raw | 16.20 ± 13.59 | 2534.16 ± 2110.80 | 0.34 ± 0.19 | 0.44 ± 0.20 |
| edge_validated | 15.60 ± 12.74 | 2413.71 ± 2056.17 | 0.34 ± 0.18 | 0.48 ± 0.23 |
| hybrid_validated | 15.00 ± 13.17 | 2367.69 ± 1994.10 | 0.36 ± 0.18 | 0.49 ± 0.23 |
| clean | 3.60 ± 0.55 | 517.86 ± 96.15 | 0.63 ± 0.07 | 0.82 ± 0.02 |

### E9 by leak size (synthetic cases)

| input | leak area (m2) | median rank | median distance (m) | top-5 share |
|---|---|---|---|---|
| raw | 0.0005 | 17.40 ± 13.39 | 3546.35 ± 2427.07 | 0.29 ± 0.23 |
| edge_validated | 0.0005 | 20.20 ± 12.11 | 3368.96 ± 2137.80 | 0.26 ± 0.16 |
| hybrid_validated | 0.0005 | 16.80 ± 13.37 | 3252.52 ± 2124.73 | 0.29 ± 0.17 |
| clean | 0.0005 | 6.60 ± 2.51 | 1106.12 ± 477.12 | 0.46 ± 0.06 |
| raw | 0.001 | 19.60 ± 13.05 | 2838.65 ± 3312.15 | 0.29 ± 0.30 |
| edge_validated | 0.001 | 17.00 ± 13.13 | 2731.67 ± 3366.90 | 0.31 ± 0.31 |
| hybrid_validated | 0.001 | 17.00 ± 13.13 | 2750.88 ± 3350.99 | 0.31 ± 0.31 |
| clean | 0.001 | 3.20 ± 1.79 | 444.76 ± 155.13 | 0.69 ± 0.16 |
| raw | 0.0015 | 15.80 ± 16.13 | 1955.44 ± 1326.12 | 0.47 ± 0.25 |
| edge_validated | 0.0015 | 15.70 ± 17.49 | 1903.47 ± 1323.52 | 0.47 ± 0.22 |
| hybrid_validated | 0.0015 | 15.40 ± 17.69 | 1876.80 ± 1333.42 | 0.50 ± 0.26 |
| clean | 0.0015 | 4.10 ± 2.68 | 774.83 ± 361.80 | 0.73 ± 0.25 |

### E9 the testbed's own leak (one case per seed)

| input | rank | distance (m) |
|---|---|---|
| raw | 13.2 ± 10.7 | 3223.7 ± 3840.6 |
| edge_validated | 12.4 ± 10.8 | 3223.7 ± 3840.6 |
| hybrid_validated | 10.6 ± 9.9 | 3011.6 ± 3950.7 |
| clean | 3.0 ± 2.5 | 1121.5 ± 1220.2 |

### E9 all variants (test week)

| input | baseline | method | median rank | median distance (m) | top-5 share | top-10 share |
|---|---|---|---|---|---|---|
| raw | none | correlation | 19.00 ± 15.73 | 3327.81 ± 2777.09 | 0.39 ± 0.19 | 0.47 ± 0.28 |
| raw | none | angle | 16.20 ± 13.59 | 2534.16 ± 2110.80 | 0.34 ± 0.19 | 0.44 ± 0.20 |
| raw | day1 | correlation | 30.60 ± 12.60 | 4131.57 ± 2266.13 | 0.19 ± 0.09 | 0.24 ± 0.13 |
| raw | day1 | angle | 26.40 ± 6.23 | 4749.99 ± 1929.27 | 0.20 ± 0.07 | 0.28 ± 0.10 |
| raw | day3 | correlation | 29.40 ± 11.59 | 5000.30 ± 1573.92 | 0.15 ± 0.05 | 0.25 ± 0.09 |
| raw | day3 | angle | 25.20 ± 8.38 | 4521.09 ± 1497.36 | 0.19 ± 0.09 | 0.26 ± 0.10 |
| edge_validated | none | correlation | 18.80 ± 13.95 | 3586.57 ± 2459.86 | 0.32 ± 0.16 | 0.42 ± 0.19 |
| edge_validated | none | angle | 15.60 ± 12.74 | 2413.71 ± 2056.17 | 0.34 ± 0.18 | 0.48 ± 0.23 |
| edge_validated | day1 | correlation | 27.80 ± 14.13 | 4218.13 ± 2076.54 | 0.18 ± 0.08 | 0.24 ± 0.10 |
| edge_validated | day1 | angle | 24.80 ± 9.63 | 4280.28 ± 2062.44 | 0.22 ± 0.10 | 0.28 ± 0.11 |
| edge_validated | day3 | correlation | 27.80 ± 13.44 | 5229.51 ± 2110.77 | 0.14 ± 0.09 | 0.24 ± 0.09 |
| edge_validated | day3 | angle | 23.60 ± 10.69 | 4484.82 ± 1060.58 | 0.16 ± 0.12 | 0.24 ± 0.12 |
| hybrid_validated | none | correlation | 18.20 ± 14.27 | 3110.47 ± 2292.91 | 0.34 ± 0.14 | 0.43 ± 0.18 |
| hybrid_validated | none | angle | 15.00 ± 13.17 | 2367.69 ± 1994.10 | 0.36 ± 0.18 | 0.49 ± 0.23 |
| hybrid_validated | day1 | correlation | 25.80 ± 16.38 | 4078.83 ± 2077.65 | 0.20 ± 0.11 | 0.30 ± 0.16 |
| hybrid_validated | day1 | angle | 19.00 ± 11.58 | 3904.76 ± 2378.66 | 0.25 ± 0.14 | 0.35 ± 0.20 |
| hybrid_validated | day3 | correlation | 27.80 ± 15.27 | 4376.98 ± 2273.34 | 0.17 ± 0.17 | 0.29 ± 0.17 |
| hybrid_validated | day3 | angle | 21.80 ± 11.82 | 4200.80 ± 1932.17 | 0.20 ± 0.13 | 0.30 ± 0.19 |
| clean | none | correlation | 3.20 ± 0.45 | 518.76 ± 113.13 | 0.70 ± 0.07 | 0.82 ± 0.09 |
| clean | none | angle | 3.60 ± 0.55 | 517.86 ± 96.15 | 0.63 ± 0.07 | 0.82 ± 0.02 |
| clean | day1 | correlation | 8.40 ± 4.67 | 1316.43 ± 749.61 | 0.47 ± 0.13 | 0.61 ± 0.13 |
| clean | day1 | angle | 8.20 ± 3.03 | 1043.32 ± 360.71 | 0.42 ± 0.07 | 0.60 ± 0.10 |
| clean | day3 | correlation | 8.00 ± 4.80 | 1334.72 ± 635.17 | 0.49 ± 0.13 | 0.60 ± 0.17 |
| clean | day3 | angle | 7.00 ± 2.35 | 1146.41 ± 351.44 | 0.48 ± 0.06 | 0.64 ± 0.10 |

### E9 variants on week 7 (edge-validated, used to choose the operating variant)

| baseline | method | median rank | median distance (m) | top-5 share | top-10 share |
|---|---|---|---|---|---|
| none | correlation | 12.60 ± 6.83 | 2812.69 ± 1785.07 | 0.31 ± 0.20 | 0.48 ± 0.19 |
| none | angle | 13.10 ± 9.17 | 2202.73 ± 1586.82 | 0.37 ± 0.21 | 0.53 ± 0.20 |
| day1 | correlation | 20.70 ± 5.99 | 3262.30 ± 1136.63 | 0.21 ± 0.08 | 0.35 ± 0.08 |
| day1 | angle | 21.00 ± 7.59 | 3606.39 ± 1477.03 | 0.21 ± 0.13 | 0.37 ± 0.15 |
| day3 | correlation | 24.20 ± 4.22 | 3256.23 ± 1308.09 | 0.18 ± 0.08 | 0.28 ± 0.10 |
| day3 | angle | 27.60 ± 12.01 | 3780.37 ± 1545.23 | 0.17 ± 0.08 | 0.24 ± 0.07 |

## Station footprint (P1.5)

Measured with the MicroPython Unix port on a laptop (gc.mem_alloc after a collection), one day of calibration-week samples per station. The SRAM figure is the ESP32's internal SRAM from Espressif's ESP32 Series Datasheet v5.3; this is a comparison of numbers, not a measurement on an ESP32, where MicroPython's heap is only part of that SRAM.

| station | version | package (B) | live heap (B) | share of 520 KB SRAM | most allocated in one sample (B) | µs/sample MicroPython |
|---|---|---|---|---|---|---|
| PS-10 (pump) | v0.1 | 91964 | 22080 | 4.1% | 2560 | 33.0 |
| PS-10 (pump) | v0.2 float32 | 95987 | 25568 | 4.8% | 20288 | 60.6 |
| PS-10 (pump) | v0.2 int8 | 94007 | 25248 | 4.7% | 16192 | 54.3 |
| TK-1 (tank) | v0.1 | 59770 | 16352 | 3.1% | 3264 | 28.4 |
| TK-1 (tank) | v0.2 float32 | 63805 | 19776 | 3.7% | 76256 | 56.5 |
| TK-1 (tank) | v0.2 int8 | 61804 | 19456 | 3.7% | 59872 | 56.0 |
| LG-131 (logger) | v0.1 | 63026 | 15904 | 3.0% | 2464 | 24.8 |
| LG-131 (logger) | v0.2 float32 | 65715 | 18720 | 3.5% | 20256 | 55.2 |
| LG-131 (logger) | v0.2 int8 | 64721 | 18560 | 3.5% | 16160 | 49.6 |
| LG-101 (logger) | v0.1 | 45639 | 11360 | 2.1% | 2464 | 21.1 |
| LG-101 (logger) | v0.2 float32 | 45688 | 11552 | 2.2% | 2624 | 22.4 |
| LG-101 (logger) | v0.2 int8 | 45688 | 11552 | 2.2% | 2624 | 41.7 |

## Edge footprint

Measured on a laptop (CPython 3.12 and the MicroPython Unix port) for one pressure sensor; these are not ESP32 timings.

| forecaster | parameters | model bytes | buffer (samples) | µs/sample CPython | µs/sample MicroPython |
|---|---|---|---|---|---|
| seasonal_naive | 0 | 8064 | 2028 | 2.9 | 14.5 |
| holt_winters | 4 | 9264 | 2316 | 3.1 | 19.8 |
| mlp | 609 | 2466 | 300 | 29.0 | 121.8 |
