#!/usr/bin/env python3
"""Prepare an incoming AudioSocket call without originating another call."""
import json
import socket
import sys


def prepare(caller, socket_path='/run/agentcall/incoming.sock'):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(8)
        connection.connect(socket_path)
        connection.sendall((json.dumps({'action':'prepare_incoming','caller_number':caller})+'\n').encode())
        with connection.makefile('rb') as stream:
            result = json.loads(stream.readline(65537))
    if not result.get('ok'):
        raise RuntimeError('Incoming call was not prepared')
    return result['call_id']


def main():
    environment = {}
    for _ in range(100):
        line = sys.stdin.readline().strip()
        if not line:
            break
        key, _, value = line.partition(':')
        environment[key] = value.strip()
    try:
        call_id = prepare(environment.get('agi_callerid', ''))
        for key,value in [('AGENTCALL_ID',call_id),('AGENTCALL_ALLOWED','1')]:
            print(f'SET VARIABLE {key} "{value}"', flush=True)
            sys.stdin.readline()
    except Exception as error:
        print(f'agentcall incoming preparation: {type(error).__name__}', file=sys.stderr)


if __name__ == '__main__':
    main()
