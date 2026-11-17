"""Send-on-delta with dual prediction.

Edge and cloud run identical copies of a forecaster. At every slot the edge compares
the validated value with the shared prediction and transmits only when they differ
by more than ``eps`` (physical units), when the heartbeat interval has passed, or
when the sample carries a quality flag. Without a transmission both sides feed the
prediction itself into their copy, so the copies stay identical and the cloud's
value is within ``eps`` of the edge's validated value at every slot (within half a
resolution step on slots that were transmitted, because the frame quantises values).
"""


class DeltaSender:
    def __init__(self, model, eps, heartbeat_s, scale=0.0):
        self.model = model
        self.eps = eps
        self.scale = scale
        self.heartbeat = heartbeat_s
        self.last_tx = None
        self.sent = 0
        self.slots = 0

    def offer(self, t, v, force=False):
        """Return ``(transmit, value_cloud_will_hold)`` for validated value ``v``."""
        self.slots += 1
        pred = self.model.forecast_at(t) if self.model.k is not None else None
        if v is None:
            if pred is not None:
                self.model.update(pred, t)
            return False, pred
        send = (
            force
            or pred is None
            or self.last_tx is None
            or abs(v - pred) > self.eps
            or t - self.last_tx >= self.heartbeat
        )
        if send:
            if self.scale > 0.0:
                v = round(v / self.scale) * self.scale
            self.model.update(v, t)
            self.last_tx = t
            self.sent += 1
            return True, v
        self.model.update(pred, t)
        return False, pred


class DeltaReceiver:
    def __init__(self, model):
        self.model = model

    def step(self, t, received):
        """Advance one slot; ``received`` is the transmitted value or None."""
        if received is not None:
            self.model.update(received, t)
            return received
        if self.model.k is None:
            return None
        pred = self.model.forecast_at(t)
        self.model.update(pred, t)
        return pred
