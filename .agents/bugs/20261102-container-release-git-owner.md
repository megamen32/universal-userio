# LHC bug: installer Git preflight runs as root instead of repository owner

- ID: USERIO-20261102-CONTAINER-RELEASE-GIT-OWNER
- Status: OPEN
- Reporter: UserIO auto-replies owner
- Severity: rollout blocked
- Component: scripts/container_release.py
- Discovery: 2026-11-02
- Scope: read-only Git preflight, preserving privileged Docker actions

## Reproduction
Repository ownership: roomhacker UID 1000. Installer (root) Git preflight fails with Git dubious ownership. Existing scripts/container_release.py already documents runuser -u roomhacker -- for SSH. No safe.directory, chown, permission or credential changes authorized.

## Required resolution
The fixed-repository Git read-only operations execute through the established repo owner executor. Immutable published origin/main/HEAD, source blob checks, root-only Docker entry, locks, rollback and release identity are unchanged.

## Evidence
- [x] RED focused test
- [x] GREEN focused test
- [ ] Reviewed and published installer fix
- [ ] Immutable source/candidate match
- [ ] Guarded backend/frontend deployment and synthetic CRUD/preview/ACL/dedupe

## Source fix evidence
- RED: test_container_release_git_owner.py, 4 failures (repo_git_argv missing).
- GREEN: 5 focused tests passed; 31 existing container release source/revision/idle tests passed.
- Root read-only canary: UID 0 retained, Git source queries run via runuser roomhacker, exact published main dcb556581919fa1b05ed3e6f37d026fdc84a08a3.
