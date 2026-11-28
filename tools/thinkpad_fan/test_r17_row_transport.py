import pytest
from r17_row_transport import StrictTransport


class FakePort:
    def __init__(self):
        self.state = "idle"
        self.reg = {0x81: 96, 0xA0: 255}
        self.address = None
        self.command = None
        self.lock = False
        self.tick = 0
        self.writes = []
        self.fail = False

    def acquire(self):
        self.lock = True
        return True

    def release(self):
        self.lock = False

    def now(self):
        return self.tick

    def sleep(self):
        self.tick += 1

    def status(self):
        assert self.lock
        return 1 if self.state == "reply" else 0

    def command_write(self, v):
        assert self.lock and self.state == "idle"
        self.writes.append(v)
        self.command = v
        self.state = "address"

    def data_write(self, v):
        assert self.lock
        self.writes.append(v)
        if self.state == "address":
            self.address = v
            self.state = "reply" if self.command == 0x80 else "value"
        elif self.state == "value":
            self.reg[self.address] = v
            self.state = "idle"
        else:
            raise AssertionError("unexpected write")

    def data_read(self):
        assert self.state == "reply"
        if self.fail:
            raise OSError("lost read")
        self.state = "idle"
        return self.reg[self.address]


def test_strict_read_accepts_ff_and_restores_selector():
    p = FakePort()
    t = StrictTransport(p)
    assert t.acquire() and t.synchronized_idle()
    assert t.read(0xA0) == 255
    t.write_selector(0x56)
    assert t.read(0x81) == 0x56
    t.begin_restore()
    t.write_selector(96)
    assert t.read(0x81) == 96
    t.release()
    assert not p.lock


def test_pending_reply_is_not_drained_or_written():
    p = FakePort()
    p.state = "reply"
    t = StrictTransport(p)
    assert t.acquire()
    assert not t.synchronized_idle()
    with pytest.raises(OSError):
        t.read(0xA0)
    assert p.state == "reply" and p.writes == []


def test_ambiguous_driver_failure_forbids_next_transaction():
    p = FakePort()
    t = StrictTransport(p)
    assert t.acquire()
    p.fail = True
    with pytest.raises(OSError):
        t.read(0xA0)
    writes = p.writes.copy()
    assert not t.synchronized_idle()
    with pytest.raises(OSError):
        t.write_selector(96)
    assert p.writes == writes


def test_soft_budget_stops_new_data_reads_but_keeps_restoration():
    p = FakePort()
    t = StrictTransport(p)
    assert t.acquire()
    p.tick = 1000
    with pytest.raises(TimeoutError):
        t.read(0xA0)
    assert t.synchronized_idle() and p.writes == []
    t.begin_restore()
    assert t.read(0x81) == 96


def test_no_arbitrary_address_or_value():
    p = FakePort()
    t = StrictTransport(p)
    assert t.acquire()
    for a in (0x00, 0x84, 0xAA):
        with pytest.raises(ValueError):
            t.read(a)
    with pytest.raises(ValueError):
        t.write_selector(0x57)
    assert p.writes == []


def test_transport_instance_cannot_be_reopened_after_failure():
    p = FakePort()
    t = StrictTransport(p)
    assert t.acquire()
    p.fail = True
    with pytest.raises(OSError):
        t.read(0xa0)
    t.release()
    p.state = "idle"
    p.fail = False
    with pytest.raises(RuntimeError):
        t.acquire()
    assert not p.lock
