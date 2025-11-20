# Repository Guidelines

## Project Structure & Module Organization
- `bot.py` is the entry point; it wires the health server, nudge pinger, and Telegram polling loop.
- `util/` holds core modules: `config.py` (env + prompt loading), `telegram.py` (bot handlers), `sheets.py` (Google Sheets I/O), `openrouter.py` (LLM calls), `scheduler.py` (daily summaries), and `health.py` (Flask health/nudge services).
- `tests/` contains the pytest suite (`test_*.py`) plus shared fixtures in `conftest.py`.
- Supporting files: `prompt.txt` (LLM prompt), `Procfile` (process declaration), `scripts/run_tests_with_coverage.sh`, coverage outputs in `htmlcov/` and `coverage.xml`.

## Build, Test, and Development Commands
- Create a venv and install deps: `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt -r requirements-test.txt`.
- Run the bot locally (requires .env + credentials): `python bot.py`.
- Fast test run: `make test` (verbosely runs pytest via the venv).
- Coverage run: `make coverage` or `./scripts/run_tests_with_coverage.sh` (HTML in `htmlcov/`, XML in `coverage.xml`).
- Linting is manual; run `python -m pytest tests/ --cov=. --cov-report=term-missing` before opening a PR.

## Coding Style & Naming Conventions
- Follow PEP 8 with 4-space indentation and module-level `logger = logging.getLogger(__name__)`; prefer `logging` over `print`.
- Use f-strings and small, pure helpers inside `util/` to keep Telegram and Sheets handlers readable.
- Tests follow `test_*.py` with `Test*` classes or free functions; fixtures live in `tests/conftest.py`.
- Keep configuration reads centralized in `util/config.py` instead of scattering `os.getenv` calls.

## Testing Guidelines
- Primary framework: pytest with asyncio support and coverage defaults from `pytest.ini`.
- Mark long-running or external calls with `@pytest.mark.integration`/`@pytest.mark.slow`; default runs should stay green without external services.
- Target coverage ≥ the current 90%+ badge; add unit tests for new paths and regression tests for bug fixes.
- Use factories/mocking (responses, freezegun) to isolate Telegram and Sheets interactions.

## Commit & Pull Request Guidelines
- Commit messages are short, imperative summaries (e.g., “Add daily spend display”), similar to existing history.
- For PRs, include: summary of behavior change, how to reproduce/test (`make test`/`make coverage` outputs), and any config or prompt updates that reviewers should mirror.
- Link related issues/tasks; if behavior affects deployment, note Procfile or env var changes explicitly.

## Security & Configuration Tips
- Store secrets in `.env` and keep `credentials.json` out of commits; use `GOOGLE_CREDENTIALS_PATH` to point at local credentials.
- Required vars: `TELEGRAM_BOT_TOKEN`, `GOOGLE_SHEET_ID`, `OPENROUTER_API_KEY` (for LLM), plus optional `OPENROUTER_LLM_VERSION` and `SERVICE_URL`.
- When updating `prompt.txt` or scheduler timings, document the change in the PR description so operators can sync runtime config.
