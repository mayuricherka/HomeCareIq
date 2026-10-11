"""Small behavior tests; every saved CSV lives in pytest's temporary directory."""

from datetime import date

import pandas as pd
import pytest

import maintenance


AS_OF_DATE = date(2026, 10, 9)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    """Freeze the date, including validation performed inside save_data."""
    # autouse runs this setup before every test, like a JUnit @BeforeEach method.
    # monkeypatch restores the real function after each test automatically.
    def today():
        return AS_OF_DATE

    monkeypatch.setattr(maintenance, "maintenance_today", today)


@pytest.fixture
def tasks():
    """Give each test its own small source table, without reading personal data."""
    # pytest supplies this fixture when a test declares a tasks parameter.
    return pd.DataFrame([{
        "task_id": "001",
        "task_name": "Filter replacement",
        "category": "HVAC",
        "last_service_date": "2026-09-01",
        "frequency_days": "30",
        "next_due_date": "2026-10-01",
        "estimated_cost_usd": "31.43",
        "actual_cost_usd": "0",
        "notes": "Check the filter",
        "owner_note": "Retain this unused source column",
    }], dtype=str)


# Like JUnit @ParameterizedTest: one test function runs once for each input row.
@pytest.mark.parametrize("days, expected_status, expected_color", [
    (-1, "Overdue", "#b91c1c"),
    (0, "Due soon", "#c2410c"),
    (7, "Due soon", "#c2410c"),
    (8, "Upcoming", "#1d4ed8"),
    (30, "Upcoming", "#1d4ed8"),
    (31, "Later", "#15803d"),
])
def test_urgency_boundaries(days, expected_status, expected_color):
    status, color = maintenance.calculate_due_status(days)
    # assert is pytest's usual equivalent of JUnit assertEquals/assertTrue.
    assert status == expected_status
    assert color == expected_color


def test_blank_due_date_uses_service_date_and_frequency(tasks):
    tasks.loc[0, "next_due_date"] = ""
    schedule, warnings = maintenance.calculate_schedule(tasks, as_of_date=AS_OF_DATE)
    assert schedule.loc[0, "effective_due_date"] == date(2026, 10, 1)
    assert schedule.loc[0, "days_until_due"] == -8
    assert warnings == []
    assert tasks.loc[0, "next_due_date"] == ""  # Derived dates must not change source data.


def test_explicit_due_date_wins_when_sources_disagree(tasks):
    tasks.loc[0, "next_due_date"] = "2026-10-05"
    schedule, warnings = maintenance.calculate_schedule(tasks, as_of_date=AS_OF_DATE)
    assert schedule.loc[0, "effective_due_date"] == date(2026, 10, 5)
    assert len(warnings) == 1
    assert warnings[0]["Field"] == "next_due_date"


def test_malformed_due_date_does_not_use_fallback(tasks):
    task = tasks.iloc[0].to_dict()
    task["next_due_date"] = "10/01/2026"
    # Like JUnit assertThrows: this block must raise the specified exception.
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        maintenance.resolve_due_date(task)


def test_duplicate_ids_report_both_rows(tasks):
    duplicated = pd.concat([tasks, tasks], ignore_index=True)
    _, _, errors, _ = maintenance.validate_data(duplicated, today=AS_OF_DATE)
    duplicate_errors = [issue for issue in errors if issue["Field"] == "task_id"]
    assert len(duplicate_errors) == 2
    assert {issue["Row"] for issue in duplicate_errors} == {"2", "3"}


@pytest.mark.parametrize("field, value", [
    ("last_service_date", "2026-02-30"),  # Correct format, impossible calendar date.
    ("next_due_date", "10/01/2026"),
    ("warranty_expiry_date", "2026-13-01"),
    ("last_service_date", "2026-10-10"),  # Future relative to our fixed date.
    ("next_due_date", "2026-08-31"),     # Before the last service.
])
def test_invalid_dates_report_the_field(tasks, field, value):
    tasks.loc[0, field] = value
    _, _, errors, _ = maintenance.validate_data(tasks, today=AS_OF_DATE)
    assert any(issue["Field"] == field and issue["Row"] == "2" for issue in errors)


@pytest.mark.parametrize("field, value", [
    ("estimated_cost_usd", "-0.01"),
    ("estimated_cost_usd", "NaN"),
    ("estimated_cost_usd", "Infinity"),
    ("estimated_cost_usd", "$12.00"),
    ("estimated_cost_usd", "not-a-number"),
    ("actual_cost_usd", "12.345"),
])
def test_invalid_costs_report_the_field(tasks, field, value):
    tasks.loc[0, field] = value
    _, _, errors, _ = maintenance.validate_data(tasks, today=AS_OF_DATE)
    assert any(issue["Field"] == field and issue["Row"] == "2" for issue in errors)


def test_last_service_cost_requires_a_service_date(tasks):
    tasks.loc[0, "last_service_date"] = ""
    # Even an explicitly recorded zero is a last-service cost needing a service date.
    _, _, errors, _ = maintenance.validate_data(tasks, today=AS_OF_DATE)
    assert any(issue["Field"] == "actual_cost_usd" for issue in errors)


def test_unknown_and_zero_costs_have_different_coverage(tasks):
    tasks.loc[0, "estimated_cost_usd"] = ""
    zero_task = tasks.iloc[0].to_dict()
    zero_task.update(task_id="002", estimated_cost_usd="0")
    candidate = pd.DataFrame([tasks.iloc[0].to_dict(), zero_task], dtype=str)
    _, cents, errors, _ = maintenance.validate_data(candidate, today=AS_OF_DATE)
    assert errors == []
    assert cents.loc[0, "estimated_cost_usd"] is None
    assert cents.loc[1, "estimated_cost_usd"] == 0
    assert maintenance.summarize_costs(cents["estimated_cost_usd"]) == {
        "total_cents": 0, "known_count": 1, "task_count": 2,
    }
    assert maintenance.format_usd(None) == "Unknown"
    assert maintenance.format_usd(0) == "$0.00"


def test_all_unknown_costs_have_an_unknown_total():
    unknown_costs = pd.Series([None, None], dtype=object)
    assert maintenance.summarize_costs(unknown_costs) == {
        "total_cents": None, "known_count": 0, "task_count": 2,
    }


def test_category_with_no_estimates_has_no_zero_total(tasks):
    tasks.loc[0, "estimated_cost_usd"] = ""
    data, cents, errors, _ = maintenance.validate_data(tasks, today=AS_OF_DATE)
    assert errors == []
    categories = maintenance.summarize_category_costs(data, cents).set_index("category")
    assert categories.loc["HVAC", "total_cents"] is None
    assert categories.loc["HVAC", "known_count"] == 0
    assert categories.loc["HVAC", "task_count"] == 1


def test_exact_sample_totals_and_counts():
    # The original sample is read only. Personal data/saved_tasks.csv is never read here.
    sample = maintenance.read_sample_data()
    data, cents, errors, _ = maintenance.validate_data(sample, today=AS_OF_DATE)
    assert errors == []
    schedule, warnings = maintenance.calculate_schedule(data, as_of_date=AS_OF_DATE)
    assert warnings == []
    counts, summaries = maintenance.summarize_dashboard(schedule, cents)
    assert len(data) == 18
    assert counts.set_index("due_status")["task_count"].to_dict() == {
        "Overdue": 3, "Due soon": 2, "Upcoming": 2, "Later": 11,
    }
    # Dollar expectations expressed as exact integer cents, not approximate floats.
    assert maintenance.summarize_costs(cents["estimated_cost_usd"])["total_cents"] == 102_300
    assert summaries["last_service"]["total_cents"] == 103_351
    assert summaries["overdue"]["total_cents"] == 7_500
    assert summaries["upcoming"]["total_cents"] == 21_200  # Excludes overdue estimates.
    assert summaries["overdue"]["known_count"] == summaries["overdue"]["task_count"] == 3
    assert summaries["upcoming"]["known_count"] == summaries["upcoming"]["task_count"] == 4
    assert summaries["last_service"]["known_count"] == 18


def test_save_and_reload_preserves_source_fields(tmp_path, tasks):
    # tmp_path is a unique pytest-managed directory, like JUnit @TempDir.
    # ALWAYS pass path explicitly; save_data's default points to the real saved dataset.
    path = tmp_path / "data" / "saved_tasks.csv"
    tasks.loc[0, "notes"] = 'Check "seal", then replace filter.\nCafé supplies'
    maintenance.save_data(tasks, path=path)
    reloaded = maintenance.read_saved_data(path=path)
    assert list(reloaded.columns) == list(tasks.columns)  # Source columns only, including extras.
    assert reloaded.to_dict(orient="records") == tasks.to_dict(orient="records")
    assert reloaded.loc[0, "task_id"] == "001"
    assert reloaded.loc[0, "estimated_cost_usd"] == "31.43"
    _, cents, errors, _ = maintenance.validate_data(reloaded, today=AS_OF_DATE)
    assert errors == []
    assert cents.loc[0, "estimated_cost_usd"] == 3_143


def test_validation_failure_preserves_saved_file(tmp_path, tasks):
    path = tmp_path / "saved_tasks.csv"
    maintenance.save_data(tasks, path=path)
    previous_bytes = path.read_bytes()
    invalid = tasks.copy()
    invalid.loc[0, "estimated_cost_usd"] = "-1"
    with pytest.raises(ValueError, match="estimated_cost_usd"):
        maintenance.save_data(invalid, path=path)
    assert path.read_bytes() == previous_bytes


def test_failed_replacement_preserves_saved_file(tmp_path, tasks, monkeypatch):
    path = tmp_path / "saved_tasks.csv"
    maintenance.save_data(tasks, path=path)
    previous_bytes = path.read_bytes()
    changed = tasks.copy()
    changed.loc[0, "notes"] = "A change that must not reach the saved file"

    def fail_replace(source, destination):
        # Verify the draft is on the same filesystem before simulating a failure.
        assert source.parent == path.parent
        assert destination == path
        assert source.exists()
        raise OSError("Simulated replacement failure")

    # Similar to a temporary Mockito stub; pytest restores os.replace afterward.
    monkeypatch.setattr(maintenance.os, "replace", fail_replace)
    with pytest.raises(OSError, match="Simulated replacement failure"):
        maintenance.save_data(changed, path=path)
    assert path.read_bytes() == previous_bytes
    assert list(tmp_path.glob(".saved_tasks-*.tmp")) == []


def test_invalid_existing_snapshot_is_not_overwritten(tmp_path, tasks):
    path = tmp_path / "saved_tasks.csv"
    original_bytes = b"task_id,task_name,next_due_date\n001,Filter,bad-date\n"
    path.write_bytes(original_bytes)
    with pytest.raises(ValueError, match="existing saved CSV is invalid"):
        maintenance.save_data(tasks, path=path)
    assert path.read_bytes() == original_bytes
