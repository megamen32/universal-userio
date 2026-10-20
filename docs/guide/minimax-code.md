# UserIO в MiniMax Code на macOS

В корне репозитория UserIO лежит весь серверный проект. Пакет плагина находится
в отдельном подкаталоге. Импортируйте именно его: MiniMax Code ограничивает
распакованные файлы выбранного плагина объёмом 64 МиБ. Файлы корневого проекта
занимают 67,2 МиБ, поэтому его импорт заканчивается общей ошибкой
**GitHub Plugin cannot be imported**. Отдельный пакет занимает около 10 КиБ.

## Импорт плагина из GitHub

1. В MiniMax Code откройте **Plugins → Import plugin**.
2. Вставьте эту ссылку на подкаталог пакета:

   ```text
   https://github.com/megamen32/universal-userio/tree/main/plugins/universal-userio
   ```

   Ссылка без `/tree/main/plugins/universal-userio` указывает на весь серверный
   репозиторий и превышает лимит распакованного пакета.
3. Нажмите **Preview**, затем **Import**.
4. Убедитесь, что `universal-userio` появился в списке плагинов и его Skill
   `userio` включён.
5. Если UserIO просит авторизацию, сначала выполните шаги в разделе
   «Авторизация UserIO» ниже, затем попросите агента показать подключённые
   аккаунты. Убедитесь, что MiniMax вызвал инструмент UserIO и получил список;
   текстовый ответ модели сам по себе не подтверждает подключение.

В публичном marketplace megamen32 пакет также ссылается на этот подкаталог.
MiniMax Code не устанавливает индекс marketplace целиком.

## Если GitHub Import недоступен

Посмотрите активный каталог локальных плагинов:

```sh
mcode plugin marketplace list
```

Скопируйте отдельную папку `plugins/universal-userio` в найденный каталог
плагинов. Для стандартного профиля MiniMax Code на macOS это
`~/.minimax/plugins/`:

```sh
git clone --depth 1 https://github.com/megamen32/universal-userio.git /tmp/universal-userio-source
mkdir -p ~/.minimax/plugins
cp -R /tmp/universal-userio-source/plugins/universal-userio ~/.minimax/plugins/
mcode plugin list --available --marketplace local
mcode plugin enable universal-userio@local
```

Команда показывает путь к источнику `local`. Если используете профиль MiniMax
или `MINIMAX_DATA_DIR`, устанавливайте плагин в указанный там каталог, а не в
путь по умолчанию. Для обновления уже
установленного плагина замените его файлы из этой же поддиректории после выхода
из MiniMax Code. Не копируйте весь серверный проект в каталог плагинов.

## Авторизация UserIO

В пакет плагина не записан ваш токен, и добавить его туда нельзя: пакет лежит в
публичном репозитории, а подключение `UserIO` в `mcp.json` плагина не умеет
подставлять переменные окружения. Поэтому у плагина всегда два разных
подключения:

| Подключение | Где живёт | Токен | Подстановка `${ПЕРЕМЕННОЙ}` |
| --- | --- | --- | --- |
| `UserIO` | `mcp.json` пакета плагина | нет, по условию | нет — значения берутся дословно |
| `userio-authenticated` | `.mcp.json` проекта | `${USERIO_MCP_TOKEN}` | да, MiniMax Code подставляет |
| `userio-authenticated` | конфиг уровня приложения | литералом в файле | нет |

MiniMax Code не содержит MCP-OAuth-клиента, поэтому подключение `UserIO` без
токена всегда отвечает ошибкой авторизации — вызывать его не надо. Рабочее
подключение — `userio-authenticated`.

Создайте персональный токен на странице
[подключения MCP в UserIO](https://msg.bezrabotnyi.com/connect-mcp) и добавьте
сервер с именем `userio-authenticated` в `.mcp.json` в корне рабочего проекта
MiniMax Code. Подключение плагина и сервер из `.mcp.json` управляются отдельно,
поэтому разные имена не смешают их. Объедините запись с имеющимися серверами,
не удаляя другие записи:

```json
{
  "mcpServers": {
    "userio-authenticated": {
      "type": "http",
      "url": "https://msg.bezrabotnyi.com/mcp",
      "headers": {
        "Authorization": "Bearer ${USERIO_MCP_TOKEN}"
      }
    }
  }
}
```

MiniMax Code подставляет переменную окружения из процесса. Чтобы не сохранять
токен в файле, введите его скрытым вводом и запустите `mcode` из того же
терминала:

```sh
read -r -s -p 'UserIO MCP token: ' USERIO_MCP_TOKEN
printf '\n'
export USERIO_MCP_TOKEN
mcode
```

Приложение, запущенное из Finder или Dock, окружение терминала не наследует.
Передайте токен через `launchd`, тогда переменная доживёт до любой следующей
запущенной копии MiniMax Code:

```sh
launchctl setenv USERIO_MCP_TOKEN "$(security find-generic-password -s userio-mcp -a "$(id -un)" -w)"
launchctl unsetenv USERIO_MCP_TOKEN   # отзыв
```

Токен из `launchctl setenv` живёт в сессии пользователя и виден процессам,
запущенным после этой команды, поэтому после выдачи перезапустите MiniMax Code.

Если приложение запускается без переменной окружения, положите токен
литералом в конфиг уровня приложения `~/.minimax/mcp/mcp.json` — там
подстановки нет, зато запись переживает переустановку и обновление плагина:

```json
{
  "mcpServers": {
    "userio-authenticated": {
      "type": "http",
      "url": "https://msg.bezrabotnyi.com/mcp",
      "headers": { "Authorization": "Bearer <токен>" }
    }
  }
}
```

Если оба подключения видны агенту, используйте `userio-authenticated`; не
вызывайте `UserIO`, если это подключение сообщает об ошибке авторизации.
Управлять областью доступа и отзывать токены можно на странице UserIO. Для
режима чтения выпустите токен с областью `read_only`; сервер применит её и к
вызовам плагина. Подробнее: [подключение ИИ к UserIO](connect-ai.md).

Сервер говорит на обеих версиях протокола MCP: на legacy-хендшейке
`2024-11-05` (его используют клиенты 2025 года, в том числе MiniMax Code) и
на современной ревизии `2026-07-28` с per-request метаданными. Клиент, который
шлёт заголовок `MCP-Protocol-Version: 2024-11-05`, обслуживается как legacy и
не обязан присылать `_meta`.
