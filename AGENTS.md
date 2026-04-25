# Repository Guidelines for Agentic Development
*(≈ 150 lines – concise but comprehensive)*

---

## 1️⃣ Project Layout & Core Modules
```
/bot.py                     # entry‑point, starts health server + telegram polling
/util/
    __init__.py
    config.py               # env loading, prompt caching, constants
    telegram.py             # all bot handlers, keyboards, callbacks
    postgres.py             # PostgreSQL persistence layer
    openrouter.py           # LLM request wrapper with retry/fallback logic
    scheduler.py            # daily summary job registration
    health.py               # Flask health/nudge service
    message_queue.py        # per‑chat async queue for sequential processing
    postgres.py             # optional Postgres persistence layer
/tests/
    conftest.py            # shared fixtures, monkeypatches, mock servers
    test_*.py              # pytest suites (unit + integration)
scripts/
    run_tests_with_coverage.sh   # wrapper for coverage HTML/XML generation
    build_apk.sh                 # local APK build (auto-detects JDK 17 & Android SDK)
/android/                        # Android notification capture app (Jetpack Compose + Room)
    app/src/main/java/com/expensetracker/notif/
        MainActivity.kt          # entry‑point activity, screen navigation
        data/
            AppDatabase.kt       # Room database singleton
            NotificationEntity.kt
            NotificationDao.kt
            AppFilterPrefs.kt    # SharedPreferences for app notification filter
        service/
            NotificationCaptureService.kt  # NotificationListenerService impl
        ui/
            NotificationsScreen.kt   # main notification list screen
            SettingsScreen.kt        # app filter settings screen
            NotificationsViewModel.kt
            Theme.kt
    app/build.gradle.kts         # versionCode/versionName live here
Procfile                    # process declarations for deployment platforms
.prompt.txt                 # LLM prompt used by the bot
```

---

## 2️⃣ Environment Setup & Build Commands
| Task | Command | Description |
|------|---------|-------------|
| **Create venv** | `python -m venv .venv && source .venv/bin/activate` | Isolates dependencies. |
| **Install deps** | `pip install -r requirements.txt -r requirements-test.txt` | Core + test packages. |
| **Run bot locally** | `python bot.py` | Requires a populated `.env`. |
| **Run all tests** | `make test` *(or `pytest -q` inside the venv)* | Fast unit‑test run, no external services. |
| **Run single test** | `pytest tests/test_telegram.py::test_help_command -vv` | Replace path & function as needed. |
| **Run coverage** | `make coverage` or `./scripts/run_tests_with_coverage.sh` | Generates `htmlcov/` and `coverage.xml`. |
| **Lint / type‑check** | `ruff check . && mypy util/ tests/` | Enforces style & static typing (optional). |
| **Start health only** | `RUN_TELEGRAM_BOT=false python bot.py` | Useful for CI health checks. |
| **Build APK locally** | `make build-apk` *(or `./scripts/build_apk.sh`)* | Requires JDK 17 + Android SDK. Output: `android/expense-tracker.apk`. |

*All commands assume the virtual environment is active (`source .venv/bin/activate`).*

---

## 3️⃣ Testing Guidelines
- **Framework:** `pytest` with `asyncio` plugin (auto‑detects async tests).
- **Markers:** `@pytest.mark.integration` for external calls, `@pytest.mark.slow` for long but deterministic runs.
- **Isolation:** Use factories/mocking (`responses`, `freezegun`, `aiounittest`) to stub network and time‑dependent code.
- **Coverage Goal:** ≥ 90 % overall, ≥ 80 % on newly touched modules.
- **Running a single test** (see table above) is the preferred workflow during development.

---

## 4️⃣ Code Style & Naming Conventions
| Aspect | Guideline |
|--------|-----------|
| **Indentation** | 4 spaces, no tabs. |
| **Line length** | ≤ 100 chars; split long strings with parentheses. |
| **Imports** | Grouped: std‑lib → third‑party → local; alphabetical within groups; explicit (`from telegram import Update`). |
| **Naming** | Modules/files `snake_case.py`; functions `snake_case()`; classes `PascalCase`; constants `UPPER_SNAKE_CASE`. |
| **Type hints** | All public functions (handlers, utils) must have full annotations. Use `typing` (`Optional`, `Dict`, `List`). |
| **Docstrings** | Google style triple‑quoted strings; include param/return sections for non‑trivial signatures. |
| **Logging** | One module‑level logger: `logger = logging.getLogger(__name__)`; prefer `logger.*` over `print`. |
| **Error handling** | Catch specific exceptions, add context if re‑raising, and surface user‑friendly Telegram messages. |
| **Async best practices** | All I/O functions are `async def`; never block the loop with sync code (`time.sleep`). |
| **Keyboard helpers** | Small dedicated functions (`get_command_keyboard`, `get_full_command_keyboard`) returning a `ReplyKeyboardMarkup`. |
| **Constants** | Centralize in `util/config.py` or module globals; avoid magic numbers. |

Additional notes:
- Use f‑strings everywhere.
- Keep Telegram‑specific logic thin; delegate heavy work to helpers in `util/`.
- Configuration reads should always go through `util.config`. 

---

## 5️⃣ Dependency Management
When a new third‑party library is required:
1. Install it inside the active venv (`pip install <pkg>`).
2. Freeze with `pip freeze > requirements.txt` (or `requirements-test.txt` if test‑only).
3. Add an entry under **New Dependencies** in this file describing purpose and version range.
4. Run the full test suite to confirm nothing breaks.

---

## 6️⃣ Commit & Pull‑Request Process
1. **Branch naming:** `feature/<short-description>` or `bugfix/<issue-id>`.
2. **Commit title:** ≤ 50 chars, imperative mood (e.g., “Add dashboard menu button”).
3. **PR checklist:**
   - Run `make test && make coverage`.
   - Lint with `ruff check .` (and optional `mypy`).
   - Verify new code is covered by tests.
   - Update docs (`README`, this `AGENTS.md`) if behaviour changes.
   - Ensure no secrets are added to the diff.

---

## 7️⃣ Security & Secrets
- `.env` and `credentials.json` are listed in `.gitignore`; never commit them.
- All secret access must go through helpers in `util/config.py`.
- Required env vars: `TELEGRAM_BOT_TOKEN`, `DATABASE_URL`, `OPENROUTER_API_KEY`. Optional: `OPENROUTER_LLM_VERSION`, `SERVICE_URL`.

---

## 8️⃣ CI & Android Release Pipeline

**Python CI** (future):
```yaml
steps:
  - pip install -r requirements.txt -r requirements-test.txt
  - ruff check .
  - make test && make coverage
```

**Android APK release** (`.github/workflows/android-apk.yml`):
- **Trigger:** Push to `main` touching `android/` files, or manual `workflow_dispatch`.
- **Steps:** JDK 17 + Android SDK setup → `./gradlew assembleDebug` → publish to GitHub Releases.
- **Release tag:** `android-latest` (rolling prerelease, always overwritten).
- **Asset:** `expense-tracker.apk`.

**Bot APK distribution** (`/app` command in Telegram):
1. Bot checks for local `android/expense-tracker.apk`.
2. If missing, fetches from GitHub Releases (`android-latest` tag) via GitHub API.
3. Downloads APK bytes server-side and sends to user via Telegram.
- Config: `GITHUB_REPO` and `APK_RELEASE_TAG` env vars in `util/config.py`.

**To release a new Android version:** bump `versionCode`/`versionName` in `android/app/build.gradle.kts`, commit, and push to `main`. The CI will build and publish automatically.

---

## 9️⃣ Cursor & Copilot Rules
*No `.cursor/` or `.github/copilot‑instructions.md` files exist, so there are currently no special cursor or copilot directives.*
If such files appear, copy their relevant sections here and reference them in the **Agent** documentation.

---

*This file is deliberately detailed to give autonomous coding agents a clear contract for building, testing, styling, and safely modifying the repository.*