"""Read, validate, schedule, summarize, and explicitly save maintenance data."""

# These helpers are built into Python, so they need no additional packages.
import csv
import os
import re
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from io import StringIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo

import pandas as pd


# __file__ locates this module, independently of the terminal's directory.
SAMPLE_CSV_PATH = Path(__file__).resolve().parent / "homecare_maintenance_sample.csv"
SAVED_CSV_PATH = Path(__file__).resolve().parent / "data" / "saved_tasks.csv"
MAX_UPLOAD_MB = 10
MAX_FILE_BYTES = MAX_UPLOAD_MB * 1024 * 1024
MAX_ROWS = 10_000  # Underscores make large numbers easier to read, as in Java.
SOURCE_COLUMNS = (
    "task_id", "task_name", "category", "location", "asset_name",
    "last_service_date", "frequency_days", "next_due_date",
    "estimated_cost_usd", "actual_cost_usd", "warranty_expiry_date",
    "priority", "vendor", "notes",
)
COST_COLUMNS = ("estimated_cost_usd", "actual_cost_usd")

# Hex colors work with Pandas table styling and, later, Plotly charts.
# Each status also has a text label, so color is not the only urgency indicator.
STATUS_COLORS = {
    "Overdue": "#b91c1c",   # Red
    "Due soon": "#c2410c",  # Orange
    "Upcoming": "#1d4ed8",  # Blue
    "Later": "#15803d",     # Green
}


def maintenance_today() -> date:
    """Return today's calendar date in the homeowner's New York timezone."""
    return datetime.now(ZoneInfo("America/New_York")).date()


def validation_issue(row: int | None, field: str, message: str) -> dict:
    """Describe an error or warning using a small dictionary, like a Java Map."""
    return {"Row": "File" if row is None else str(row), "Field": field, "Message": message}


def read_csv_data(content: bytes) -> pd.DataFrame:
    """Read UTF-8 CSV bytes, checking headers and limits before building a table."""
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("The CSV exceeds the 10 MB file limit.")
    try:
        # utf-8-sig accepts both ordinary UTF-8 and UTF-8 with a BOM.
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("The CSV must use UTF-8 encoding.") from error

    reader = csv.reader(StringIO(text, newline=""), strict=True)
    records = []
    row_numbers = []
    try:
        headers = next(reader, None)
        if not headers:
            raise ValueError("The CSV is empty. Add a header and at least one task.")
        headers = [header.strip().lower() for header in headers]
        if any(not header for header in headers):
            raise ValueError("Every CSV column needs a nonblank header.")
        duplicates = [name for name, count in Counter(headers).items() if count > 1]
        if duplicates:
            raise ValueError("Duplicate headers after normalization: " + ", ".join(duplicates))

        # Check headers ourselves: Pandas can otherwise rename duplicate headers.
        previous_line = reader.line_num
        for values in reader:
            row_number = previous_line + 1
            previous_line = reader.line_num
            if not values:
                continue  # Ignore completely empty lines between records.
            if len(values) != len(headers):
                raise ValueError(
                    f"Row {row_number}: expected {len(headers)} fields, got {len(values)}."
                )
            if len(records) >= MAX_ROWS:
                raise ValueError("The CSV exceeds the 10,000-task limit.")
            records.append(values)
            row_numbers.append(row_number)
    except csv.Error as error:
        raise ValueError(f"CSV format error near line {reader.line_num}: {error}") from error

    if not records:
        raise ValueError("The CSV has column names but no task rows.")
    # csv.reader returns strings, so identifiers such as 001 remain unchanged.
    tasks = pd.DataFrame(records, columns=headers, dtype=str)
    # attrs holds table metadata; it does not add a column to the source data.
    tasks.attrs["csv_row_numbers"] = row_numbers
    return tasks


def read_sample_data() -> pd.DataFrame:
    """Read the existing sample through the same parser used for uploads."""
    if SAMPLE_CSV_PATH.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("The sample CSV exceeds the 10 MB file limit.")
    return read_csv_data(SAMPLE_CSV_PATH.read_bytes())


def read_saved_data(path: Path = SAVED_CSV_PATH) -> pd.DataFrame:
    """Read a saved snapshot without creating it or changing its contents."""
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("The saved CSV exceeds the 10 MB file limit.")
    return read_csv_data(path.read_bytes())


def save_data(
    tasks: pd.DataFrame, path: Path = SAVED_CSV_PATH
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]:
    """Atomically save valid source data and return its parsed data, cents, and warnings."""
    # index=False prevents Pandas' row index from becoming an extra CSV field.
    # Only source data enters this function, never the schedule or integer-cents table.
    content = tasks.to_csv(index=False).encode("utf-8")
    # Reading the proposed bytes also checks file size, row limits, and CSV headers.
    data, cents, errors, warnings = validate_data(read_csv_data(content))
    if errors:
        details = "; ".join(
            f"Row {issue['Row']}, {issue['Field']}: {issue['Message']}" for issue in errors
        )
        raise ValueError("The dataset was not saved. " + details)

    # Never replace an invalid existing snapshot, even with a valid new upload.
    if path.exists():
        try:
            _, _, saved_errors, _ = validate_data(read_saved_data(path))
        except ValueError as error:
            raise ValueError("The existing saved CSV is invalid; correct it first.") from error
        if saved_errors:
            raise ValueError("The existing saved CSV is invalid; correct it first.")

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        # Like Java try-with-resources: with closes the file even when an error occurs.
        # The same directory keeps replacement on the same filesystem.
        with NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=".saved_tasks-", suffix=".tmp", delete=False
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        # Replace only after the complete temporary file has been written and closed.
        os.replace(temporary_path, path)
    finally:
        # A failed write/replacement leaves the old snapshot intact; clean up the draft.
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    # The round-trip also refreshes CSV line numbers after edits to multiline notes.
    return data, cents, warnings


def update_task(tasks: pd.DataFrame, task_id: str, changes: dict) -> pd.DataFrame:
    """Apply one task's source-field edits to a copy, preserving its identifier."""
    matching_task = tasks["task_id"].eq(task_id)
    if int(matching_task.sum()) != 1:
        raise ValueError("Select exactly one existing task to edit.")
    if changes.get("task_id", task_id) != task_id:
        raise ValueError("Task IDs cannot be changed.")
    updated = tasks.copy()
    for field, value in changes.items():
        if field == "task_id":
            continue
        # An optional field absent from the upload is added only when it gets a value.
        # Existing extra columns and values on other tasks remain intact.
        if field not in updated.columns:
            if not value:
                continue
            updated[field] = ""
        updated.loc[matching_task, field] = value
    return updated


def propose_due_date(original: dict, changes: dict) -> tuple[str | None, str]:
    """Suggest a due date after service/frequency edits, without changing accepted data."""
    scheduling_fields = ("last_service_date", "frequency_days")
    if all(changes.get(field, "") == original.get(field, "") for field in scheduling_fields):
        return None, "Service date and frequency are unchanged; review the entered due date."
    try:
        last_service = parse_date(changes.get("last_service_date", ""))
        frequency = parse_frequency(changes.get("frequency_days", ""))
        if last_service is None or frequency is None:
            return None, "No date can be proposed without both service date and frequency."
        proposed = last_service + timedelta(days=frequency)
    except (ValueError, OverflowError) as error:
        return None, "Could not propose a date. Correct the service date/frequency: " + str(error)
    return proposed.isoformat(), (
        "Service date or frequency changed. Proposed due date: " + proposed.isoformat()
        + ". Review it below; you may enter an explicit override before saving."
    )


def parse_date(value: str) -> date | None:
    """Return a calendar date; a blank optional date becomes None (like null)."""
    if not value:
        return None
    # A full-match regular expression checks the format, like Java String.matches.
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("Use YYYY-MM-DD, for example 2026-10-09.")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("Enter a real calendar date in YYYY-MM-DD format.") from error


def parse_frequency(value: str) -> int | None:
    """Accept blank or a positive whole number of calendar days."""
    if not value:
        return None
    if not re.fullmatch(r"[0-9]+", value):
        raise ValueError("Enter a positive whole number of days, for example 30.")
    try:
        days = int(value)
    except ValueError as error:
        raise ValueError("The frequency is too large to use.") from error
    if days <= 0:
        raise ValueError("The frequency must be greater than zero.")
    # timedelta also has a maximum supported number of days.
    if days > 999_999_999:
        raise ValueError("The frequency is too large to use.")
    return days


def parse_money_cents(value: str) -> int | None:
    """Parse USD exactly, like Java BigDecimal, and return integer cents."""
    if not value:
        return None  # Unknown cost is different from a known cost of zero.
    try:
        amount = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("Enter a USD amount, for example 31.43.") from error
    if not amount.is_finite() or amount < 0:
        raise ValueError("The cost must be finite and nonnegative.")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", value):
        raise ValueError("Use plain USD numbers with at most two decimals, for example 31.43.")
    # Decimal("31.43") is exact; creating a Decimal from a float can lose that property.
    # Set enough precision to avoid rounding even an unusually large supplied amount.
    with localcontext() as context:
        context.prec = max(28, len(amount.as_tuple().digits) + 2)
        return int(amount * 100)


def validate_schedule(task: dict, row: int, today: date) -> list[dict]:
    """Check supplied dates and ensure each task has a usable scheduling source."""
    errors = []
    dates = {}
    for field in ("last_service_date", "next_due_date", "warranty_expiry_date"):
        try:
            dates[field] = parse_date(task.get(field, ""))
        except ValueError as error:
            dates[field] = None
            errors.append(validation_issue(row, field, str(error)))

    try:
        frequency = parse_frequency(task.get("frequency_days", ""))
    except ValueError as error:
        frequency = None
        errors.append(validation_issue(row, "frequency_days", str(error)))

    last_service = dates["last_service_date"]
    due_date = dates["next_due_date"]
    if last_service is not None and last_service > today:
        errors.append(validation_issue(
            row, "last_service_date", "Last service cannot be future-dated."
        ))
    if due_date is not None and last_service is not None and due_date < last_service:
        errors.append(validation_issue(
            row, "next_due_date", "Due date cannot precede the last service."
        ))

    # Check that this interval can produce a date before accepting the dataset.
    if last_service is not None and frequency is not None:
        try:
            last_service + timedelta(days=frequency)
        except OverflowError:
            errors.append(validation_issue(
                row, "frequency_days", "This interval produces an unsupported date."
            ))
    if not task.get("next_due_date", "") and (last_service is None or frequency is None):
        errors.append(validation_issue(
            row, "next_due_date",
            "Supply a due date, or both a service date and a positive frequency."
        ))
    if task.get("actual_cost_usd", "") and last_service is None:
        errors.append(validation_issue(
            row, "actual_cost_usd", "A last-service cost needs a valid last_service_date."
        ))
    return errors


def validate_task(task: dict, row: int, today: date) -> tuple[dict, list[dict], list[dict]]:
    """Validate one task and return its costs in cents, errors, and warnings."""
    errors = validate_schedule(task, row, today)
    warnings = []
    costs = {}
    for field in ("task_id", "task_name"):
        if not task.get(field, ""):
            errors.append(validation_issue(row, field, "This field cannot be blank."))
    priority = task.get("priority", "")
    if priority and priority not in ("High", "Medium", "Low"):
        errors.append(validation_issue(
            row, "priority", "Use High, Medium, or Low, or leave blank."
        ))
    for field in COST_COLUMNS:
        try:
            costs[field] = parse_money_cents(task.get(field, ""))
        except ValueError as error:
            costs[field] = None
            errors.append(validation_issue(row, field, str(error)))
        if field in task and not task[field]:
            warnings.append(validation_issue(
                row, field, "Cost is unknown; it will not become zero."
            ))
    for field in ("category", "location"):
        if field in task and not task[field]:
            warnings.append(validation_issue(row, field, "Blank values display as Unknown."))
    return costs, errors, warnings


def validate_data(
    tasks: pd.DataFrame, today: date | None = None
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict], list[dict]]:
    """Return normalized source data, separate integer costs, errors, and warnings."""
    if today is None:
        today = maintenance_today()
    data = tasks.copy()  # Normalize a copy instead of changing the caller's table.
    errors = []
    warnings = []
    for field in data.columns:
        data[field] = data[field].fillna("").astype(str).str.strip()
    if "priority" in data.columns:
        data["priority"] = data["priority"].str.title()

    for field in ("task_id", "task_name"):
        if field not in data.columns:
            errors.append(validation_issue(None, field, "Required column is missing."))
    if "next_due_date" not in data.columns and not {
        "last_service_date", "frequency_days"
    }.issubset(data.columns):
        errors.append(validation_issue(
            None, "schedule", "Include next_due_date, or both last_service_date and frequency_days."
        ))

    extra = [field for field in data.columns if field not in SOURCE_COLUMNS]
    missing_optional = [field for field in SOURCE_COLUMNS[2:] if field not in data.columns]
    if extra:
        warnings.append(validation_issue(
            None, "columns", "Preserved, unused extra columns: " + ", ".join(extra)
        ))
    if missing_optional:
        warnings.append(validation_issue(
            None, "columns",
            "Fields not supplied: " + ", ".join(missing_optional)
            + ". Some details are unavailable."
        ))
    if errors:
        return data, pd.DataFrame(), errors, warnings

    row_numbers = data.attrs.get("csv_row_numbers", list(range(2, len(data) + 2)))
    duplicates = data["task_id"].duplicated(keep=False) & data["task_id"].ne("")
    cost_records = []
    # records turns each row into a dictionary; enumerate also gives its position.
    for index, task in enumerate(data.to_dict(orient="records")):
        row = row_numbers[index]
        if duplicates.iloc[index]:
            errors.append(validation_issue(row, "task_id", "Duplicate task ID: " + task["task_id"]))
        costs, row_errors, row_warnings = validate_task(task, row, today)
        cost_records.append(costs)
        errors.extend(row_errors)
        warnings.extend(row_warnings)

    # object dtype retains Python integers and None instead of converting them to floats.
    # Keep cents separate: the uploaded source column names and extra fields are preserved.
    cents = pd.DataFrame(cost_records, columns=COST_COLUMNS, index=data.index, dtype=object)
    return data, cents, errors, warnings


def resolve_due_date(task: dict) -> tuple[date, str | None]:
    """Choose the explicit due date or a calculated fallback, with a mismatch warning."""
    # Parse the supplied date first. A malformed value raises an error;
    # only a blank value allows fallback to a calculated date.
    supplied = parse_date(task.get("next_due_date", ""))
    last_service = parse_date(task.get("last_service_date", ""))
    frequency = parse_frequency(task.get("frequency_days", ""))
    calculated = None
    if last_service is not None and frequency is not None:
        # Adding timedelta days works like Java's LocalDate.plusDays(frequency).
        calculated = last_service + timedelta(days=frequency)

    if supplied is not None:
        warning = None
        if calculated is not None and supplied != calculated:
            warning = (
                f"Supplied date {supplied.isoformat()} differs from service date + frequency "
                f"({calculated.isoformat()}). Using the supplied date."
            )
        return supplied, warning
    if calculated is not None:
        return calculated, None
    raise ValueError("A task needs a valid due date or a service date and frequency.")


def calculate_due_status(days_until_due: int) -> tuple[str, str]:
    """Return an urgency label and color using inclusive day boundaries."""
    # Earlier branches take precedence. elif is Python's equivalent of else if.
    if days_until_due < 0:
        status = "Overdue"
    elif days_until_due <= 7:
        status = "Due soon"  # Includes today (zero days) and exactly seven days away.
    elif days_until_due <= 30:
        status = "Upcoming"  # The earlier branch already handled days zero through seven.
    else:
        status = "Later"
    # A tuple returns two values together; the caller can assign each to a variable.
    return status, STATUS_COLORS[status]


def calculate_schedule(
    tasks: pd.DataFrame, as_of_date: date | None = None
) -> tuple[pd.DataFrame, list[dict]]:
    """Build a sorted, separate schedule from accepted source data without changing it."""
    if as_of_date is None:
        as_of_date = maintenance_today()
    records = []
    warnings = []
    priority_order = {"High": 0, "Medium": 1, "Low": 2}
    row_numbers = tasks.attrs.get("csv_row_numbers", list(range(2, len(tasks) + 2)))

    for index, task in enumerate(tasks.to_dict(orient="records")):
        due_date, warning = resolve_due_date(task)
        # Subtracting dates returns a timedelta. .days gives its whole-day difference.
        # For example: yesterday - today gives -1, meaning overdue.
        days_until_due = (due_date - as_of_date).days
        status, color = calculate_due_status(days_until_due)
        records.append({
            "task_id": task["task_id"],
            "effective_due_date": due_date,
            "days_until_due": days_until_due,
            "due_status": status,
            "status_color": color,
            "_priority_order": priority_order.get(task.get("priority", ""), 3),
        })
        if warning:
            warnings.append(validation_issue(row_numbers[index], "next_due_date", warning))

    # Preserve source row indexes so the interface can align names and other details.
    schedule = pd.DataFrame(records, index=tasks.index, columns=[
        "task_id", "effective_due_date", "days_until_due", "due_status",
        "status_color", "_priority_order",
    ])
    schedule = schedule.sort_values(
        ["effective_due_date", "_priority_order", "task_id"], kind="stable"
    )
    # The temporary numeric priority is for sorting, not a source or display field.
    return schedule.drop(columns="_priority_order"), warnings


def format_usd(cents: int | None) -> str:
    """Format integer cents for display, keeping unknown amounts distinct from zero."""
    if cents is None:
        return "Unknown"
    # // is whole-number division; % gives the remainder, like Java integer math.
    # This avoids floating-point arithmetic when formatting monetary totals.
    return f"${cents // 100:,}.{cents % 100:02d}"


def summarize_costs(values: pd.Series) -> dict:
    """Sum recorded integer costs and report how many tasks contributed."""
    # dropna removes unknown costs (None), but keeps a recorded cost of zero.
    known_values = values.dropna().tolist()
    # Python sum uses integer arithmetic here, comparable to summing Java long values.
    # No known costs means an unknown total, rather than a misleading zero-dollar total.
    total = sum(int(value) for value in known_values) if known_values else None
    return {
        "total_cents": total,
        "known_count": len(known_values),
        "task_count": len(values),
    }


def summarize_dashboard(
    schedule: pd.DataFrame, costs: pd.DataFrame
) -> tuple[pd.DataFrame, dict]:
    """Count tasks by urgency and summarize overdue, upcoming, and last-service costs."""
    counts = schedule["due_status"].value_counts()
    status_counts = pd.DataFrame({
        "due_status": list(STATUS_COLORS),
        "task_count": [int(counts.get(status, 0)) for status in STATUS_COLORS],
    })
    # Comparisons produce a Boolean mask: one True/False value per task.
    overdue_mask = schedule["days_until_due"] < 0
    upcoming_mask = schedule["days_until_due"].between(0, 30, inclusive="both")
    # loc selects rows matching the mask. Pandas aligns indexes even after sorting.
    # Starting at day zero keeps overdue tasks out of the upcoming total.
    cost_summaries = {
        "overdue": summarize_costs(costs.loc[overdue_mask, "estimated_cost_usd"]),
        "upcoming": summarize_costs(costs.loc[upcoming_mask, "estimated_cost_usd"]),
        "last_service": summarize_costs(costs["actual_cost_usd"]),
    }
    return status_counts, cost_summaries


def summarize_category_costs(tasks: pd.DataFrame, costs: pd.DataFrame) -> pd.DataFrame:
    """Group tasks by category and total their known estimates using integer cents."""
    grouped_data = costs[["estimated_cost_usd"]].copy()
    if "category" in tasks.columns:
        grouped_data["category"] = tasks["category"].replace("", "Unknown")
    else:
        grouped_data["category"] = "Unknown"
    records = []
    # groupby collects related rows, similar to Java Collectors.groupingBy.
    for category, group in grouped_data.groupby("category", sort=True):
        summary = summarize_costs(group["estimated_cost_usd"])
        records.append({
            "category": category,
            "total_cents": summary["total_cents"],
            "known_count": summary["known_count"],
            "task_count": summary["task_count"],
        })
    # object dtype preserves exact Python integers and None in partial-cost datasets.
    return pd.DataFrame(
        records, columns=["category", "total_cents", "known_count", "task_count"], dtype=object
    )
