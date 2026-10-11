"""HomeCare IQ, Step 6: explore, edit, and explicitly save maintenance tasks."""

# Imports make library code available, similar to imports in Java.
import plotly.express as px
import streamlit as st

# Import our function from maintenance.py; a Python file is called a module.
from maintenance import (
    MAX_UPLOAD_MB,
    SAVED_CSV_PATH,
    SOURCE_COLUMNS,
    STATUS_COLORS,
    calculate_schedule,
    format_usd,
    maintenance_today,
    propose_due_date,
    read_csv_data,
    read_sample_data,
    read_saved_data,
    save_data,
    summarize_category_costs,
    summarize_dashboard,
    update_task,
    validate_data,
    validation_issue,
)


def style_due_status(status: str) -> str:
    """Give the status cell a colored background while retaining its text label."""
    # Pandas calls this function for each status cell. The result is a CSS style.
    return f"background-color: {STATUS_COLORS[status]}; color: white; font-weight: bold;"


def show_cost_summary(label: str, summary: dict) -> None:
    """Display one cost total with its coverage, without adding another task KPI card."""
    st.write(label)
    if summary["task_count"] == 0:
        st.write("No tasks in this group.")
    else:
        st.markdown(f"**{format_usd(summary['total_cents'])}**")
        st.caption(
            f"Costs recorded for {summary['known_count']} of {summary['task_count']} tasks."
        )


def accept_dataset(data, cents, source: str, warnings: list) -> None:
    """Replace session data only after validation (and writing, when saving) succeeds."""
    st.session_state["accepted_data"] = data
    st.session_state["accepted_costs_cents"] = cents
    st.session_state["accepted_source"] = source
    st.session_state["validation_source"] = source
    st.session_state["validation_errors"] = []
    st.session_state["validation_warnings"] = warnings
    # A new version resets task widgets, even if an uploaded file reuses task IDs.
    st.session_state["dataset_revision"] += 1


def save_candidate(candidate, success_message: str) -> None:
    """Validate an explicit submission, write it, then update the active session."""
    data, cents, errors, warnings = validate_data(candidate)
    if errors:
        st.error("Changes were not saved. Correct the errors and try again.")
        st.dataframe(errors, width="stretch", hide_index=True)
        return
    try:
        data, cents, warnings = save_data(data)
    except (OSError, ValueError) as error:
        st.error(f"Could not save the dataset: {error}")
        return
    # Do not claim success or replace accepted data until the write has completed.
    accept_dataset(data, cents, "data/saved_tasks.csv", warnings)
    st.session_state["save_success"] = success_message
    # Refresh cards, table, and download bytes. The submit button is false on this rerun.
    st.rerun()


def show_edit_form(original: dict, fields: list, revision: int, task_id: str) -> None:
    """Collect source edits and prepare a draft, without writing anything."""
    st.write("1. Enter changes, then review the proposed due date.")
    st.caption(
        "Dates: YYYY-MM-DD. Frequency: whole days. Priority: High, Medium, Low, or blank. "
        "Costs: plain USD (for example 31.43); leave blank for unknown."
    )
    values = st.session_state["edit_values"]
    with st.form(f"edit_task_{revision}_{task_id}", enter_to_submit=False):
        st.text_input("Task ID", value=task_id, disabled=True)
        changes = {"task_id": task_id}
        for field in fields:
            label = field.replace("_", " ").title()
            if field == "actual_cost_usd":
                label = "Last-service cost (actual_cost_usd)"
            key = f"edit_{revision}_{task_id}_{field}"
            initial_value = values.get(field, "")
            if field == "notes" or "\n" in initial_value:
                value = st.text_area(label, value=initial_value, key=key)
            else:
                value = st.text_input(label, value=initial_value, key=key)
            # Text inputs preserve blanks instead of inventing zero-valued dates/costs.
            changes[field] = value.strip()
        review = st.form_submit_button("Review changes")
    if review:
        proposed, message = propose_due_date(original, changes)
        if proposed is not None:
            # Change only the draft. The user can override this proposal before saving.
            changes["next_due_date"] = proposed
        st.session_state["edit_draft"] = changes
        st.session_state["edit_message"] = message
        st.rerun()


def show_review_form(tasks, original: dict, fields: list, saving_blocked: bool) -> None:
    """Review the draft, allow a due-date override, and save only on explicit submission."""
    draft = st.session_state["edit_draft"]
    task_id = original["task_id"]
    revision = st.session_state["dataset_revision"]
    st.write("2. Review your changes and confirm the due date.")
    st.info(st.session_state["edit_message"])
    comparison = [
        {"Field": field, "Current value": original.get(field, ""),
         "Reviewed value": draft.get(field, "")}
        for field in fields
    ]
    st.dataframe(comparison, width="stretch", hide_index=True)
    with st.form(f"save_task_{revision}_{task_id}", enter_to_submit=False):
        due_date = st.text_input(
            "Next due date — confirm or override (YYYY-MM-DD)",
            value=draft.get("next_due_date", ""),
            help="Blank uses service date + frequency when both are valid.",
        )
        save_changes = st.form_submit_button("Save changes", disabled=saving_blocked)
        back_to_editing = st.form_submit_button("Back to editing")
    if save_changes:
        changes = draft.copy()
        changes["next_due_date"] = due_date.strip()
        # Build and validate a copy. A failed edit leaves accepted data untouched.
        candidate = update_task(tasks, task_id, changes)
        save_candidate(candidate, f"Changes to {task_id} saved to data/saved_tasks.csv.")
    if back_to_editing:
        values = draft.copy()
        values["next_due_date"] = due_date.strip()
        st.session_state["edit_values"] = values
        st.session_state["edit_draft"] = None
        st.rerun()


def show_task_editor(tasks, saving_blocked: bool) -> None:
    """Select a task and display either its edit form or its review form."""
    st.subheader("Edit a task")
    revision = st.session_state["dataset_revision"]
    # A dictionary comprehension builds a Map of IDs to readable task labels.
    task_labels = {
        task["task_id"]: f"{task['task_id']} — {task['task_name']}"
        for task in tasks.to_dict(orient="records")
    }
    task_id = st.selectbox(
        "Task to edit", list(task_labels), format_func=task_labels.get,
        key=f"task_selector_{revision}",
    )
    original = tasks.loc[tasks["task_id"].eq(task_id)].iloc[0].to_dict()
    # Keep a reviewed draft only for this task and this accepted dataset version.
    context = (revision, task_id)
    if st.session_state.get("editor_context") != context:
        st.session_state["editor_context"] = context
        st.session_state["edit_values"] = original.copy()
        st.session_state["edit_draft"] = None

    fields = [field for field in tasks.columns if field != "task_id"]
    fields += [field for field in SOURCE_COLUMNS if field not in tasks.columns]
    st.caption("Task ID is read-only. Saving edits saves the entire active dataset.")
    if SAVED_CSV_PATH.exists():
        st.warning("Save changes replaces data/saved_tasks.csv with the active dataset; no merge.")
    if saving_blocked:
        st.error("Saving is blocked while the existing saved CSV is invalid or unreadable.")

    if st.session_state["edit_draft"] is None:
        show_edit_form(original, fields, revision, task_id)
    else:
        show_review_form(tasks, original, fields, saving_blocked)


# Streamlit runs this script from top to bottom to build the page.
st.set_page_config(page_title="HomeCare IQ", layout="wide")
st.title("My HomeCare IQ")
st.write("Explore your home maintenance tasks. Start by loading the sample data.")

# Streamlit reruns the script after interactions. Session state is a dictionary
# that keeps values between those reruns, similar to browser-session storage.
# It is memory only: refreshing the browser can start a new session.
if "accepted_data" not in st.session_state:
    st.session_state["accepted_data"] = None
    st.session_state["accepted_costs_cents"] = None
    st.session_state["accepted_source"] = ""
    st.session_state["validation_source"] = ""
    st.session_state["validation_errors"] = []
    st.session_state["validation_warnings"] = []

# Separate defaults let an already-open Step 5 session pick up the new features.
if "dataset_revision" not in st.session_state:
    st.session_state["dataset_revision"] = 0

# Reading is safe on reruns. Accept the saved snapshot only once per browser session,
# so later uploads/edits are not replaced by the startup file on each interaction.
saved_exists = True
saved_errors = []
saved_data = None
saved_cents = None
saved_warnings = []
try:
    saved_data = read_saved_data()
    saved_data, saved_cents, saved_errors, saved_warnings = validate_data(saved_data)
except FileNotFoundError:
    saved_exists = False
except (OSError, ValueError) as error:
    saved_errors = [validation_issue(None, "saved CSV", str(error))]

if "startup_data_checked" not in st.session_state:
    st.session_state["startup_data_checked"] = True
    if saved_data is not None and not saved_errors and st.session_state["accepted_data"] is None:
        accept_dataset(saved_data, saved_cents, "data/saved_tasks.csv", saved_warnings)

# This message is set only after a successful write, then consumed on the next rerun.
if "save_success" in st.session_state:
    st.success(st.session_state.pop("save_success"))

# st.tabs returns three containers. Assign each one to a descriptive variable.
upload_tab, status_tab, due_tab = st.tabs(
    ["Upload Data", "Current Status", "Due Tracker"]
)

# with directs the indented interface code into this tab's container.
with upload_tab:
    st.subheader("Upload and validate maintenance data")
    st.write(
        "Use a UTF-8 CSV with task_id, task_name, and either next_due_date "
        "or both last_service_date and frequency_days. Dates use YYYY-MM-DD; "
        "costs use plain USD numbers such as 31.43."
    )
    st.caption(
        "Maximum 10 MB and 10,000 tasks. Loading or uploading changes only this session. "
        "Use Save dataset or Save changes to keep data after closing the app."
    )
    uploaded_file = st.file_uploader(
        "Choose a maintenance CSV", type="csv", max_upload_size=MAX_UPLOAD_MB
    )
    use_upload = st.button("Validate and use CSV", disabled=uploaded_file is None)
    use_sample = st.button("Load sample data")

    # Only these explicit actions try to replace the accepted dataset.
    if use_upload or use_sample:
        source = "homecare_maintenance_sample.csv" if use_sample else uploaded_file.name
        st.session_state["validation_source"] = source
        st.session_state["validation_errors"] = []
        st.session_state["validation_warnings"] = []
        # try/except handles expected errors, similar to Java's try/catch.
        try:
            if use_sample:
                candidate = read_sample_data()
            else:
                candidate = read_csv_data(uploaded_file.getvalue())
        except FileNotFoundError:
            st.session_state["validation_errors"] = [validation_issue(
                None, "file", "Sample CSV not found. Place it beside app.py and maintenance.py."
            )]
        except OSError:
            st.session_state["validation_errors"] = [validation_issue(
                None, "file", "The CSV could not be opened. Check the file and access permissions."
            )]
        except ValueError as error:
            st.session_state["validation_errors"] = [validation_issue(None, "file", str(error))]
        else:
            data, cents, errors, warnings = validate_data(candidate)
            st.session_state["validation_errors"] = errors
            st.session_state["validation_warnings"] = warnings
            # An invalid candidate never replaces the previously accepted data.
            if not errors:
                accept_dataset(data, cents, source, warnings)

    errors = st.session_state["validation_errors"]
    warnings = st.session_state["validation_warnings"]
    if st.session_state["validation_source"]:
        st.caption("Validation result for: " + st.session_state["validation_source"])
    if errors:
        st.error("This CSV was not accepted. Correct the errors and validate it again.")
        st.dataframe(errors, width="stretch", hide_index=True)
    if warnings:
        st.warning("Review these warnings. Warnings alone do not block acceptance.")
        st.dataframe(warnings, width="stretch", hide_index=True)
    if errors or warnings:
        st.caption(
            "Row numbers refer to CSV line numbers. File means a problem with the whole file."
        )

    data = st.session_state["accepted_data"]
    if data is not None:
        st.subheader("Accepted dataset preview")
        st.caption("Active source: " + st.session_state["accepted_source"])
        if errors:
            st.info("Your previously accepted dataset is still active below.")
        st.success(f"Loaded {len(data)} tasks with {len(data.columns)} source columns.")
        # Change display labels on a copy, preserving blank source values.
        preview = data.copy()
        for field in ("category", "location"):
            if field not in preview.columns:
                preview[field] = "Unknown"
            else:
                preview[field] = preview[field].replace("", "Unknown")
        if "priority" in preview.columns:
            preview["priority"] = preview["priority"].replace("", "Unspecified")
        st.dataframe(preview, width="stretch", hide_index=True)
    else:
        st.info("Load the sample or validate an uploaded CSV to see an accepted dataset.")

    st.subheader("Saved dataset")
    if saved_errors:
        st.error(
            "The existing saved CSV is invalid or unreadable. It has not been overwritten. "
            "Correct the file before saving. You can still load the sample or an upload."
        )
        st.dataframe(saved_errors, width="stretch", hide_index=True)
    if saved_exists:
        st.warning(
            "Save dataset replaces the entire data/saved_tasks.csv snapshot with the active "
            "dataset, including any uploaded data. Tasks are not merged."
        )
    else:
        st.caption("Save dataset creates data/saved_tasks.csv for the first time.")
    if st.button("Save dataset", disabled=data is None or bool(saved_errors)):
        save_candidate(data, "Dataset saved to data/saved_tasks.csv.")
    if saved_exists and not saved_errors:
        try:
            # Export the file on disk, rather than unsaved uploaded data or an edit draft.
            saved_bytes = SAVED_CSV_PATH.read_bytes()
        except OSError as error:
            st.error(f"Could not read the saved CSV for download: {error}")
        else:
            st.download_button(
                "Download saved CSV", data=saved_bytes, file_name="saved_tasks.csv",
                mime="text/csv", on_click="ignore",
            )
    elif not saved_exists:
        st.info("Save a dataset to make Download saved CSV available.")

# Use one fresh schedule for both tabs, so cards and tracker share the same date.
data = st.session_state["accepted_data"]
as_of_date = maintenance_today()
schedule = None
schedule_warnings = []
schedule_error = None
if data is not None and not data.empty:
    try:
        schedule, schedule_warnings = calculate_schedule(data, as_of_date=as_of_date)
    except (ValueError, OverflowError) as error:
        schedule_error = str(error)

with status_tab:
    st.subheader("Current Status")
    st.caption(f"Status as of {as_of_date.isoformat()} · America/New_York")
    if data is None or data.empty:
        st.info("Load the sample or validate a CSV in Upload Data to see your summaries.")
    elif schedule_error is not None:
        st.error(f"Could not calculate summaries: {schedule_error} Validate the dataset again.")
    else:
        costs = st.session_state["accepted_costs_cents"]
        status_counts, cost_summaries = summarize_dashboard(schedule, costs)
        counts = status_counts.set_index("due_status")["task_count"]

        # Exactly four task cards; costs are shown as separate text summaries below.
        total_card, overdue_card, soon_card, upcoming_card = st.columns(4)
        total_card.metric("Total tasks", len(schedule))
        overdue_card.metric("Overdue", int(counts["Overdue"]))
        soon_card.metric("Due in 0–7 days", int(counts["Due soon"]))
        upcoming_card.metric("Due in 8–30 days", int(counts["Upcoming"]))

        st.subheader("Maintenance costs")
        overdue_cost, upcoming_cost, last_service_cost = st.columns(3)
        with overdue_cost:
            show_cost_summary("Estimated overdue cost", cost_summaries["overdue"])
        with upcoming_cost:
            show_cost_summary("Estimated cost due in 0–30 days", cost_summaries["upcoming"])
        with last_service_cost:
            show_cost_summary("Recorded last-service cost total", cost_summaries["last_service"])
        st.caption(
            "Upcoming estimates exclude overdue tasks. Totals include recorded costs only; "
            "blank costs remain unknown. Last-service costs are the latest recorded service "
            "amount for each task."
        )

        # Plotly Express builds a figure from a small summary DataFrame.
        urgency_chart = px.bar(
            status_counts,
            x="due_status",
            y="task_count",
            color="due_status",
            color_discrete_map=STATUS_COLORS,
            category_orders={"due_status": list(STATUS_COLORS)},
            text="task_count",
            title="Task counts by urgency",
            labels={"due_status": "Urgency", "task_count": "Tasks"},
        )
        urgency_chart.update_layout(showlegend=False)
        urgency_chart.update_yaxes(dtick=1)
        st.plotly_chart(urgency_chart, width="stretch", key="urgency_chart")

        category_costs = summarize_category_costs(data, costs)
        # notna marks categories with known totals; all-unknown groups get no zero bar.
        known_categories = category_costs[category_costs["total_cents"].notna()].copy()
        unknown_categories = category_costs.loc[
            category_costs["total_cents"].isna(), "category"
        ].tolist()
        if unknown_categories:
            st.info("No recorded estimates for these categories: " + ", ".join(unknown_categories))

        # Dollar conversion is only for chart display; all sums above used integer cents.
        known_categories["estimated_cost_usd"] = [
            cents / 100 for cents in known_categories["total_cents"]
        ]
        known_categories["display_amount"] = [
            format_usd(cents) for cents in known_categories["total_cents"]
        ]
        category_chart = px.bar(
            known_categories,
            x="category",
            y="estimated_cost_usd",
            text="display_amount",
            hover_data={"estimated_cost_usd": ":.2f", "known_count": True, "task_count": True},
            title="Known estimated costs by category",
            labels={
                "category": "Category",
                "estimated_cost_usd": "Estimated cost (USD)",
                "known_count": "Tasks with estimates",
                "task_count": "Tasks in category",
                "display_amount": "Known total",
            },
        )
        category_chart.update_traces(textposition="outside", cliponaxis=False)
        category_chart.update_yaxes(tickprefix="$", tickformat=",.2f", rangemode="tozero")
        if known_categories.empty:
            category_chart.add_annotation(
                text="No estimated costs are recorded yet.",
                x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False,
            )
        st.plotly_chart(category_chart, width="stretch", key="category_cost_chart")
        st.caption(
            "Category totals cover each task's next scheduled occurrence. "
            "Hover over a bar to see how many tasks have a recorded estimate."
        )

with due_tab:
    st.subheader("Due Tracker")
    # The shared schedule above is recalculated on every script run.
    st.caption(f"Status as of {as_of_date.isoformat()} · America/New_York")
    st.write(
        "Red: overdue · Orange: due today through 7 days · "
        "Blue: due in 8–30 days · Green: due later"
    )
    st.caption(
        "Due dates use the supplied date, or service date + frequency when blank. "
        "Tasks sort by due date, then High/Medium/Low priority, then task ID."
    )
    if data is None or data.empty:
        st.info("Load the sample or validate a CSV in Upload Data to see your due tracker.")
    elif schedule_error is not None:
        st.error(f"Could not calculate due dates: {schedule_error} Validate the dataset again.")
    else:
        if schedule_warnings:
            st.warning("Some supplied due dates differ from their calculated dates.")
            st.dataframe(schedule_warnings, width="stretch", hide_index=True)

        # Build a display table from separate derived fields. Source data stays intact.
        tracker = schedule[[
            "task_id", "due_status", "effective_due_date", "days_until_due"
        ]].copy()
        for field in (
            "task_name", "priority", "category", "location", "estimated_cost_usd", "vendor"
        ):
            if field in data.columns:
                # Pandas aligns by row index, even though the schedule is now sorted.
                tracker[field] = data[field]
            else:
                tracker[field] = ""
        for field in ("category", "location", "estimated_cost_usd"):
            tracker[field] = tracker[field].replace("", "Unknown")
        tracker["priority"] = tracker["priority"].replace("", "Unspecified")
        tracker = tracker[[
            "task_id", "task_name", "due_status", "effective_due_date", "days_until_due",
            "priority", "category", "location", "estimated_cost_usd", "vendor",
        ]]
        # Styler.map applies our color function to status cells only.
        st.dataframe(
            tracker.style.map(style_due_status, subset=["due_status"]),
            width="stretch",
            hide_index=True,
            column_config={
                "task_id": "Task ID",
                "task_name": "Task",
                "due_status": "Status",
                "effective_due_date": st.column_config.DateColumn(
                    "Due date", format="YYYY-MM-DD"
                ),
                "days_until_due": st.column_config.NumberColumn(
                    "Days until due", format="%d"
                ),
                "priority": "Priority",
                "category": "Category",
                "location": "Location",
                "estimated_cost_usd": "Estimated cost (USD)",
                "vendor": "Vendor",
            },
        )

        show_task_editor(data, saving_blocked=bool(saved_errors))
