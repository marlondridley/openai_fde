# OpenAI API Key Management

This guide covers how to keep `OPENAI_API_KEY` out of source control while letting the demos run locally, in CI, and inside Docker.

## 1. Local development (.env + python-dotenv)

1. Install the helper dependency inside your virtual environment:
   ```powershell
   pip install python-dotenv
   ```
2. Copy the example file and paste your real key (never commit the new `.env`):
   ```powershell
   Copy-Item .env.example .env
   notepad .env   # paste OPENAI_API_KEY=sk-...
   ```
3. Populate the Postgres credentials (`PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`) in the same `.env`. The Streamlit dashboard and Docker services read those values at runtime so no container image stores credentials.
4. `demo/utils.py` automatically loads `.env` via `python-dotenv` whenever a demo script imports it. You can also load it manually in ad-hoc scripts:
   ```python
   from pathlib import Path
   from dotenv import load_dotenv

   load_dotenv(Path(__file__).resolve().parent / ".env")
   ```
5. Confirm it is available to Python before running live demos:
   ```powershell
   python -c "import os; print(os.environ.get('OPENAI_API_KEY', 'missing'))"
   ```

## 2. GitHub Actions secret

1. In GitHub, open **Settings → Secrets and variables → Actions**.
2. Create a new *Repository secret* named `OPENAI_API_KEY` and paste the live key once.
3. In workflows, reference it without printing to logs:
   ```yaml
   env:
     OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
   steps:
     - name: Run evals (live)
       run: python demo/run_all.py --live
       env:
         OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
   ```
4. Rotate the secret from the same screen if it is ever exposed. No code changes are needed because workflows only read from `secrets.OPENAI_API_KEY`.

## 3. Docker / Docker Compose injection

**Goal:** Keep credentials in your local `.env`, then hand them to Docker at runtime instead of baking them into images.

- Docker Compose automatically reads a `.env` file in the project root. To be explicit:
  ```powershell
  docker compose --env-file .env run --rm demo python demo/run_all.py --mock
  docker compose --env-file .env run --rm demo python demo/run_all.py --live
  ```
- Plain `docker run` can use the same file:
  ```powershell
  docker run --rm --env-file .env demo:latest python demo/run_all.py --live
  ```
- For ad-hoc containers (e.g., VS Code devcontainers) you can pass the variable interactively:
  ```powershell
  $env:OPENAI_API_KEY = (Get-Content .env | Select-String OPENAI_API_KEY).ToString().Split('=')[1].Trim()
  docker run --rm -e OPENAI_API_KEY=$env:OPENAI_API_KEY demo:latest python demo/run_all.py --live
  ```

**Rules of thumb:**
- `.env` stays local; `.env.example` documents the required keys.
- GitHub Actions use `secrets.OPENAI_API_KEY` so no plaintext ever appears in YAML.
- Containers read from the host environment (either `--env-file` or forwarded variables), which keeps the Dockerfile and `docker-compose.yml` portable and public-safe.
