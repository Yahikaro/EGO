# Contributing to EGO

Thank you for your interest. EGO is a private project in development. Until the first release, changes are made by the owner only. This file describes how contributions will be handled once the project opens up.

## Before you start

- Read [README](README.md), [Architecture](docs/ARCHITECTURE.md) and the [Developer guide](docs/DEVELOPER_GUIDE.md).
- Open an issue before a large change, so we can agree on the approach.
- Check the [roadmap](docs/ROADMAP.md) for planned work.

## Rules

- Keep changes backward compatible: add fields, do not remove them.
- Never commit personal data, passwords, tokens, mail settings, paths from your machine or story text.
- Logs must not contain story text.
- Every new error code goes to the error catalog with text in Russian and English.
- Tests must pass before a pull request is opened. Update the API contract snapshot if you change the API.
- Write commit messages in the imperative mood, one line for the summary.

## Pull requests

1. Fork the repository and create a branch from `main`.
2. Make the change and add or update tests.
3. Run the test suite (see the developer guide).
4. Open a pull request using the template and describe what changed and how it was tested.

## Licensing

A license will be chosen with the first release. Contributions made before that are covered by the same terms as the project.
