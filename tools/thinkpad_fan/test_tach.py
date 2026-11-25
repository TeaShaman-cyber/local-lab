import struct

import pytest

from tools.thinkpad_fan.tach import decode, plateau


def frame(seq, rpm, mode=3, level=5, cmd=1, ack=1):
    return struct.pack("<10i", seq, cmd, ack, mode, 0, level,
                       rpm & 255, rpm >> 8, 0, 0)


def rows(rpms, level=5):
    return [(float(i), decode(frame(i+1, rpm, level=level)))
            for i, rpm in enumerate(rpms)]


def test_decode_raw_unsigned_tach():
    assert decode(frame(1, 44718)).rpm == 44718


def test_isolated_corruption_is_not_substituted():
    result = plateau(rows([4772, 4780, 44718, 4782, 4778, 4780, 4781, 4784, 4779, 4783]), 5)
    assert result.rpm == 4780
    assert result.accepted == 9
    assert result.rejected == 1


@pytest.mark.parametrize("rpms", [
    [4780]*4, [44718]*10, [4780, 44718]*5,
    list(range(4700, 3700, -100)), [4780]*5+[4000]*5,
    [4780]*8+[44718]*2,
])
def test_no_plateau_without_enough_stable_recent_data(rpms):
    with pytest.raises(ValueError):
        plateau(rows(rpms), 5)


def test_duplicate_or_restarted_sequence_rejected():
    samples = rows([4780]*10)
    samples[6] = (6.0, samples[5][1])
    with pytest.raises(ValueError):
        plateau(samples, 5)


def test_level_change_or_pending_command_rejected():
    samples = rows([4780]*10)
    samples[4] = (4.0, decode(frame(5, 4780, level=4)))
    with pytest.raises(ValueError):
        plateau(samples, 5)
    samples[4] = (4.0, decode(frame(5, 4780, cmd=2, ack=1)))
    with pytest.raises(ValueError):
        plateau(samples, 5)


def test_stale_or_too_short_window_rejected():
    samples = rows([4780]*10)
    with pytest.raises(ValueError):
        plateau([(t/10, s) for t,s in samples], 5)
    with pytest.raises(ValueError):
        plateau([(t*4, s) for t,s in samples], 5)


def test_new_command_even_same_level_rejected():
    samples = rows([4780]*10)
    samples[-1] = (9.0, decode(frame(10, 4780, cmd=2, ack=2)))
    with pytest.raises(ValueError):
        plateau(samples, 5)
