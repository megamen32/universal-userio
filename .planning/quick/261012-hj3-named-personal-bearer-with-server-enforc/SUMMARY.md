# Read-only personal token — source verification

Added explicit `read_only` scope using existing token storage and principal. MCP manifest hides all mutable tools; both real dispatch paths refuse manual mutation calls. HTTP refuses mutation and credential/management access, allowing bounded content reads. Default full personal tokens and OAuth unchanged. Existing token page offers permission choice; account reply capability is removed from restricted responses.

Focused integration RED reproduced missing scope; final ordinary 57 checks GREEN in39.92s (maximum180s), covering read-only restrictions, revocation, user isolation, full token compatibility, OAuth and HTTP/MCP. Tests create no provider messages.

Root Exmanager owns final credential issuance into encrypted UserIO resource and native Secretary read acceptance. UserIO owns rollback-first publication/install and live temporary-token read/refused-write/revoke proof. Production acceptance pending installation; never claim a live connection from tests.
