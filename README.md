# Everydayprep Scheduler

FastAPI-based lesson scheduling application with SQLite for local use and
PostgreSQL for production deployments.

## Local setup

1. Create the virtual environment and install dependencies with
   `setup_windows.bat`, or install `requirements.txt` manually.
2. For direct local execution, run `run_windows.bat`. A development-only session
   secret is generated in memory when no value is configured.
3. For Docker Compose, copy `.env.example` to `.env`, generate two different
   random values, and set `SAT_SCHEDULER_SESSION_SECRET` and
   `SAT_SCHEDULER_DB_PASSWORD`. Compose intentionally refuses to start without
   them.

`seed.py` creates accounts with documented demo passwords and therefore refuses
to run when `SAT_SCHEDULER_ENV=production`.

Generate a suitable value without placing it in shell history:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

The local `.env`, SQLite databases, virtual environments, logs, generated output,
private keys, and common cloud credential files are excluded from both Git and
Docker build contexts.

## Production configuration

Set `SAT_SCHEDULER_ENV=production`, use a unique random
`SAT_SCHEDULER_SESSION_SECRET`, enable `SAT_SCHEDULER_HTTPS_ONLY=1`, and supply
PostgreSQL credentials through the deployment platform's secret manager. The
application refuses to start in production when these safeguards are missing or
when a documented placeholder secret is used.

Do not store production values in `render.yaml`, `compose.yml`, a committed `.env`
file, source code, screenshots, logs, or GitHub Actions workflow files. Render's
blueprint generates the session secret and obtains the database URL from the
managed database rather than committing either value.

## Before publishing to GitHub

Run the tests, then inspect exactly what Git will upload:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
git status --short --ignored
git add --dry-run .
git diff --check
git config --get user.email
```

Before the first push:

- Confirm `.env`, `scheduler.db`, `tmp/`, `output/`, and `.venv/` appear as
  ignored and are not staged.
- Review every staged file with `git diff --cached`.
- If you do not want a personal email address embedded permanently in commit
  metadata, copy your exact GitHub-provided `noreply` address from GitHub's email
  settings and set it for this repository with `git config user.email "..."`.
- Enable GitHub secret scanning and push protection, Dependabot alerts, private
  vulnerability reporting, and branch protection for the default branch.
- If a real credential was ever committed, revoke and rotate it before rewriting
  history. Adding the file to `.gitignore` does not remove it from existing
  commits.

Dependency update pull requests are configured in `.github/dependabot.yml`.
Security reports should follow [SECURITY.md](SECURITY.md).
