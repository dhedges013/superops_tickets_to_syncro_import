import main_SuperOpsTickets_import
import syncro_read
import syncro_utils
from main_SuperOpsTickets_import import (
    build_ticket_result,
    extract_assigned_tech,
    extract_contact_name,
    normalize_superops_ticket,
    process_individual_ticket,
)
from syncro_utils import get_next_available_syncro_ticket_number, get_syncro_status


def test_extract_contact_name_from_superops_to_users():
    to_users = [{"user": "Sally User"}]

    assert extract_contact_name(to_users) == "Sally User"


def test_extract_contact_name_returns_none_for_empty_list():
    assert extract_contact_name([]) is None


def test_normalize_superops_ticket_returns_string_fields_for_mapped_users():
    ticket = {
        "displayId": "SO-123",
        "ticketId": "123",
        "subject": "Printer issue",
        "status": "Open",
        "priority": "High",
        "createdTime": 1234567890,
        "notes": [],
        "conversations": [
            {
                "type": "DESCRIPTION",
                "time": 1,
                "content": "Initial description",
                "user": {"name": "Customer"},
            },
            {
                "type": "TECH_REPLY",
                "time": 2,
                "content": "Investigating",
                "user": {"name": "Tech One"},
                "toUsers": [{"user": "Sally User"}],
            },
        ],
    }

    normalized = normalize_superops_ticket(ticket)

    assert normalized["assigned_tech"] == "Tech One"
    assert normalized["contact"] == "Sally User"
    assert normalized["description"] == "Initial description"


def test_extract_assigned_tech_accepts_normalized_string():
    ticket_info = {"assigned_tech": "Tech One"}

    assert extract_assigned_tech("123", ticket_info) == "Tech One"


def test_build_ticket_result_creates_structured_outcome():
    result = build_ticket_result("Client A", "123", "SO-123", "created", syncro_ticket_id=42)

    assert result == {
        "customer": "Client A",
        "ticket_id": "123",
        "display_id": "SO-123",
        "result": "created",
        "reason": None,
        "syncro_ticket_id": 42,
        "comment_failures": 0,
        "comment_count": 0,
    }


def test_process_individual_ticket_classifies_missing_required_fields():
    ticket_info = {
        "displayId": "SO-123",
        "subject": None,
        "created_time": None,
    }

    result = process_individual_ticket("Client A", "123", ticket_info, [])

    assert result["result"] == "skipped_missing_required_fields"
    assert result["reason"] == "missing_subject_or_created_time"


def test_process_individual_ticket_classifies_duplicate_before_api_work():
    ticket_info = {
        "displayId": "SO-123",
        "subject": "Printer issue",
        "created_time": "2025-02-07T19:21:47-0500",
        "priority": "High",
        "assigned_tech": "Tech One",
        "description": "Initial description",
        "contact": "Sally User",
        "notes": [],
        "conversations": [],
    }

    result = process_individual_ticket("Client A", "123", ticket_info, ["SO-123"])

    assert result["result"] == "skipped_duplicate"
    assert result["reason"] == "matched_existing_syncro_ticket"


def test_process_individual_ticket_returns_would_create_in_dry_run(monkeypatch):
    ticket_info = {
        "displayId": "SO-123",
        "subject": "Printer issue",
        "created_time": "2025-02-07T19:21:47-0500",
        "priority": "High",
        "assigned_tech": "Tech One",
        "description": "Initial description",
        "contact": "Sally User",
        "notes": [],
        "conversations": [
            {"type": "DESCRIPTION", "content": "Initial description", "time": 1, "user": {"name": "Customer"}},
            {"type": "TECH_REPLY", "content": "Investigating", "time": 2, "user": {"name": "Tech One"}},
        ],
    }

    monkeypatch.setattr(main_SuperOpsTickets_import, "DRY_RUN", True)
    monkeypatch.setattr(
        main_SuperOpsTickets_import,
        "syncro_prepare_ticket_json_superops",
        lambda *args, **kwargs: {"subject": "Printer issue SO-123"},
    )

    result = process_individual_ticket("Client A", "123", ticket_info, [])

    assert result["result"] == "would_create"
    assert result["reason"] == "dry_run_preview"
    assert result["comment_count"] == 1


def test_process_individual_ticket_dry_run_skips_syncro_write_calls(monkeypatch):
    ticket_info = {
        "displayId": "SO-123",
        "subject": "Printer issue",
        "created_time": "2025-02-07T19:21:47-0500",
        "priority": "High",
        "assigned_tech": "Tech One",
        "description": "Initial description",
        "contact": "Sally User",
        "notes": [],
        "conversations": [],
    }

    monkeypatch.setattr(main_SuperOpsTickets_import, "DRY_RUN", True)
    monkeypatch.setattr(
        main_SuperOpsTickets_import,
        "syncro_prepare_ticket_json_superops",
        lambda *args, **kwargs: {"subject": "Printer issue SO-123"},
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("syncro_create_ticket should not be called during dry run")

    monkeypatch.setattr(main_SuperOpsTickets_import, "syncro_create_ticket", fail_if_called)

    result = process_individual_ticket("Client A", "123", ticket_info, [])

    assert result["result"] == "would_create"


def test_get_syncro_status_exact_match(monkeypatch):
    monkeypatch.setattr(
        syncro_utils,
        "load_or_fetch_temp_data",
        lambda *args, **kwargs: {"statuses": ["Open", "Resolved", "Pending"]},
    )

    assert get_syncro_status("Pending") == "Pending"


def test_get_syncro_status_uses_fallback_mapping(monkeypatch):
    monkeypatch.setattr(
        syncro_utils,
        "load_or_fetch_temp_data",
        lambda *args, **kwargs: {"statuses": ["Open", "Resolved"]},
    )

    assert get_syncro_status("Closed") == "Resolved"


def test_get_syncro_status_uses_default_when_no_statuses_available(monkeypatch):
    monkeypatch.setattr(
        syncro_utils,
        "load_or_fetch_temp_data",
        lambda *args, **kwargs: {"statuses": []},
    )

    assert get_syncro_status("Anything", default_status="Resolved") == "Resolved"


def test_get_next_available_syncro_ticket_number_uses_display_id_when_free(monkeypatch):
    monkeypatch.setattr(syncro_utils, "get_syncro_ticket_number", lambda value: str(value))

    def fake_get_ticket_by_number(number):
        return None

    monkeypatch.setattr(
        syncro_read,
        "get_syncro_ticket_by_number",
        fake_get_ticket_by_number,
    )

    assert get_next_available_syncro_ticket_number("12345") == "12345"


def test_get_next_available_syncro_ticket_number_increments_when_taken(monkeypatch):
    monkeypatch.setattr(syncro_utils, "get_syncro_ticket_number", lambda value: str(value))

    def fake_get_ticket_by_number(number):
        if number == "12345":
            return {"id": 1, "number": "12345"}
        return None

    monkeypatch.setattr(
        syncro_read,
        "get_syncro_ticket_by_number",
        fake_get_ticket_by_number,
    )

    assert get_next_available_syncro_ticket_number("12345") == "12346"
