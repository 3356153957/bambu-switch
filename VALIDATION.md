# Validation

Verification date: 2026-10-19. Version: 1.1.1.

- 18 local tests cover isolated profile switching, backups, writer locks, interruption recovery, conflicts, desktop shortcut creation, and adding an account without launching Studio.
- Tests use temporary directories and fictional session files. They do not use real accounts or send print jobs.
- The Chinese desktop interface has been visually inspected at high DPI. The README screenshot was captured from the actual application in isolated demo mode.
- Release archives include the executable, both READMEs, attribution, and dependency licenses.

Not verified: persistent login for real accounts, cloud connectivity, or printing on physical devices. Automated tests and a successful build do not establish those behaviors.
