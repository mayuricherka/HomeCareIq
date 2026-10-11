# HomeCare IQ: development prompt summary

## AI assistance and process

HomeCare IQ was developed with an AI coding assistant through staged user requests. The user specified the project, selected a simple design suited to learning Python from a Java background, and approved individual implementation steps. The assistant drafted application code, tests, comments, and documentation, and explained the Python concepts and proposed commands.

This document **summarizes and paraphrases the main software-development prompts**. It is not a verbatim transcript or a complete conversation log. It describes the development requests and intended outputs without claiming that every proposed command was executed or that tests have passed.

## Prompts used and their purpose

| Step | Prompt summary (paraphrased) | Purpose and resulting work |
|---|---|---|
| 0 — Discovery | Inspect the repository, README, maintenance CSV, Python, and uv using read-only operations. Show the real CSV columns and explain one row using Java/Maven comparisons. | Establish the existing dataset, environment, and project scope before changing files. |
| 1 — Project setup | Create a minimal application pyproject.toml for homecare-iq, version 0.1.0, requiring Python >=3.13,<3.14, initially with empty dependencies and no packaging build system. Add ignore rules and explain uv setup without executing it. | Define project metadata, environment boundaries, and dependency management. Application and test dependencies were declared through uv during later setup. |
| 2 — Initial interface | Create app.py and maintenance.py. Read the sample using paths relative to the application files. Create exactly three tabs, preview the sample, and show friendly file errors. Keep later features as placeholders. | Separate interface code from data functions and establish Upload Data, Current Status, and Due Tracker. |
| 3 — Upload and validation | Add UTF-8/BOM CSV upload with string identifiers, normalized headers, duplicate checks, required task/schedule fields, valid dates, positive frequency, priority, and finite nonnegative USD costs. Treat blank costs as unknown, use Decimal/integer cents, preserve extras, limit files to 10 MB/10,000 rows, and keep accepted data if an upload fails. | Implement reusable validation, row/field feedback, exact money parsing, and session-state acceptance. |
| 4 — Scheduling | Prefer valid explicit due dates; otherwise calculate service date plus frequency. Warn on disagreement, reject malformed supplied dates, use America/New_York, accept a test date, apply urgency boundaries, sort the tracker, and keep derived fields separate. | Implement dynamic dates, status labels/colors, and a sorted Due Tracker without changing source fields. |
| 5 — Summary dashboard | Add four task cards and exactly two Plotly charts. Show overdue estimates, estimates due in 0–30 days excluding overdue, latest-service cost totals, and cost coverage. Preserve unknown costs and calculate with integer cents. | Implement the Current Status overview and explain filtering and aggregation. |
| 6 — Editing and persistence | Add task selection and editing with a read-only ID. Propose and review due-date changes, permit an override, validate before explicit saving, load valid saved data at startup, protect invalid snapshots, replace rather than merge, write atomically, preserve source fields, and export the saved CSV. | Implement review/save forms and local snapshot persistence in data/saved_tasks.csv. |
| 7 — Testing | Add a small pytest suite and minimal configuration covering urgency boundaries, due-date fallback, duplicate IDs, invalid dates/costs, unknown versus zero, sample totals, temporary CSV saves/reloads, and preservation of existing data on failure. Show test commands without executing them. | Add tests/test_maintenance.py with isolated persistence tests and a fixed date. |
| 8 — Documentation | Write a beginner-friendly README covering setup, tabs, CSV fields, scheduling, costs, editing, explicit saving, replacement, export, troubleshooting, and Maven comparisons. Inspect Git and suggest explicit staging commands without running Git write commands or changing GitHub. | Document usage, design, limitations, and a reviewable submission workflow. |

## Shared instructions that guided the prompts

- Use small ordinary functions, clear names, comments, and short docstrings.
- Explain Python using the user's Java background; prefer a simple two-file application.
- Implement only the approved step and stop afterward.
- Use actual CSV field names; do not invent service history or completion fields.
- Keep the original sample unchanged and personal saved data out of Git.
- Explain commands for the user to execute; do not assume they have run.
- Defer SQLite, completion tracking, service history, accounts, reminders, advanced filters, and extra charts.

## Design decisions visible in the submission

The staged prompts led to a clear separation between source data and derived schedules/costs; exact integer-cent totals; explicit review and save actions; preservation of the previous snapshot on failed writes; and tests using temporary files rather than personal data. The README and source files describe these behaviors so they can be assessed independently of the prompt summary.

## Verification commands

Run from the project root:

```bash
uv sync --locked
uv run --locked pytest -q
uv run --locked streamlit run app.py
```

Use actual test output as evidence of results. The summary records the development process, not a guarantee of runtime correctness.
