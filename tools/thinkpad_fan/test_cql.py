import pytest

from tools.thinkpad_fan.cql import CQL_OFF, GET, clear_cql


class FakeDytc:
    def __init__(self, state=0x20037D01, *, stuck=False):
        self.state = state
        self.stuck = stuck
        self.commands = []

    def __call__(self, command):
        self.commands.append(command)
        if command == CQL_OFF and not self.stuck:
            self.state &= ~(1 << 17)
        return self.state


def test_clears_only_cql_with_psc7():
    device = FakeDytc()
    assert clear_cql(device) == 0x20017D01
    assert device.commands == [GET, CQL_OFF, GET]


def test_already_inactive_is_noop():
    device = FakeDytc(0x20017D01)
    assert clear_cql(device) == 0x20017D01
    assert device.commands == [GET]


@pytest.mark.parametrize('state', [0x20035D01, 0x00037D01, 0x20037D02, 0x20027D01])
def test_unexpected_state_does_not_write(state):
    device = FakeDytc(state)
    with pytest.raises(RuntimeError):
        clear_cql(device)
    assert device.commands == [GET]


def test_failed_readback_is_not_retried():
    device = FakeDytc(stuck=True)
    with pytest.raises(RuntimeError):
        clear_cql(device)
    assert device.commands == [GET, CQL_OFF, GET]


def test_transport_error_does_not_retry():
    commands = []

    def call(command):
        commands.append(command)
        if command == GET:
            return 0x20037D01
        raise OSError('transport failure')

    with pytest.raises(OSError):
        clear_cql(call)
    assert commands == [GET, CQL_OFF]
