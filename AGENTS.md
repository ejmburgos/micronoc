# Repository Guidelines

## Project Structure & Module Organization
This repository is currently in bootstrap state (no code checked in yet). Use this layout as the default:

- `src/`: application source code, organized by feature or domain.
- `tests/`: unit and integration tests mirroring `src/` paths.
- `scripts/`: local automation (build, lint, release helpers).
- `docs/`: architecture notes, ADRs, and operational runbooks.
- `assets/` (optional): static files such as images or fixtures.

Example: `src/users/service.ts` should map to `tests/users/service.test.ts`.

## Build, Test, and Development Commands
No toolchain is committed yet. When adding one, expose a minimal command set and keep it consistent:

- `make setup` or `npm install`: install dependencies.
- `make dev` or `npm run dev`: run locally with hot reload if available.
- `make test` or `npm test`: run the full test suite.
- `make lint` or `npm run lint`: run static checks.
- `make format` or `npm run format`: apply code formatting.

Document the chosen commands in `README.md` and keep this file aligned.

## Coding Style & Naming Conventions
- Use 2 or 4 spaces consistently per language (do not mix within a file).
- Prefer descriptive names: `user_service.ts`, `PaymentProcessor`, `fetchOrderById`.
- Keep modules focused; avoid large “god files.”
- Enforce style with formatter + linter (e.g., Prettier/ESLint, Black/Ruff, or equivalents for chosen stack).

## Testing Guidelines
- Place tests under `tests/` with paths parallel to `src/`.
- Name tests clearly (`*.test.*` or `test_*.py`, depending on language).
- Cover core business logic, error paths, and integration boundaries.
- Aim for meaningful coverage on critical paths before merging.

## Commit & Pull Request Guidelines
Git history is not available yet; adopt Conventional Commits:

- `feat: add order validation`
- `fix: handle null customer id`
- `docs: update setup steps`

PRs should include:

- clear summary of change and scope,
- linked issue/ticket when applicable,
- test evidence (command output or screenshots),
- migration/config notes if behavior changes.

## Security & Configuration Tips
- Never commit secrets; use `.env` files ignored by git.
- Provide `.env.example` with required variables and safe defaults.
- Validate external input and fail securely by default.
