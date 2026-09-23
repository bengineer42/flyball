# Security policy

flyball controls physical equipment. A bug that lets someone move an actuator is a safety
problem as well as a security one, so please report it privately.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting:
<https://github.com/bengineer42/flyball/security/advisories/new>.

**Please do not open a public issue, pull request or discussion for a suspected
vulnerability.**

Useful in a report: what the attacker needs (network position, an account, physical access),
what they get, the version or commit, and the rig file or configuration that reproduces it
with any secrets removed.

You will get an acknowledgement within a few days. This is a single-maintainer project, so
please allow a reasonable period to investigate and fix before disclosing publicly.

## Supported versions

flyball has not had a release. Only the default branch is supported; fixes land there.

## Scope

In scope: the HTTP and websocket API, the MCP server, authentication and session handling,
the Go daemon and its routing, rig-file and program parsing, the recorder, and anything that
lets an unauthorised party read telemetry or command a device.

Out of scope, because it is how the system is designed to work:

- **A rig file, a driver package or a program is trusted input.** A driver is Python that the
  runner imports and runs; installing one is as consequential as installing any other
  package. Do not load configuration you would not run as a script.
- Anything that requires you to already have shell access on the machine running the rig.
- Denial of service against your own rig by configuring it that way.

## Known posture

Authentication and the trust boundary are actively being worked on, and the current answer is
deliberately conservative: the daemon is intended to be reachable from the local machine, not
from an untrusted network. **Do not expose a rig to the internet.** If you have, treat that as
the finding and tell us what it let you do.
