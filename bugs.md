# Known unrelated defects

## MCP Matrix snapshot/uncertain-send regressions (2026-11-26)

`tests/test_mcp_surface.py::test_matching_snapshot_claim_blocks_edit_and_duplicate_send`
fails with missing `sent`; both variants of `test_uncertain_delivery_keeps_claim_and_blocks_retry`
leave the draft `proposed` rather than `sending`. Reproduced unchanged against
committed HEAD, independently of the DION dirty integration, by extracting
`git archive HEAD src tests/test_mcp_surface.py pyproject.toml` into
`/tmp/opencode/dion-baseline-regression` and running its tests with that source
on `PYTHONPATH`. Found during DION HTTP/MCP regression checks (62 passed, 3 failed).
Not changed as part of DION work.
