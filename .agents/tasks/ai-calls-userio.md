# ИИ-звонки S21 в UserIO

- Status: partial; UserIO bridge proven live, phone audio path awaiting selected hardware route
- Started at: 2026-09-12T00:54:14+03:00 (system clock; uptime 86266.07s)
- Estimate: minimum 45 / maximum 90 active minutes

## Minimal path

- Wanted result: локальный ИИ ведёт GSM-звонок через rooted S21, а ход и транскрипт звонка отображаются в UserIO.
- Shortest real canary: контролируемый звонок на безопасный номер, одна реплика в обе стороны, завершённый звонок и обе реплики видны в UserIO.
- Smallest YAGNI slice: безопасный diagnostic-only S21 profile для AgentCall и отдельный AgentCall event -> UserIO `phone` ingress bridge; до RX/TX qualification звонки запрещены.
- Discard now: автодозвон, кампании, multi-SIM, широкая совместимость устройств, бессрочный аудиоархив, публичный ADB и непроверенные mixer writes.

## Evidence

- Upstream AgentCall 1.0.1 is qualified only for POCO M2 Pro and physical USB/ADB.
- S21 is `SM-G998B` / `p3s`, Exynos `universal2100_r`, Android 15/API 35, SELinux Enforcing.
- Root SSH works without ADB. Audio policy exposes `AUDIO_DEVICE_IN_VOICE_CALL`, `incall_music_uplink`, and voice uplink/downlink channel masks.
- UserIO already accepts canonical `phone` messages through authenticated `POST /v1/messages`.
- Existing UserIO checkout contains unrelated staged and unstaged work; this task owns only this file, `agentcall_ingress.py`, its focused tests, and the console entrypoint hunk.

## Gates

- No privileged APK/Magisk install or external call before a device-specific fail-closed profile and RX/TX qualification plan exist.
- Synthetic UserIO record is intermediate evidence only; it is not the final call canary.

## TDD evidence

- Red: `python3 -m pytest tests/test_agentcall_ingress.py -q` failed during collection with `ModuleNotFoundError: universal_userio.agentcall_ingress`.
- Green: `python3 -m pytest tests/test_agentcall_ingress.py -q` -> `6 passed`.
- Focused regression: `python3 -m pytest tests/test_agentcall_ingress.py tests/test_http_api.py -q` -> `15 passed`.
- Live Red 1: wrong assumed gateway `127.0.0.1:8787` returned 401; actual UserIO runtime is `USERIO_PORT=18093`.
- Live Red 2: real UserIO accepted ingress with HTTP 202, which the first bridge client rejected; a focused test reproduced it.
- Live Green: four idempotent synthetic AgentCall events were accepted by live UserIO on `:18093`; `userio.channels.list` returned channel `phone`, conversation `conv_5f80881e66d987d4e953d3fd`, final snippet `Звонок завершён (synthetic_canary)`, unread `4`.
- Existing unrelated suite debt remains: full collection fails on removed `routes_from_environment` / `NoticePlaceOutboxClient`; `test_multi_user.py` has four stale exact-message assertions. None is caused by this task.
- Commit is blocked by a pre-existing merge (`MERGE_HEAD=d932a3f`) with unrelated staged work; partial commit is forbidden. Task files were returned to unstaged/untracked state and no foreign path was committed.
- Measured active time at 2026-09-12T01:17:19+03:00: 1385.17s (23m05s), derived from monotonic uptime 86266.07 -> 87651.24.

## Reconciliation note

Preserved from the managed runtime on 2026-09-16. The historical merge blocker
above describes the original runtime attempt; the canonical AgentCall source and
focused tests were already present on `main` when this record was recovered.
