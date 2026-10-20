# Universal UserIO Agent Plugin

Portable Agent Plugin 1.0 package for connecting an agent to the owner's
Universal UserIO MCP endpoint and loading the `userio` Skill.

Import this package from MiniMax Code with this GitHub URL:

```text
https://github.com/megamen32/universal-userio/tree/main/plugins/universal-userio
```

This subdirectory contains only the plugin package. It does not include the
UserIO server application. The package does not contain personal credentials;
authorize the MCP connection through the client or a separate protected client
configuration. See the [MiniMax Code installation guide](../../docs/guide/minimax-code.md)
for GitHub import and token setup.

The `UserIO` entry in `mcp.json` is intentionally credential-free: this package
is public, and MiniMax Code takes plugin `mcp.json` headers verbatim without
substituting environment variables. The `UserIO` connection therefore reports an
authorization error, and the working connection is `userio-authenticated` —
declared by the user in the project `.mcp.json` (with `${USERIO_MCP_TOKEN}`,
which MiniMax Code does substitute) or in the application-level MCP config
(with the token written literally). The guide shows both, plus how to hand the
token to a GUI-launched MiniMax Code through `launchctl setenv`.
