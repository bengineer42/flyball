# Security policy

flyball controls physical equipment. A bug that lets someone move an actuator is a safety
problem as well as a security one, so please report it privately.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting:
<https://github.com/bengineer42/flyball/security/advisories/new>.

If that page is not available, open an issue asking for a security contact — **with no
details of the problem in it** — and you will be given somewhere private to send them.

**Please do not put a suspected vulnerability in a public issue, pull request or
discussion.**

Useful in a report: what the attacker needs (network position, a credential, physical
access), what they get, the commit you tested, and the rig file or configuration that
reproduces it with any secrets removed.

I aim to acknowledge within a week. This is a one-person project — if you have heard nothing
after 14 days, assume it was missed and ping the issue tracker without details, rather than
concluding you are being ignored. If we have not agreed something else, 90 days from your
report is a reasonable time to publish. English, please.

There is no bug bounty. You will be credited in the advisory unless you would rather not be.

## Safe harbour

Testing against **your own rig or your own installation** is authorised, and a report made in
good faith will never be met with legal action. Do not test against someone else's rig, do
not access anyone else's data, and stop as soon as you have enough to write the report. If
you are unsure whether something is in bounds, ask first.

## Supported versions

flyball has not had a release. Only the default branch is supported; fixes land there.

## Scope

In scope:

- the HTTP and websocket API, and authentication and authorisation on it;
- the MCP server and its tool tiers;
- the Go daemon — its API, **its reverse proxy to registered runners**, and the process
  launching behind `POST /api/runners`. Privilege escalation from a read-only credential to
  anything that runs code is very much in scope;
- **parsing and schema validation of rig files and programs** — a crash, a path traversal, a
  resource exhaustion or a type confusion reached from a rig file's *contents*;
- the web dashboard, and the client packages it is built from;
- the recorder and the session store;
- the hardware extensions — i2c, spi, gpio, pwm, Modbus, VISA — which are the code that
  actually moves an actuator;
- credential handling: any way that a token or password ends up in a response, a log, a rig
  document or a URL;
- missing secure defaults (see below);
- anything that lets an unauthorised party read telemetry or command a device.

Dependency vulnerabilities are welcome but low priority unless you can show the path is
actually reachable in flyball.

Out of scope, because it is how the system is designed to work:

- **A driver package is code.** A driver is Python that the runner imports and executes.
  Installing one is as consequential as installing any other package.
- **The rig-file fields that name code to run** — `runner.drivers`, and the `driver:` /
  `instrument:` fields that import a dotted path — are trusted by design for the same reason.
  *The rest of a rig file is not:* it is parsed with a safe YAML loader, `tomllib` or
  `json.loads` into schema-validated models, and bugs in that path are in scope, per above.
- Anything that requires you to already have shell access on the machine running the rig.
- Denial of service against your own rig by configuring it that way.

## Known posture

Authentication is **opt-in**: it is only enforced when a password or token is configured, and
with neither set every caller is treated as fully authorised. The runner and the daemon
default to binding loopback, but **nothing currently enforces that** — an operator who passes
`--host 0.0.0.0` gets an open, unauthenticated rig, and some of this project's own examples
still show exactly that. Work to close this is tracked and not yet merged.

So: **do not expose a rig to an untrusted network.** But missing secure defaults, missing
enforcement and anything that makes accidental exposure easy are **in scope and wanted** —
those are among the most useful reports this project can receive, not operator error.
