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
5. Попросите агента показать подключённые аккаунты UserIO. Убедитесь, что MiniMax
   вызвал инструмент UserIO и получил список; текстовый ответ модели сам по себе
   не подтверждает подключение.

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

В пакет не записан ваш токен. Если MiniMax Code не завершит авторизацию MCP при
первом обращении к инструменту, создайте персональный токен на странице
[подключения MCP в UserIO](https://msg.bezrabotnyi.com/connect-mcp). Добавьте
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

Если оба подключения видны агенту, используйте `userio-authenticated`; не
вызывайте `UserIO`, если это подключение сообщает об ошибке авторизации.
Управлять областью доступа и отзывать токены можно на странице UserIO. Для
режима чтения выпустите токен с областью `read_only`; сервер применит её и к
вызовам плагина. Подробнее: [подключение ИИ к UserIO](connect-ai.md).
