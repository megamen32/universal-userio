"""Connector reachability probes for the telegram-qr outbox tests.

The outbox proves a non-submission only when the connector was not accepting
connections, and it proves that in the connection phase, before any request
byte exists. A test that injects its own HTTP runner must also say what the
connector reachability looks like, otherwise the real probe would talk to a
port that no test owns.
"""


def reachable_probe(_base_url, _timeout):
    """Report a connector that is accepting connections."""
    return None


def refused_probe(_base_url, _timeout):
    """Report a connector that is not accepting connections."""
    raise ConnectionRefusedError(111, "Connection refused")
