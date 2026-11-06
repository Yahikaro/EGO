# Security policy

## Supported versions

Only the latest release receives security fixes. The project has not yet had a public release.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Report it privately to the project owner (see the repository profile for contact details). Include:

- a short description of the problem and its impact;
- steps to reproduce, or a proof of concept;
- the version and Windows edition you tested on.

You will receive an answer within a reasonable time. Please give the owner time to fix the problem before disclosing it publicly.

## Scope

In scope: the Core API, sign-in and sessions, encryption of stored data, the Control Center, the client, and the tunnel and home-network access features.

Out of scope: third-party software (llama.cpp, Cloudflare tunnel, models) unless the issue comes from how EGO uses it.

## Built-in protections

- Passwords are stored as salted scrypt hashes; sign-in codes and session tokens are stored only as hashes.
- Lockouts after repeated wrong codes or passwords, and a rate limit on sign-in attempts.
- The database is encrypted; keys are protected by Windows (DPAPI).
- Home-network access uses HTTPS only; the tunnel is off by default.
