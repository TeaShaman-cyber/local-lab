"""Strict, bounded transaction adapter for the R17 diagnostic row.

Only the known read offsets and the two documented-by-replay selector values
are accepted. The port backend is supplied by a single-owner diagnostic harness.
A partial transaction permanently inhibits this instance, even if a later
status happens to look idle. No drain, retry, layout discovery or fan command.
"""

READ_OFFSETS = frozenset((0x2F, 0xC8, 0xCB, 0x81, *range(0xA0, 0xAA)))


class StrictTransport:
    def __init__(self, port):
        self.port = port
        self.locked = False
        self.certain = False
        self.until = 0
        self.restoring = False
        self.used = False

    def acquire(self):
        if self.used:
            raise RuntimeError("one-shot transport already used")
        self.used = True
        if not self.port.acquire():
            return False
        self.locked = True
        self.certain = True
        self.until = self.port.now() + 900
        return True

    def release(self):
        self.port.release()
        self.locked = False

    def synchronized_idle(self):
        if not self.locked or not self.certain:
            return False
        try:
            if self.port.status() & 3:
                self.certain = False
        except Exception:
            self.certain = False
        return self.certain

    def begin_restore(self):
        if self.restoring:
            raise RuntimeError("already restoring")
        self.restoring = True
        self.until = self.port.now() + 600

    def start(self):
        if not self.synchronized_idle():
            raise OSError("unknown or busy mailbox")
        # The budget only admits new transactions, never abandons one midway
        # because of the overall deadline. Each step has a bounded wait.
        if self.port.now() >= self.until:
            raise TimeoutError("diagnostic budget exhausted")
        self.certain = False
        return self.port.now() + 100

    def wait(self, mask, expected, deadline):
        while True:
            status = self.port.status()
            if status & mask == expected:
                return
            if self.port.now() >= deadline:
                raise TimeoutError("mailbox handshake")
            self.port.sleep()

    def address(self, command, address, deadline):
        self.port.command_write(command)
        self.wait(2, 0, deadline)
        self.port.data_write(address)
        self.wait(2, 0, deadline)

    def read(self, address):
        if address not in READ_OFFSETS:
            raise ValueError("outside R17 observation scope")
        deadline = self.start()
        self.address(0x80, address, deadline)
        self.wait(1, 1, deadline)
        value = self.port.data_read()
        self.wait(3, 0, deadline)
        self.certain = True
        return value

    def write_selector(self, value):
        if value not in (0x56, 96):
            raise ValueError("outside R17 observation scope")
        deadline = self.start()
        self.address(0x81, 0x81, deadline)
        self.port.data_write(value)
        self.wait(3, 0, deadline)
        self.certain = True
