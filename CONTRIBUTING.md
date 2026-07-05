# Contributing to Provenova

Thanks for wanting to contribute! Provenova is maintained by one person, so a few ground
rules keep things sane.

## Before you start

- **Open an issue first** for anything larger than a small fix. It saves you from building
  something that can't be merged, and the maintainer from reviewing it cold.
- Response times are honest, not instant — this is a solo-maintained project.

## Licensing: what you're contributing to

One repo, two licenses — the directory you touch determines the license the public receives
your code under:

| Directory | License |
|---|---|
| `packages/ql-core`, `packages/ql-sdk`, `packages/ql-crawler`, `frameworks/` | Apache-2.0 |
| `server/` | BUSL-1.1 (each release converts to Apache-2.0 four years after it ships) |

Every contribution, in any directory, is additionally licensed to the Licensor under the
[Provenova CLA](CLA.md) — see below. Plain-language guide to the licensing model, with
examples: [provenova.net/docs/licensing](https://provenova.net/docs/licensing).

## The CLA (one comment, one time)

Provenova dual-licenses the server (BUSL-1.1 plus commercial licenses) and schedules every
server release to convert to Apache-2.0 after four years. Both are only legally possible if
the Licensor holds relicensing rights over all contributed code — rights a DCO sign-off would
not grant. So contributions require signing the [Provenova CLA](CLA.md).

Signing is automated: on your first pull request the CLA bot comments with instructions;
reply with the exact sentence it asks for and you're done — one signature covers all future
contributions. Contributing on behalf of your employer? See the
[note in the CLA](CLA.md#contributing-on-behalf-of-a-company).

## Development setup

See the [README Quickstart](README.md#quickstart-local-no-account--5-minutes) for the guided
version. In short:

```bash
make install   # editable install of all packages into .venv
make test      # pytest suite
make e2e       # full end-to-end flow
```

## Pull request guidelines

- Keep PRs small and focused — one change per PR.
- Add or update tests for behaviour changes.
- No drive-by reformatting; match the style of the code you're editing.
- Fill in the PR template — it includes the per-directory license reminder and the CLA
  checkbox.

## Security issues

Please **don't open public issues for security problems** — email
[hi@ben.gy](mailto:hi@ben.gy?subject=Provenova%20security) instead.
