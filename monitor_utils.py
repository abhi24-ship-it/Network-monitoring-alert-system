"""Small, dependency-free helpers shared by the collectors and their tests."""
import json
import math


def decode_telemetry(data):
    """Return a validated report, or raise ValueError before it reaches state.

    Validate the fields consumed by either collector. Extra fields are allowed
    so clients can add measurements without changing the receiver.
    """
    try:
        msg = json.loads(data.decode('utf-8'))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError('invalid JSON telemetry') from exc
    if not isinstance(msg, dict):
        raise ValueError('telemetry must be a JSON object')
    for key in ('cpu_pct', 'tx_bps'):
        value = msg.get(key)
        try:
            valid = (type(value) in (int, float) and math.isfinite(value)
                     and value >= 0)
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError(f'{key} must be a finite nonnegative number')
    if msg['cpu_pct'] > 100:
        raise ValueError('cpu_pct must be between 0 and 100')
    seq = msg.get('seq')
    if type(seq) is not int or seq < 0:
        raise ValueError('seq must be a nonnegative integer')
    return msg


def flow_rate(previous, byte_count, age, now):
    """Bits/second from switch counters; previous is (bytes, age, monotonic time).

    Flow duration comes from the switch. A smaller counter/duration, or a flow
    younger than the time since our last reading, indicates a new lifetime.
    Use that lifetime instead of dividing by the old flow's idle gap.
    """
    if previous is None:
        return byte_count * 8 / max(age, 1e-3)
    old_bytes, old_age, old_time = previous
    recreated = (byte_count < old_bytes or age < old_age
                 or age + 0.05 < now - old_time)
    if recreated:
        return byte_count * 8 / max(age, 1e-3)
    elapsed = age - old_age
    if elapsed <= 0:
        return 0.0  # duplicate sample: no new measurement interval
    return (byte_count - old_bytes) * 8 / elapsed
