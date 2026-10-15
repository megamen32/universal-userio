"""Run the configured UserIO processes inside one bounded backend container.

Each worker keeps its own identity, environment, cursor and delivery journal.
This module supervises processes; it never creates or retries provider actions.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request


STATUS = Path('/run/userio/processes.json')


def ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status == 200
    except urllib.error.HTTPError as error:
        return error.code in {401, 403}
    except (OSError, urllib.error.URLError):
        return False


def load_config(path: Path) -> dict:
    data = json.loads(path.read_text())
    workers = data.get('processes')
    if not isinstance(workers, list) or not workers:
        raise ValueError('backend process configuration is empty')
    names = [p.get('name') for p in workers]
    if len(set(names)) != len(names) or 'core' not in names:
        raise ValueError('unique process names and one core are required')
    for worker in workers:
        command = worker.get('command')
        if not isinstance(command, list) or not command or not all(isinstance(v, str) and v for v in command):
            raise ValueError('process command must be an argument list')
        if not isinstance(worker.get('environment', {}), dict):
            raise ValueError('invalid process environment')
    return data


class Supervisor:
    def __init__(self, config: dict, *, status: Path = STATUS):
        self.config, self.status = config, status
        self.children: dict[str, subprocess.Popen] = {}
        self.attempts = collections.defaultdict(collections.deque)
        self.stopping = False
        self.started = time.time()

    def start(self, spec: dict) -> None:
        name = spec['name']
        now = time.monotonic()
        recent = self.attempts[name]
        while recent and recent[0] < now - 300:
            recent.popleft()
        if len(recent) >= 5:
            raise RuntimeError(f'worker {name} repeatedly failed; stopping backend')
        recent.append(now)
        env = {str(k): str(v) for k, v in spec.get('environment', {}).items()}
        env.setdefault('PATH', '/usr/local/bin:/usr/bin:/bin')
        env['PYTHONUNBUFFERED'] = '1'
        command = spec['command']
        kwargs = {}
        if os.getuid() == 0 and spec.get('uid', 0):
            kwargs.update(user=spec['uid'], group=spec.get('gid', spec['uid']),
                          extra_groups=spec.get('groups', []))
        # A process group gives graceful stop a precise owned target. Cgroups
        # and the container's aggregate resource bounds remain unchanged.
        self.children[name] = subprocess.Popen(command, cwd=spec.get('cwd', '/opt/universal-userio'),
            env=env, start_new_session=True, **kwargs)
        print(json.dumps({'process': name, 'state': 'started'}), flush=True)

    def save(self) -> None:
        self.status.parent.mkdir(parents=True, exist_ok=True)
        rows = [{'name': name, 'pid': p.pid, 'running': p.poll() is None,
                 'starts': len(self.attempts[name])} for name, p in self.children.items()]
        temp = self.status.with_suffix('.tmp')
        temp.write_text(json.dumps({'started_at': self.started, 'stopping': self.stopping,
                                   'expected': len(self.config['processes']), 'processes': rows}))
        temp.replace(self.status)

    def stop(self, *_args) -> None:
        self.stopping = True

    def close(self) -> None:
        # Ingress/dispatchers stop first; the core writer remains until last.
        children = list(self.children.items())
        groups = ([p for n, p in children if n != 'core'], [p for n, p in children if n == 'core'])
        for group, grace in zip(groups, (25, 5)):
            for child in group:
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGTERM)
            deadline = time.monotonic() + grace
            for child in group:
                try:
                    child.wait(timeout=max(.1, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=3)
        self.save()

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        core = next(p for p in self.config['processes'] if p['name'] == 'core')
        self.start(core)
        deadline = time.monotonic() + 60
        try:
            while not self.stopping and not ready(self.config['ready_url']):
                if self.children['core'].poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('core did not become ready')
                time.sleep(.25)
            if not self.stopping:
                for spec in self.config['processes']:
                    if spec['name'] != 'core':
                        self.start(spec)
            while not self.stopping:
                for spec in self.config['processes']:
                    child = self.children.get(spec['name'])
                    if child is not None and child.poll() is not None:
                        time.sleep(3)
                        if not self.stopping:
                            self.start(spec)
                self.save()
                time.sleep(1)
        finally:
            self.stopping = True
            self.close()


def health() -> int:
    try:
        state = json.loads(STATUS.read_text())
        if state['stopping'] or len(state['processes']) != state['expected']:
            return 1
        for worker in state['processes']:
            if not worker['running']:
                return 1
            os.kill(worker['pid'], 0)
        return 0 if ready('http://127.0.0.1:18093/v1/accounts') else 1
    except (OSError, ValueError, KeyError):
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=Path('/etc/userio-containers/processes.json'))
    parser.add_argument('--health', action='store_true')
    args = parser.parse_args()
    if args.health:
        return health()
    Supervisor(load_config(args.config)).run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
