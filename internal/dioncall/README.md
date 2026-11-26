# UserIO DION call helper

This is the narrow UserIO-owned lifecycle seam for DION calls. It deliberately
does not import WhiteTransport or expose credentials. The production `Core`
implementation will adapt the independent upstream `relay/dion` package:
`Session`/`CreateRoom`/`ConnectWSS` plus `Call.Start`/`Close`.

The current tests prove only account identity admission and create/join/leave
state transitions with a fake core. No network call, room creation, media, or
real participant is performed.
