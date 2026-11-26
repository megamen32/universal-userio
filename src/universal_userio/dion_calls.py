"""Durable native DION WebRTC call lifecycle for UserIO.

The Go helper owns only one live WebRTC process. Authentication and refresh
remain owned by DionAuthBroker; only a fresh access token crosses the private
stdin pipe.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

from .account_connections import ConnectorSecrets
from .dion_auth import DionAuthBroker, DionAuthError, DionReauthRequired

ROOT = Path("/var/lib/universal-userio/dion-calls")
HELPER = os.environ.get("USERIO_DION_CALL_HELPER", "/opt/userio-dion-call-helper")


class DionCalls:
    def __init__(self, service, root=None, helper_path=None):
        self.service = service
        self.root = Path(root or ROOT)
        self.helper_path = str(helper_path or HELPER)
        self.lock = threading.Lock()
        self.active: dict[str, dict] = {}

    def _save(self, record):
        folder = self.root / record["call_id"]
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = folder / (".call-" + uuid.uuid4().hex)
        with temporary.open("x") as output:
            os.chmod(temporary, 0o600)
            json.dump(record, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, folder / "call.json")

    def get(self, call_id, user_id):
        call_id = str(uuid.UUID(str(call_id)))
        try:
            record = json.loads((self.root / call_id / "call.json").read_text())
        except FileNotFoundError:
            raise KeyError("call not found") from None
        if record.get("user_id") != user_id:
            raise KeyError("call not found")
        if record.get("status") in {"accepted", "active"} and call_id not in self.active:
            record["status"] = "unknown"
        return record

    def start(
        self, *, user_id, account_ref, request_id, confirm=False,
        room_id="", max_seconds=600,
    ):
        if confirm is not True:
            raise ValueError("explicit call confirmation required")
        if not self.service._store.capability_enabled("send", user_id=user_id):
            raise PermissionError("send capability disabled")
        if not (16 <= len(str(request_id)) <= 128):
            raise ValueError("invalid DION call request_id")
        if type(max_seconds) is not int or not 30 <= max_seconds <= 3600:
            raise ValueError("invalid DION call duration")
        account = next(
            (a for a in self.service._store.accounts(user_id=user_id)
             if a["id"] == account_ref and a["provider"] == "dion" and a["enabled"]),
            None,
        )
        if account is None:
            raise KeyError("DION account not found")

        action = "join" if str(room_id).strip() else "create"
        config = {
            "user_id": user_id,
            "account_ref": account_ref,
            "action": action,
            "room_id": str(room_id).strip(),
            "max_seconds": max_seconds,
        }
        call_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            "userio:dion:" + user_id + ":" + str(request_id),
        ))
        fingerprint = hashlib.sha256(
            json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

        with self.lock:
            path = self.root / call_id / "call.json"
            if path.exists():
                previous = self.get(call_id, user_id)
                if previous.get("fingerprint") != fingerprint:
                    raise ValueError("call request conflict")
                return previous
            if self.active:
                raise RuntimeError("another DION call is active")
            record = {
                **config,
                "call_id": call_id,
                "request_id": str(request_id),
                "fingerprint": fingerprint,
                "status": "accepted",
                "created_at": time.time(),
            }
            self._save(record)
            stop = threading.Event()
            slot = {"stop": stop, "process": None, "io_lock": threading.Lock()}
            self.active[call_id] = slot
            threading.Thread(
                target=self._run, args=(record, slot), daemon=True,
                name="userio-dion-call",
            ).start()
            return record.copy()

    def stop(self, call_id, user_id):
        record = self.get(call_id, user_id)
        with self.lock:
            slot = self.active.get(record["call_id"])
            if slot:
                slot["stop"].set()
        return record

    def _write_read(self, process, payload, *, io_lock):
        if process.stdin is None or process.stdout is None:
            raise RuntimeError("DION helper pipes are unavailable")
        with io_lock:
            process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
            process.stdin.flush()
            line = process.stdout.readline()
        if not line:
            raise RuntimeError("DION helper ended without receipt")
        result = json.loads(line)
        if not isinstance(result, dict) or result.get("request_id") != payload["request_id"]:
            raise RuntimeError("DION helper receipt mismatch")
        return result

    def _run(self, record, slot):
        process = None
        try:
            vault = ConnectorSecrets()
            broker = DionAuthBroker(
                self.service, record["user_id"], vault=vault,
            )
            secret = broker.load(record["account_ref"])
            access = broker.access_token(record["account_ref"])
            helper_request = {
                "action": record["action"],
                "account_ref": record["account_ref"],
                "user_id": str(secret["provider_user_id"]),
                "org_id": str(secret["organization_id"]),
                "access_token": access,
                "room_id": record["room_id"],
                "request_id": record["call_id"],
            }
            process = subprocess.Popen(
                [self.helper_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                close_fds=True,
            )
            slot["process"] = process
            result = self._write_read(
                process, helper_request, io_lock=slot["io_lock"],
            )
            # Only explicit pre-admission auth rejection is retryable. If a
            # room was created, resume that room instead of creating a second.
            if result.get("status") == "auth_expired" and not slot["stop"].is_set():
                if result.get("room_id"):
                    record["room_id"] = str(result["room_id"])
                    record["room_slug"] = str(result.get("room_slug") or "")
                    self._save(record)
                    if not record["room_slug"]:
                        raise RuntimeError("DION created room has no resumable slug")
                    helper_request["action"] = "join"
                    helper_request["room_id"] = record["room_slug"]
                fresh = broker.refresh(record["account_ref"], rejected_access_token=access)
                helper_request["access_token"] = str(fresh["access_token"])
                helper_request["request_id"] = record["call_id"] + "-auth-retry"
                result = self._write_read(process, helper_request, io_lock=slot["io_lock"])
            status = str(result.get("status") or "")
            record["room_id"] = str(result.get("room_id") or record["room_id"])
            record["room_slug"] = str(result.get("room_slug") or "")
            if status == "connected":
                record["status"] = "active"
                record["connected_at"] = time.time()
                self._save(record)
                slot["stop"].wait(record["max_seconds"])
                leave = {
                    "action": "leave",
                    "account_ref": record["account_ref"],
                    "user_id": str(secret["provider_user_id"]),
                    "org_id": str(secret["organization_id"]),
                    "access_token": "",
                    "room_id": record["room_id"],
                    "request_id": record["call_id"] + "-leave",
                }
                result = self._write_read(
                    process, leave, io_lock=slot["io_lock"],
                )
                record["status"] = (
                    "completed" if result.get("status") == "closed" else "unknown"
                )
                record["ended_at"] = time.time()
            elif status == "auth_expired":
                record["status"] = "failed"
                record["error"] = "auth_expired"
            elif status in {"rejected"}:
                record["status"] = "failed"
                record["error"] = str(result.get("error") or "rejected")[:80]
            else:
                # A room may already have been created or a join may have been
                # partially accepted. Never replay an unknown call admission.
                record["status"] = "unknown"
                record["error"] = str(result.get("error") or "unknown")[:80]
        except (DionAuthError, FileNotFoundError) as error:
            record["status"] = "reauth_required" if isinstance(error, DionReauthRequired) else (
                "unknown" if process is not None else "failed"
            )
            record["error"] = type(error).__name__
        except Exception as error:
            record["status"] = "unknown" if process is not None else "failed"
            record["error"] = type(error).__name__
        finally:
            if process is not None:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except Exception:
                    try:
                        process.kill()
                        process.wait(timeout=5)
                    except Exception:
                        pass
                for pipe in (process.stdin, process.stdout):
                    if pipe is not None:
                        pipe.close()
            self._save(record)
            with self.lock:
                self.active.pop(record["call_id"], None)


def calls_for(service):
    with service._manual_send_lock:
        manager = getattr(service, "_dion_calls", None)
        if manager is None:
            manager = service._dion_calls = DionCalls(service)
    return manager
