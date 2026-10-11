# HomeCare IQ

HomeCare IQ is a local home maintenance dashboard built with **Python, Streamlit, Pandas, Plotly, and uv**. It helps a homeowner identify tasks that need attention, plan maintenance costs, and update the latest service details.

The application manages one home using CSV data and date-based scheduling rules. Each row represents one maintenance task with its latest service information and next due date.

## Run the project

Requires uv and Python 3.13. Open a terminal in the project root, the folder containing `pyproject.toml`, and run:

```bash
uv sync --locked
uv run --locked python --version
uv run --locked streamlit run app.py
```

The version command should report Python 3.13.x. Open the local browser address printed by Streamlit, normally `http://localhost:8501`. Press `Ctrl+C` in the terminal to stop the server. Select `.venv/bin/python` as the IDE interpreter.

`uv sync --locked` installs the versions recorded in `uv.lock` into the project environment. The lockfile must match `pyproject.toml`. See the [uv project guide](https://docs.astral.sh/uv/guides/projects/) for setup details.

## Features and navigation

| Tab | What it provides |
|---|---|
| **Upload Data** | Load the sample or upload a CSV, validate it, preview accepted data, save a dataset, and download the saved snapshot |
| **Current Status** | Four cards: total tasks, overdue, due in 0–7 days, and due in 8–30 days; two charts: urgency counts and estimated costs by category |
| **Due Tracker** | A table sorted by due date, priority, and task ID; task selection, source-field editing, due-date review, and explicit saving |

To explore the project, click **Load sample data** in Upload Data, then open the other two tabs. The original sample contains 18 tasks and 14 columns. An invalid upload leaves previously accepted data active.

## CSV schema and validation

CSV files must use UTF-8; a UTF-8 BOM is accepted. Uploads are limited to 10 MB and 10,000 tasks. Headers are trimmed and normalized to lowercase; duplicate normalized headers and task IDs are rejected. Identifiers are read as strings, preserving leading zeroes.

| Column | Meaning |
|---|---|
| `task_id` | Required, unique, nonblank identifier; read-only in the editor |
| `task_name` | Required, nonblank maintenance task name |
| `category` | Optional grouping; blank displays as Unknown |
| `location` | Optional room or area; blank displays as Unknown |
| `asset_name` | Optional equipment or asset name |
| `last_service_date` | Latest service date; cannot be in the future |
| `frequency_days` | Positive whole number of days when supplied |
| `next_due_date` | Explicit next due date; cannot precede the last service |
| `estimated_cost_usd` | Optional cost estimate for the next occurrence |
| `actual_cost_usd` | Optional latest service cost; requires a valid service date |
| `warranty_expiry_date` | Optional warranty date; stored without warranty alerts |
| `priority` | High, Medium, Low, or blank; capitalization is normalized |
| `vendor` | Optional service provider |
| `notes` | Optional notes |

Dates must be real calendar dates in **YYYY-MM-DD** format. Each row needs either a valid `next_due_date`, or both `last_service_date` and `frequency_days`. A malformed supplied due date remains an error.

Costs must be finite, nonnegative plain USD numbers with at most two decimal places, such as `31.43`, `14.4`, or `0`. Blank costs mean unknown. Optional descriptions may be blank. Extra source columns are preserved in the preview, editor, and saved file. Errors identify the CSV row and field.

## Scheduling

A valid explicit due date takes precedence. If it is blank, the application calculates the due date from the service date plus frequency. If both sources disagree, it warns and retains the explicit date.

Today's date uses **America/New_York**. Status is recalculated on each Streamlit rerun; the application does not refresh continuously at midnight.

| Days until due | Status | Color |
|---|---|---|
| Less than 0 | Overdue | Red |
| 0–7 inclusive | Due soon | Orange |
| 8–30 inclusive | Upcoming | Blue |
| More than 30 | Later | Green |

Text labels accompany colors. Schedule and color fields remain separate from source CSV data.

## Cost calculations

Money is parsed with `Decimal` and summed using integer cents. Dollar conversion is used for display.

The dashboard shows estimated overdue cost, estimated cost due today through 30 days, and recorded last-service cost. Upcoming estimates exclude overdue tasks. Category totals include each task's next occurrence across all urgency groups.

Each summary shows cost coverage. A known zero counts toward coverage; a blank cost remains unknown. If all costs in a group are missing, its total is Unknown. Categories with no known estimates get an explanatory message.

`actual_cost_usd` represents the latest recorded service for its task. Its total is neither annual nor lifetime spending. Updating a service date and cost replaces those latest details; earlier services are not stored as history.

## Editing, saving, and export

1. In Due Tracker, scroll below the table and select a task.
2. Edit its source fields and click **Review changes**. The task ID stays read-only.
3. When service date or frequency changes, review the proposed due date. Confirm it or enter an explicit override. Use **Back to editing** to revise other fields.
4. Click **Save changes**. Validation must succeed before writing.
5. In Upload Data, use **Download saved CSV** to export the saved snapshot.

**Save dataset** and **Save changes** write the entire active dataset to `data/saved_tasks.csv`. Saving replaces an existing snapshot; tasks are not merged. Loading the sample or accepting an upload changes only the session until an explicit save. Export existing saved data before replacing a snapshot you want to keep.

A valid saved snapshot loads at startup. An invalid or unreadable snapshot shows errors and blocks saves without overwriting it. The original sample remains available.

Writing uses a temporary file in the same directory followed by atomic replacement, so a failed write or replacement preserves the previous snapshot. Success appears only after writing completes. Saved files contain source fields and plain USD values.

Streamlit reruns after interactions. Reading can occur on reruns; writing occurs only after a valid explicit save action. Session state preserves accepted data and drafts between reruns. See [Streamlit forms](https://docs.streamlit.io/develop/concepts/architecture/forms).

Saved data is local to one home and excluded from Git.

## Architecture and submission files

```text
HomeCareIQ/
├── app.py                         # Streamlit interface and user actions
├── maintenance.py                 # Validation, scheduling, costs, persistence
├── homecare_maintenance_sample.csv # Original sample dataset
├── tests/
│   └── test_maintenance.py         # Behavior tests with temporary file paths
├── pyproject.toml                  # Metadata, dependencies, pytest settings
├── uv.lock                        # Exact resolved dependencies
├── .python-version                # Python 3.13 selection
├── .gitignore
├── README.md                      # Project description and usage
└── PROMPTS_USED.md                 # Development prompt summary and AI usage
```

This is an application run directly from Python files, with no packaging build system. For a Java background, `pyproject.toml` plays a role similar to `pom.xml`, and pytest assertions serve a purpose similar to JUnit assertions.

SQLite, completion tracking, service history, reminders, accounts, advanced filters, and additional charts are outside the current scope.

## Testing

```bash
uv run --locked pytest -q
```

The suite defines 30 cases covering urgency boundaries, due-date fallback and overrides, duplicate IDs, invalid dates and costs, unknown versus zero costs, exact sample totals, CSV round trips, and failed-save protection. Persistence tests use temporary paths rather than the real saved dataset.

Tests use a fixed date of **2026-10-09**. Expected sample counts are 3 overdue, 2 due in 0–7 days, 2 due in 8–30 days, and 11 later. Expected totals are $1,023.00 in estimates across all tasks, $1,033.51 in latest-service costs, $75.00 in overdue estimates, and $212.00 in estimates due within 0–30 days. Live counts depend on today's date.

The expected result is `30 passed`; report the actual terminal output when submitting. This README does not certify a test run.

## Troubleshooting

| Problem | Action |
|---|---|
| uv is missing | Follow the [official installation instructions](https://docs.astral.sh/uv/getting-started/installation/) and reopen the terminal |
| Missing packages or wrong interpreter | Run `uv sync --locked`; use `uv run --locked` and select `.venv/bin/python` in the IDE |
| Sample file not found | Keep the sample CSV beside `app.py` and `maintenance.py` |
| CSV rejected | Read row/field errors; check encoding, IDs, dates, scheduling fields, and costs |
| Saved snapshot invalid | Preserve a copy, correct the reported fields, and restart the application |
| Changes disappeared | Complete an explicit save and confirm its success message |
| Download differs from active data | Download exports the saved snapshot, not an unsaved upload or draft |
| Counts differ from test expectations | The dashboard uses today's New York date; tests use a fixed date |

## Development prompts and AI assistance

See [PROMPTS_USED.md](PROMPTS_USED.md) for an accurately labeled summary of the staged development prompts, their purpose, and the role of the AI coding assistant.
