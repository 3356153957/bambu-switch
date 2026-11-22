# Validation

Verification date: 2026-11-21. Version: 1.2.2.

- 1.2.2: 68 local tests pass, including a new check that the account-list scrollbar is packed only while the list overflows and removed again when it fits. The release executable is built by GitHub Actions from the tagged commit.

- 1.2.1: 67 local tests pass, including new checks that a non-object `state.json` and an unreadable or incomplete `pending.json` raise a clear error while keeping the journal file. The release executable is built by GitHub Actions from the tagged commit. The packaged window was not re-inspected for this patch release.

Previous release (2026-11-05, 1.2.0):

- 65 local tests pass. They cover isolated profile switching, backups, writer locks, interruption recovery, conflicts, shortcuts, cache detection and automatic import, persistent opt-out, and account-file import/export.
- Tests use temporary directories and fictional session files. They do not use real accounts or send print jobs.
- Layout regressions check the minimum window size, 100%/150%/200% scaling, long account names, footer visibility, and themed input dialogs. The source application was visually inspected in isolated demo mode; the README screenshot shows its actual themed dialog and fictional accounts.
- The real local Studio cache was inspected read-only and detected. No real session was imported, exported, decrypted, or sent elsewhere during verification.
- Import/export tests use fictional sessions, preserve opaque networking files byte-for-byte, reject malformed archives, and keep existing accounts on failure. Successful UI import selects the newly imported account without launching Studio.
- The Windows executable was built successfully and a demo launch was requested. The final visual review of the packaged executable has not been completed.
- Release archives use explicit file lists. The top level contains the executable, 使用说明.md, and support/ with both READMEs, attribution, screenshot, and dependency licenses. Real account exports are excluded.

Not verified: persistent login for real accounts, sign-in persistence across computers, cloud connectivity, or printing on physical devices. Automated tests and a successful build do not establish those behaviors.
