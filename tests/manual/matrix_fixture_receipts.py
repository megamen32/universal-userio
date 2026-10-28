"""Bind a fixture phase to the native B receipt, including split utterances."""
from datetime import datetime
import re


def completed_native_turn(lines, call_id, remote_at):
    """Return the latest completed B turn heard after the real remote event.

    Input is the existing container's timestamped log, filtered by this exact
    private call. A new heard line invalidates the previous completion.
    """
    heard = re.compile(r"^\S+ \[agentcall\] call=" + re.escape(call_id) + r" heard: ")
    done = re.compile(r"^\S+ \[agentcall\] voice_turn call=" + re.escape(call_id) + r" turn=(\d+) mode=(secretary|incidents) ")
    pending = None
    result = None
    for line in lines:
        if not (heard.match(line) or done.match(line)):
            continue
        try:
            stamp = re.sub(r'(\.\d{6})\d+', r'\1', line.split()[0])
            at = datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp()
        except ValueError:
            continue
        if heard.match(line):
            pending = at
            result = None
        elif pending is not None and pending >= remote_at and at >= pending:
            match = done.match(line)
            result = {'turn_id': int(match[1]), 'mode': match[2],
                      'heard_at': pending, 'completed_at': at}
    return result
