# UserIO

### Your conversations. Your AI. One inbox.

**Free to use. Bring your own AI key or connect your MCP client.**

Messages, email, SMS and call history in one inbox. Let your AI find agreements,
explain what matters and prepare replies you can review.

**[Open UserIO →](https://msg.bezrabotnyi.com/)** · **[Quick start](docs/guide/quickstart.md)** · **[All docs](docs/README.md)** · [Русское руководство](https://msg.bezrabotnyi.com/docs/)

- **Your accounts, together.** Group authors and distinct account identities.
- **Your text, sent immediately.** AI replies need approval; MAX messages stay editable.
- **Your phone, connected.** Android SMS APK, separate native/root and call setup.
- **Your AI, informed.** MCP, API, context, cached summaries and secretary processing.
- **Find the source.** [Text and optional semantic history search](docs/history-search.md) with message links and your own Qdrant.

## Start in minutes

1. [Create your account](https://msg.bezrabotnyi.com/signup): username and password.
2. [Connect a channel](docs/guide/channels.md), then compare one message with its original.
3. [Connect your AI](docs/guide/connect-ai.md) via OAuth at `https://msg.bezrabotnyi.com/mcp`.

Try: **“Find the latest agreement in this conversation. Show the source messages
and prepare a reply.”**

## Run it yourself

In a Python **3.12+** virtual environment:

```bash
python -m pip install 'universal-userio[fast-agent] @ git+https://github.com/megamen32/universal-userio.git'
```

Then [configure credentials, connect providers and start the service](docs/guide/self-host.md).
Authorize channels separately; installation does not connect them.

## MAX и VK в Matrix

[Matrix-мост через UserIO](deploy/matrix-provider-bridge/README.md) объединяет личные
диалоги и группы MAX/ВКонтакте в пространствах Matrix. Текст, изображения и файлы
до 2 МиБ передаются в обе стороны. Установка, ограничения и участие описаны на русском.

## Go deeper

| I want to… | Open |
| --- | --- |
| Connect my accounts | [Telegram](docs/guide/telegram.md) · [MAX](docs/guide/max.md) · [VK](docs/guide/vk.md) · [Mail](docs/guide/gmail.md) · [More channels](docs/guide/channels.md) |
| Connect my Android | [SMS APK](docs/guide/phone.md) · [Calls](docs/guide/calls.md) |
| Understand the assistant | [Secretary](docs/guide/secretary.md) · [Context & privacy](docs/guide/security.md) |
| Build an integration | [MCP](docs/guide/mcp.md) · [HTTP](docs/guide/http-api.md) · [Adapter SDK](docs/adapter-sdk.md) |
| Operate a server | [Deploy & rollback](deploy/INSTALL.md) · [Runtime reference](docs/RUNTIME-REFERENCE.md) · [Resource budgets](docs/resource-budget.md) |
| Find a problem | [Troubleshooting](docs/guide/troubleshooting.md) · [FAQ](docs/guide/faq.md) · [Every document](docs/README.md) |

Provider capabilities differ. Personal MAX/WhatsApp bridges are unofficial; IMAP
and user-linked Matrix currently connect for reading. Personal VK Messenger now
connects for server-side reading, repeated edits and text/photo/document sending
after one-time authorization.
[See supported paths and limitations](docs/guide/channels.md).
