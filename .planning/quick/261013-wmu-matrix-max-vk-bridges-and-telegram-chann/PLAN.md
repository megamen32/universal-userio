---
status: in-progress
---
# Matrix MAX/VK мосты и очистка Telegram

Outcome: @bezrabotnyi видит личные MAX/VK переписки и группы в Matrix,
отправляет и изменяет текст из Matrix; каналов/обсуждений Telegram нет.
Разрешён VK Никита Кожемяко200003099333, MAX уже подключён как max.
Matrix exit не меняет удалённое членство.

Owned: deploy/matrix-provider-bridge/; этот quick-каталог;
runbook/inventory Matrix после live reconciliation.
WIP server.mjs/messenger.py/Telegram call files сохраняется, core UserIO не меняется.
1. Удалить из личной Matrix23подтверждённых channel/discussion portals,
backup+allow-filter+before/after joined proof. DONE172→149, остальные149сохранены.
2. Добавить стандартный AS, portals/ghosts, safe delivery/edit/echo, finite unit.
3. Publish owned source; deploy bridge100 + registration88; один Synapse restart.
4. Real final canary: Matrix→VK self/MAX saved and native readback,
provider→Matrix source, replay persistence; scoped publish evidence.

Tests: fast unit expected2/max30s, isolation/replay/leaves/edits15PASS1.16s.
Focused integration expected30/max180s includes setup and canaries.
Live bridge cap RAM96/192MiB/swap0/CPU0.5/Tasks32/IO25/disk512MiB/media2MiB.
