"""Fixed-size ring buffer of floats backed by ``array('f')`` (4 bytes per slot)."""

from array import array


class RingBuffer:
    def __init__(self, size, fill=0.0):
        self.size = size
        self.buf = array("f", [fill] * size)
        self.pos = 0
        self.count = 0

    def push(self, x):
        self.buf[self.pos] = x
        self.pos = (self.pos + 1) % self.size
        if self.count < self.size:
            self.count += 1

    def get(self, back):
        """Value pushed ``back`` steps ago (0 = most recent)."""
        return self.buf[(self.pos - 1 - back) % self.size]

    def full(self):
        return self.count == self.size

    def values(self):
        """Oldest-first copy of the stored values."""
        n = self.count
        return [self.get(n - 1 - i) for i in range(n)]

    def fill(self, x):
        for i in range(self.size):
            self.buf[i] = x
        self.count = self.size
