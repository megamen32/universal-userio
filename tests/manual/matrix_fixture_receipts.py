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


def received_answer_started(sdk, received_asr):
    """Match actual received speech against the exact substantive SDK answer.

    The fixture supplies audio captured after its question, excluding greeting.
    Mere audio energy, acknowledgements and progress have no authority here.
    """
    if sdk.get('state') != 'completed' or not sdk.get('text'):
        return False
    words = lambda text: re.findall(r'[\w]+', text.lower().replace('ё', 'е'))
    expected = words(sdk['text']); heard = words(received_asr)
    # Four consecutive words distinguish speech content from generic wait text.
    phrases = {tuple(expected[i:i+4]) for i in range(len(expected)-3)}
    return any(tuple(heard[i:i+4]) in phrases for i in range(len(heard)-3))


def phase_result(sdk, *, native_mode, expected_mode=None, ack_state=None):
    """A known failed ACK is recorded; it never replays the terminal turn."""
    done = sdk.get('state') in {'completed', 'delegated'}
    done = done and (not expected_mode or sdk.get('mode') == native_mode == expected_mode)
    return {'completed':done, 'failed_ACK':bool(done and ack_state is not None and ack_state != 'acknowledged')}
