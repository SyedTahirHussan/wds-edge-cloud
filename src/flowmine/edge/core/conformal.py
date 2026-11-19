"""Split conformal thresholds and intervals, as tables the station looks up.

The cloud computes the tables from calibration-week scores (``split_quantile``); the
station only reads them. For a nonconformity score s (here a residual divided by a
scale) and calibration scores s_1..s_n, the split conformal threshold at level alpha
is the ceil((n + 1)(1 - alpha))-th smallest s_i. If a new score is exchangeable with
the calibration scores, P(s > threshold) <= alpha. When n < 1/alpha - 1 the rank
exceeds n and the threshold is infinite; the table then stores ``INF`` and the test
never fires at that level.

A threshold table is a list with one value per time-of-day bucket (one entry = no
buckets). An interval table is a list of (horizon_s, q) pairs sorted by horizon: the
half-width of a reconstruction ``h`` seconds into a gap is q times the sensor scale,
with q from the first row whose horizon is at least h.
"""

INF = 1.0e30
DAY = 86400


def rank(n, alpha):
    """1-based rank of the conformal quantile among n calibration scores."""
    r = (n + 1) * (1.0 - alpha)
    k = int(r)
    if k < r:
        k += 1
    return k


def split_quantile(scores, alpha):
    """Conformal threshold for ``scores`` (any iterable of floats) at level alpha."""
    s = sorted(scores)
    n = len(s)
    k = rank(n, alpha)
    if n == 0 or k > n:
        return INF
    return s[k - 1]


def bucket(t, n_buckets):
    """Time-of-day bucket of time ``t`` (seconds since a midnight)."""
    if n_buckets <= 1:
        return 0
    return int((t % DAY) * n_buckets // DAY)


def threshold(table, t):
    return table[bucket(t, len(table))]


def halfwidth(table, horizon_s, scale):
    """Interval half-width at ``horizon_s`` into a gap; INF beyond the last row."""
    for h, q in table:
        if horizon_s <= h:
            return q * scale
    return INF
