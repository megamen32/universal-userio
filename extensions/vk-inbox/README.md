# Universal UserIO — VK Inbox extension

This is an early Chrome MV3 connector. It uses the VK account already logged in in the current Chrome profile; it does not read or export a VK browser token.

1. Open `chrome://extensions`, enable Developer mode, choose **Load unpacked**, and select this folder.
2. Open VK Web and a conversation.
3. Click the extension → **Настройки**. Normally the endpoint is already set to `https://msg.bezrabotnyi.com/v1/messages`. If the site auth cookie is not accepted by the browser request, enter the UserIO API token locally in this extension's options.
4. Click **Забрать текущий чат**.

The first version reads messages rendered in the open VK Web DOM. VK can change DOM selectors; the connector reports only messages it can see and deduplicates them locally per page.
