# Repository Guidelines

## Project Structure & Module Organization

```
.
├── fetch_normalize_logs.py   # Loki log fetcher and normalizer
├── linked-data.py            # Linked list with bubble sort (SQLite-backed)
├── create_sample_data.py     # SQLite sample data generator
├── Modelfile                 # Ollama model definition
├── user_database.db          # Sample SQLite database
├── Deployment/               # Docker Compose, Ansible, and config files
│   ├── docker-compose.yml    # Loki + Promtail + Grafana stack
│   ├── alloy-deploy.yml
│   ├── promtail/             # Ansible playbooks, packages, templates
│   └── files/                # Alloy and Loki configuration YAMLs
└── .venv/                    # Python 3.13 virtual environment
```

All Python source lives at the repository root. Deployment assets are in `Deployment/`.

## Build, Test, and Development Commands

Activate the virtual environment before running any script:

```bash
source .venv/bin/activate
```

- **Run the log fetcher**: `python fetch_normalize_logs.py` — queries Loki for error/warning logs and prints normalized JSON.
- **Generate sample data**: `python create_sample_data.py` — populates `user_database.db` with test rows.
- **Run linked-list demo**: `python linked-data.py` — exercises the linked list implementation against SQLite data.
- **Start the logging stack**: `docker compose -f Deployment/docker-compose.yml up -d` — launches Loki, Promtail, and Grafana locally.

There are currently no automated tests or build steps in this repository.

## Coding Style & Naming Conventions

- **Indentation**: 4 spaces (PEP 8).
- **Naming**: `snake_case` for functions and variables (`fetch_logs`, `raw_message`); `PascalCase` for classes (`Node`, `LinkedList`).
- **Imports**: Standard library first, then third-party, separated by a blank line.
- No formatter or linter is configured yet. Consider adding `ruff` or `black` for consistency.

## Testing Guidelines

No testing framework or coverage tool is set up. When tests are added:

- Use `pytest` as the test runner.
- Name test files with a `test_` prefix (e.g., `test_fetch_logs.py`).
- Aim to cover the core log-fetching and normalization logic first.

## Commit & Pull Request Guidelines

- **Branching**: Work directly on feature branches off `main`; submit via pull request.
- **Commit messages**: Keep them short and descriptive (e.g., `"adding model file and rules"`). No strict conventional-commit format is enforced.
- **PR descriptions**: Include a summary of changes, rationale, and any deployment or configuration impacts. Link related issues when applicable.

## Environment & Dependencies

- **Python**: 3.13 (managed via `.venv`).
- **Key dependencies**: `requests` — install with `pip install requests` inside the venv.
- **Docker**: Required for the Loki/Promtail/Grafana stack.
- **Ollama**: Used with the `Modelfile` for local LLM inference. Pull and run with `ollama create rootmedic -f Modelfile`.
