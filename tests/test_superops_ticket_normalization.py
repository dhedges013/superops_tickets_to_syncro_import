import main_SuperOpsTickets_import
import syncro_read
import syncro_utils
from main_SuperOpsTickets_import import (
    FatalImportValidationError,
    build_ticket_result,
    build_and_validate_historical_comments,
    extract_assigned_tech,
    extract_contact_name,
    normalize_superops_ticket,
    process_individual_ticket,
)
from syncro_utils import (
    get_next_available_syncro_ticket_number,
    get_syncro_created_date,
    get_syncro_status,
    syncro_prepare_ticket_json_superops,
)


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
            {
                "type": "DESCRIPTION",
                "content": "Initial description",
                "time": "2025-02-07T19:21:47.000",
                "user": {"name": "Customer"},
            },
            {
                "type": "TECH_REPLY",
                "content": "Investigating",
                "time": "2025-02-07T19:25:47.000",
                "user": {"name": "Tech One"},
            },
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
    assert result["comment_count"] == 2


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


def test_syncro_prepare_ticket_json_superops_excludes_embedded_initial_issue(monkeypatch):
    monkeypatch.setattr(syncro_utils, "get_customer_id_by_name", lambda value: 1)
    monkeypatch.setattr(syncro_utils, "get_next_available_syncro_ticket_number", lambda value: "12345")
    monkeypatch.setattr(syncro_utils, "get_syncro_tech", lambda value: 99)
    monkeypatch.setattr(syncro_utils, "get_syncro_created_date", lambda value: value)
    monkeypatch.setattr(syncro_utils, "get_syncro_customer_contact", lambda customer_id, contact: 55)
    monkeypatch.setattr(syncro_utils, "get_syncro_priority", lambda value: "High")

    payload = syncro_prepare_ticket_json_superops(
        "Client A",
        "Sally User",
        "SO-123",
        "Printer issue SO-123",
        "2025-02-07T19:21:47-0500",
        "Resolved",
        "High",
        "Tech One",
        "Initial description",
        [],
    )

    assert payload["created_at"] == "2025-02-07T19:21:47-0500"
    assert "comments_attributes" not in payload


def test_get_syncro_created_date_converts_from_configured_source_timezone(monkeypatch):
    monkeypatch.setattr(syncro_utils, "SUPEROPS_SOURCE_TIMEZONE", "America/Chicago")
    monkeypatch.setattr(syncro_utils, "SYNCRO_TIMEZONE", "America/New_York")

    assert get_syncro_created_date("2025-07-17T15:43:18.255") == "2025-07-17T16:43:18-0400"


def test_build_and_validate_historical_comments_raises_for_comment_before_ticket():
    timeline = [
        {
            "type": "TECH_REPLY",
            "content": "Investigating",
            "time": "2025-07-17T15:30:00.000",
            "user": "Tech One",
        }
    ]

    try:
        build_and_validate_historical_comments(
            "Client A",
            "123",
            "SO-123",
            "2025-07-17T16:43:18-0400",
            "Initial description",
            "Sally User",
            timeline,
        )
    except FatalImportValidationError as exc:
        assert "precedes ticket creation" in str(exc)
    else:
        raise AssertionError("Expected FatalImportValidationError for out-of-order timestamps")


def test_process_individual_ticket_creates_initial_issue_comment_first(monkeypatch):
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
    created_comments = []

    monkeypatch.setattr(main_SuperOpsTickets_import, "DRY_RUN", False)
    monkeypatch.setattr(main_SuperOpsTickets_import, "get_syncro_created_date", lambda value: value)
    monkeypatch.setattr(main_SuperOpsTickets_import, "get_syncro_status", lambda value, default_status=None: "Resolved")
    monkeypatch.setattr(
        main_SuperOpsTickets_import,
        "extract_notes_and_conversations",
        lambda ticket_id, ticket_info: (["note"], ["conversation"]),
    )
    monkeypatch.setattr(
        main_SuperOpsTickets_import,
        "combine_notes_and_conversations",
        lambda notes, conversations: [
            {"type": "TECH_REPLY", "content": "Investigating", "time": "2025-02-07T19:30:00.000", "user": "Tech One"}
        ],
    )
    monkeypatch.setattr(
        main_SuperOpsTickets_import,
        "syncro_prepare_ticket_json_superops",
        lambda *args, **kwargs: {"subject": "Printer issue SO-123", "created_at": "2025-02-07T19:21:47-0500"},
    )
    monkeypatch.setattr(
        main_SuperOpsTickets_import,
        "syncro_create_ticket",
        lambda payload: {"ticket": {"id": 42, "number": "T42"}},
    )

    def fake_syncro_create_comment(payload, created_ticket_id):
        created_comments.append((payload, created_ticket_id))
        return {"comment": {"id": len(created_comments)}}

    monkeypatch.setattr(main_SuperOpsTickets_import, "syncro_create_comment", fake_syncro_create_comment)

    result = process_individual_ticket("Client A", "123", ticket_info, [])

    assert result["result"] == "created"
    assert result["comment_count"] == 2
    assert created_comments[0][0]["subject"] == "initial issue"
    assert created_comments[0][0]["created_at"] == "2025-02-07T19:21:47-0500"
    assert created_comments[0][1] == 42
    assert created_comments[1][0]["subject"] == "TECH_REPLY"


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
