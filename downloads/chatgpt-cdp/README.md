# ChatGPT CDP starter — установка (macOS)

Этот пакет ставит серверную часть `chatgpt-cdp-mcp`: она превращает одну
залогиненную страницу ChatGPT в вашем браузере в ограниченный MCP-инструмент
(`list_chats`, `export_chat`, `send_message`, …), который потребляет UserIO.

**Статус**: сервер готов и тестируется bundled mock-драйвером. Реальный CDP
драйвер (мост к вашему Chrome) пишется отдельно по гайду проекта — см. ниже.

## 1. Требования

- macOS с Homebrew
- Node.js 20+
- Chrome / Chromium / BrowserOS с залогиненным chatgpt.com

```bash
brew install node      # если Node.js ещё нет
node --version         # нужно >= 20
```

## 2. Установить сервер

```bash
npm install --global git+https://github.com/megamen32/chatgpt-cdp-mcp.git
```

## 3. Проверить на mock-драйвере (без аккаунта)

```bash
chatgpt-cdp-mcp --help
# smoke-test MCP wiring на встроенном mock-драйвере:
# см. docs/QUICKSTART.md в репозитории
```

## 4. Реальный драйвер (Chrome с ChatGPT)

В комплекте пакета есть только mock. Реальный драйвер — небольшой JS-модуль
через Chrome DevTools Protocol:

1. Запустите Chrome с портом отладки:
   `open -na "Google Chrome" --args --remote-debugging-port=9222`
2. Напишите драйвер по гайду:
   https://github.com/megamen32/chatgpt-cdp-mcp/blob/main/docs/DRIVER.md
3. Укажите его путь в переменной `CDP_CHAT_DRIVER_MODULE`.

## 5. Подключить к UserIO (сервер)

На сервере UserIO в `/etc/universal-userio.env` добавьте:

```
USERIO_CHATGPT_CDP_MCP_COMMAND=chatgpt-cdp-mcp
CDP_CHAT_DRIVER_MODULE=/path/to/your-driver.mjs
```

и перезапустите `universal-userio`. После этого канал `chatgpt` появится в
`userio.channels.list`.

## Ссылки

- Quick start: https://github.com/megamen32/chatgpt-cdp-mcp/blob/main/docs/QUICKSTART.md
- Driver guide: https://github.com/megamen32/chatgpt-cdp-mcp/blob/main/docs/DRIVER.md
- Tool reference: https://github.com/megamen32/chatgpt-cdp-mcp/blob/main/docs/TOOLS.md
- Security model: https://github.com/megamen32/chatgpt-cdp-mcp/blob/main/docs/SECURITY.md
