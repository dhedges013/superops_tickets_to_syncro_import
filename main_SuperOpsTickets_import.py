import requests
import time
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup  # Import BeautifulSoup for HTML stripping
import pytz
from syncro_configs import (
    get_logger,
    get_chronology_logger,
    RATE_LIMIT_SECONDS,
    SUPEROPS_SOURCE_TIMEZONE,
)  # Import logger and rate limit
from syncro_read import get_all_tickets_for_customer, extract_ticket_subjects_and_dates
from syncro_utils import get_customer_id_by_name, get_syncro_created_date, get_syncro_status
from syncro_utils import syncro_prepare_ticket_json_superops, build_syncro_comment, build_syncro_initial_issue
from syncro_write import syncro_create_ticket, syncro_create_comment

#change to pauser_on to "yes" to have the import wait for each ticket and/or comment to review
#pauser_on = "yes"
pauser_on = None

# Initialize Logger
logger = get_logger(__name__)
chronology_logger = get_chronology_logger("chronology")


def parse_syncro_timestamp(timestamp_str):
    """Parse a normalized Syncro timestamp string."""
    return datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%S%z")


def build_and_validate_historical_comments(
    client,
    ticket_id,
    display_id,
    ticket_created_at,
    description,
    contact,
    timeline,
):
    """Build normalized comment payloads and record chronology issues."""
    comment_payloads = []
    chronology_issues = []

    if description and description != "No description available.":
        comment_payloads.append(build_syncro_initial_issue(description, contact, ticket_created_at))

    for entry in timeline:
        if not isinstance(entry, dict):
            logger.warning(
                "Skipping non-dict timeline entry during chronology validation for customer=%s ticket_id=%s display_id=%s entry=%s",
                client,
                ticket_id,
                display_id,
                entry,
            )
            continue

        if entry.get("type") == "DESCRIPTION":
            continue

        try:
            comment_payloads.append(build_syncro_comment(entry))
        except Exception as comment_error:
            raw_comment_time = entry.get("time")
            issue = (
                f"Skipped comment with invalid data for customer={client} "
                f"ticket_id={ticket_id} display_id={display_id} "
                f"comment_index={len(comment_payloads)} "
                f"comment_time={raw_comment_time!r}: {comment_error}"
            )
            chronology_issues.append(issue)
            logger.warning(issue)
            continue

    ticket_created_dt = parse_syncro_timestamp(ticket_created_at)
    previous_comment_dt = None

    for index, payload in enumerate(comment_payloads):
        created_at = payload.get("created_at")
        if not created_at:
            chronology_issues.append(
                f"Missing created_at on comment payload for customer={client} ticket_id={ticket_id} display_id={display_id}"
            )
            continue

        comment_dt = parse_syncro_timestamp(created_at)
        if comment_dt < ticket_created_dt:
            chronology_issues.append(
                f"Comment timestamp precedes ticket creation for customer={client} ticket_id={ticket_id} display_id={display_id}: "
                f"ticket_created_at={ticket_created_at} comment_created_at={created_at}"
            )

        if previous_comment_dt and comment_dt < previous_comment_dt:
            chronology_issues.append(
                f"Comment timestamps are out of order for customer={client} ticket_id={ticket_id} display_id={display_id}: "
                f"previous_comment_created_at={comment_payloads[index - 1]['created_at']} comment_created_at={created_at}"
            )

        previous_comment_dt = comment_dt

    return comment_payloads, chronology_issues



# API Configuration
# API_KEY = "Your SuperOps API Key"
# BASE_URL = "https://api.superops.ai/msp"
# CUSTOMER_SUBDOMAIN = "Your superops_subdomain"
DEFAULT_API_KEY = "Your SuperOps API Key"
DEFAULT_BASE_URL = "https://api.superops.ai/msp"
DEFAULT_CUSTOMER_SUBDOMAIN = "Your superops_subdomain"
DEFAULT_DRY_RUN = False
DEFAULT_MAX_TICKETS_TO_IMPORT = None
DEFAULT_SUPEROPS_TICKETS_CREATED_WITHIN_DAYS = None
API_KEY = DEFAULT_API_KEY
BASE_URL = DEFAULT_BASE_URL
CUSTOMER_SUBDOMAIN = DEFAULT_CUSTOMER_SUBDOMAIN
DRY_RUN = DEFAULT_DRY_RUN
MAX_TICKETS_TO_IMPORT = DEFAULT_MAX_TICKETS_TO_IMPORT
SUPEROPS_TICKETS_CREATED_WITHIN_DAYS = DEFAULT_SUPEROPS_TICKETS_CREATED_WITHIN_DAYS

try:
    from local_config import SUPEROPS_API_KEY as LOCAL_SUPEROPS_API_KEY
    from local_config import SUPEROPS_BASE_URL as LOCAL_SUPEROPS_BASE_URL
    from local_config import SUPEROPS_CUSTOMER_SUBDOMAIN as LOCAL_SUPEROPS_CUSTOMER_SUBDOMAIN

    API_KEY = LOCAL_SUPEROPS_API_KEY
    BASE_URL = LOCAL_SUPEROPS_BASE_URL
    CUSTOMER_SUBDOMAIN = LOCAL_SUPEROPS_CUSTOMER_SUBDOMAIN
except ImportError:
    pass

try:
    from local_config import DRY_RUN as LOCAL_DRY_RUN

    DRY_RUN = LOCAL_DRY_RUN
except ImportError:
    pass

try:
    from local_config import MAX_TICKETS_TO_IMPORT as LOCAL_MAX_TICKETS_TO_IMPORT

    MAX_TICKETS_TO_IMPORT = LOCAL_MAX_TICKETS_TO_IMPORT
except ImportError:
    pass

try:
    from local_config import (
        SUPEROPS_TICKETS_CREATED_WITHIN_DAYS as LOCAL_SUPEROPS_TICKETS_CREATED_WITHIN_DAYS,
    )

    SUPEROPS_TICKETS_CREATED_WITHIN_DAYS = LOCAL_SUPEROPS_TICKETS_CREATED_WITHIN_DAYS
except ImportError:
    pass


def normalize_ticket_cap(ticket_cap):
    """Normalize an optional ticket cap into a positive integer or None."""
    if ticket_cap in (None, "", 0):
        return None

    try:
        normalized_cap = int(ticket_cap)
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid MAX_TICKETS_TO_IMPORT value: %r", ticket_cap)
        return None

    if normalized_cap <= 0:
        logger.warning("Ignoring non-positive MAX_TICKETS_TO_IMPORT value: %r", ticket_cap)
        return None

    return normalized_cap


def normalize_ticket_created_within_days(days):
    """Normalize an optional created-time day filter into a positive integer or None."""
    if days in (None, "", 0):
        return None

    try:
        normalized_days = int(days)
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid SUPEROPS_TICKETS_CREATED_WITHIN_DAYS value: %r", days)
        return None

    if normalized_days <= 0:
        logger.warning("Ignoring non-positive SUPEROPS_TICKETS_CREATED_WITHIN_DAYS value: %r", days)
        return None

    return normalized_days


def get_superops_created_time_cutoff(days):
    """Return an ISO-8601 UTC cutoff timestamp for createdTime filtering."""
    normalized_days = normalize_ticket_created_within_days(days)
    if normalized_days is None:
        return None

    cutoff = datetime.now(timezone.utc) - timedelta(days=normalized_days)
    return cutoff.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_superops_created_datetime_cutoff(days):
    """Return a UTC datetime cutoff for filtering fetched SuperOps tickets."""
    normalized_days = normalize_ticket_created_within_days(days)
    if normalized_days is None:
        return None

    return datetime.now(timezone.utc) - timedelta(days=normalized_days)


def parse_superops_created_time(created_time):
    """Parse a SuperOps createdTime value into a timezone-aware UTC datetime."""
    if not isinstance(created_time, str) or not created_time.strip():
        return None

    normalized_str = created_time.strip()
    if normalized_str.endswith("Z"):
        normalized_str = normalized_str[:-1] + "+00:00"

    parsed_date = None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed_date = datetime.strptime(normalized_str, fmt)
            break
        except ValueError:
            continue

    if parsed_date is None:
        try:
            parsed_date = datetime.fromisoformat(normalized_str)
        except ValueError:
            logger.warning("Unable to parse SuperOps createdTime value: %r", created_time)
            return None

    if parsed_date.tzinfo is None:
        source_timezone = pytz.timezone(SUPEROPS_SOURCE_TIMEZONE)
        parsed_date = source_timezone.localize(parsed_date)

    return parsed_date.astimezone(timezone.utc)


def ticket_is_within_created_window(ticket, cutoff_datetime):
    """Return True when the fetched SuperOps ticket should be kept for import."""
    if cutoff_datetime is None:
        return True

    created_time = ticket.get("createdTime")
    parsed_created_time = parse_superops_created_time(created_time)
    if parsed_created_time is None:
        logger.warning(
            "Keeping ticket %s without createdTime filter because createdTime could not be parsed: %r",
            ticket.get("ticketId"),
            created_time,
        )
        return True

    return parsed_created_time >= cutoff_datetime


MAX_TICKETS_TO_IMPORT = normalize_ticket_cap(MAX_TICKETS_TO_IMPORT)
SUPEROPS_TICKETS_CREATED_WITHIN_DAYS = normalize_ticket_created_within_days(
    SUPEROPS_TICKETS_CREATED_WITHIN_DAYS
)

# Headers
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
    "Customersubdomain": CUSTOMER_SUBDOMAIN
}

# Reuse a single session for all API interactions with default headers
session = requests.Session()
session.headers.update(HEADERS)

# GraphQL Queries
QUERY_GET_CLIENT_LIST = """query getClientList($input: ListInfoInput!) { getClientList(input: $input) { clients { accountId name } listInfo { page pageSize hasMore totalCount }}}"""
QUERY_GET_TICKETS = """query getTicketList($input: ListInfoInput!) { getTicketList(input: $input) { tickets { ticketId displayId subject status priority createdTime } listInfo { page pageSize hasMore totalCount }}}"""
QUERY_GET_TICKET_CONVERSATIONS = """query getTicketConversationList($input: TicketIdentifierInput!) { getTicketConversationList(input: $input) { conversationId content time user toUsers { user } ccUsers { user } bccUsers { user } attachments { fileName originalFileName fileSize } type }}"""
QUERY_GET_TICKET_NOTES = """query getTicketNoteList($input: TicketIdentifierInput!) { getTicketNoteList(input: $input) { noteId addedBy addedOn content attachments { fileName originalFileName fileSize } privacyType }}"""

# Function to make API calls
def make_api_call(query, variables=None):
    """Generic function to make GraphQL requests to SuperOps API"""
    payload = {"query": query, "variables": variables or {}}
    max_attempts = 3

    for attempt in range(1, max_attempts + 1):
        time.sleep(RATE_LIMIT_SECONDS if attempt == 1 else min(30, RATE_LIMIT_SECONDS * (2 ** (attempt - 1))))
        try:
            response = session.request("POST", BASE_URL, json=payload, timeout=60)
            response.raise_for_status()
            response_data = response.json()
            graphql_errors = response_data.get("errors") if isinstance(response_data, dict) else None
            if graphql_errors:
                error_text = str(graphql_errors)
                retryable = "rate_limit_exceeded" in error_text
                if retryable and attempt < max_attempts:
                    logger.warning(
                        "SuperOps rate limit response; retrying attempt=%s/%s variables=%s",
                        attempt + 1,
                        max_attempts,
                        variables or {},
                    )
                    continue
                logger.error(
                    "SuperOps GraphQL error variables=%s errors=%s",
                    variables or {},
                    graphql_errors,
                )
                return None
            return response_data

        except requests.exceptions.RequestException as err:
            status_code = getattr(getattr(err, "response", None), "status_code", None)
            retryable = status_code == 429 or status_code is None or status_code >= 500
            if retryable and attempt < max_attempts:
                logger.warning(
                    "Transient SuperOps request failure; retrying attempt=%s/%s status=%s variables=%s error=%s",
                    attempt + 1,
                    max_attempts,
                    status_code,
                    variables or {},
                    err,
                )
                continue
            logger.error("SuperOps request failed variables=%s error=%s", variables or {}, err)
            return None
        except ValueError as err:
            logger.error("SuperOps returned invalid JSON variables=%s error=%s", variables or {}, err)
            return None

    return None

# Function to strip HTML content
def strip_html(content):
    """Strips HTML tags and returns plain text.

    Returns an empty string when content is missing.
    """
    if not content:
        return ""
    soup = BeautifulSoup(content, "html.parser")
    return soup.get_text()

# Extract relevant ticket details
def extract_ticket_details(ticket_info):
    """
    Extracts relevant ticket details from the ticket_info dictionary.
    """
    ticket_data = ticket_info.get('ticketData', {})

    return {
        "displayId": ticket_data.get('displayId'),
        "ticketId": ticket_data.get('ticketId'),
        "subject": ticket_data.get('subject'),
        "status": ticket_data.get('status'),
        "priority": ticket_data.get('priority'),
        "created_time": ticket_data.get('createdTime'),
        "notes": ticket_data.get('notes', []),
        "conversations": ticket_data.get('conversations', [])
    }

# Get the oldest TECH_REPLY conversation (Assigned Tech)
def get_assigned_tech_and_user(conversations):
    """
    Finds the oldest TECH_REPLY conversation and returns the technician details and toUsers.

    Args:
        conversations (list): List of conversation dictionaries.

    Returns:
        tuple: (assigned_tech, to_users) where
            - assigned_tech (dict or None) contains the tech's user info.
            - to_users (list) contains the toUsers from the oldest TECH_REPLY.
    """
    try:
        tech_replies = [conv for conv in conversations if conv.get('type') == 'TECH_REPLY']

        if tech_replies:
            oldest_tech_reply = min(tech_replies, key=lambda x: x['time'])  # Find oldest TECH_REPLY
            tech = oldest_tech_reply.get('user', None)  # Get the technician's info
            to_users = oldest_tech_reply.get("toUsers", [])  # Get the toUsers list
            
            return tech, to_users  # Always return a tuple

        return None, []  # No tech assigned, return an empty list for to_users

    except Exception as e:
        logger.error(f"❌ Error extracting assigned tech and toUsers: {e}", exc_info=True)
        return None, []  # Ensure a tuple is always returned


# Extract DESCRIPTION content
def get_description_content(conversations):
    """
    Extracts the first DESCRIPTION-type conversation (initial issue).
    """
    for conv in conversations:
        if conv.get('type') == 'DESCRIPTION':
            return conv.get('content')  # Return first DESCRIPTION found

    return None  # No DESCRIPTION found


def extract_contact_name(to_users):
    """Normalize SuperOps toUsers data into a single contact name string."""
    if not to_users:
        return None

    first_user = to_users[0]
    if isinstance(first_user, dict):
        user_value = first_user.get("user")
        if isinstance(user_value, dict):
            return user_value.get("name")
        if isinstance(user_value, str):
            return user_value
        return first_user.get("name")

    if isinstance(first_user, str):
        return first_user

    return None


def normalize_superops_ticket(ticket):
    """Convert raw SuperOps ticket data into a stable internal shape."""
    ticket_info = extract_ticket_details({"ticketData": ticket})
    assigned_tech, to_users = get_assigned_tech_and_user(ticket_info["conversations"])

    assigned_tech_name = None
    if isinstance(assigned_tech, dict):
        assigned_tech_name = assigned_tech.get("name")
    elif isinstance(assigned_tech, str):
        assigned_tech_name = assigned_tech

    normalized_ticket = {
        "displayId": ticket_info.get("displayId"),
        "ticketId": ticket_info.get("ticketId"),
        "subject": ticket_info.get("subject"),
        "status": ticket_info.get("status"),
        "priority": ticket_info.get("priority"),
        "created_time": ticket_info.get("created_time"),
        "notes": ticket_info.get("notes", []),
        "conversations": ticket_info.get("conversations", []),
        "assigned_tech": assigned_tech_name,
        "contact": extract_contact_name(to_users),
        "description": get_description_content(ticket_info["conversations"]),
    }

    return normalized_ticket

# Fetch ticket conversations
def get_ticket_conversations(ticket_id):
    """Fetches all conversations for a given ticket ID."""
    variables = {"input": {"ticketId": ticket_id}}
    response = make_api_call(QUERY_GET_TICKET_CONVERSATIONS, variables)

    if response is None or "data" not in response or response["data"].get("getTicketConversationList") is None:
        return []

    conversations = response["data"]["getTicketConversationList"]
    logger.info(f"Conversations for ticket {ticket_id}: {len(conversations)}")
    for conv in conversations:
        conv["user"] = conv.get("user", {})  
        conv["content"] = strip_html(conv.get("content", ""))  

    return conversations

# Fetch ticket notes
def get_ticket_notes(ticket_id):
    """Fetches all notes for a given ticket ID."""
    variables = {"input": {"ticketId": ticket_id}}
    response = make_api_call(QUERY_GET_TICKET_NOTES, variables)

    if response is None or "data" not in response or response["data"].get("getTicketNoteList") is None:
        return []

    notes = response["data"]["getTicketNoteList"]
    logger.info(f"Notes for ticket {ticket_id}: {len(notes)}")
    for note in notes:
        note["content"] = strip_html(note.get("content", ""))

    return notes

# Fetch all tickets for a client
def get_tickets_for_client(
    account_id,
    remaining_ticket_cap=None,
    pagination_stats=None,
    client_name=None,
    ticket_callback=None,
    progress_state=None,
):
    """Fetches all tickets for a given client using `condition` filter."""
    tickets = []
    page = 1
    page_size = 100
    pages_fetched = 0
    raw_tickets_fetched = 0
    tickets_read = 0
    selected_tickets = 0
    expected_total = None
    seen_ticket_ids = set()
    cutoff_datetime = get_superops_created_datetime_cutoff(SUPEROPS_TICKETS_CREATED_WITHIN_DAYS)
    cutoff_time = (
        cutoff_datetime.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
        if cutoff_datetime is not None
        else None
    )
    logger.info(
        "Getting tickets for client %s remaining_ticket_cap=%s created_since=%s",
        account_id,
        remaining_ticket_cap,
        cutoff_time,
    )

    if remaining_ticket_cap is not None and remaining_ticket_cap <= 0:
        logger.info("Skipping ticket fetch for client %s because the ticket cap was reached.", account_id)
        return tickets

    while True:
        variables = {
            "input": {
                "page": page,
                "pageSize": page_size,
                "condition": {
                    "joinOperator": "AND",
                    "operands": [{"attribute": "client.accountId", "operator": "is", "value": account_id}]
                },
                "sort": [{"attribute": "ticketId", "order": "ASC"}],
            }
        }

        response = make_api_call(QUERY_GET_TICKETS, variables)

        if response is None or "data" not in response or response["data"].get("getTicketList") is None:
            logger.error("Failed to retrieve ticket page client=%s page=%s; stopping pagination.", account_id, page)
            break

        ticket_data = response["data"]["getTicketList"]
        if "tickets" not in ticket_data:
            logger.error("Ticket response missing tickets list for client=%s page=%s", account_id, page)
            break

        page_tickets = ticket_data["tickets"]
        if not isinstance(page_tickets, list):
            logger.error(
                "Ticket response has invalid tickets list for client=%s page=%s value_type=%s",
                account_id,
                page,
                type(page_tickets).__name__,
            )
            break
        pages_fetched += 1
        raw_tickets_fetched += len(page_tickets)
        list_info = ticket_data.get("listInfo")
        if not isinstance(list_info, dict):
            logger.error(
                "Ticket response missing listInfo; stopping pagination for client=%s page=%s fetched=%s",
                account_id,
                page,
                raw_tickets_fetched,
            )
            break
        response_page = list_info.get("page")
        if response_page is not None and response_page != page:
            logger.error(
                "Ticket response page mismatch for client=%s requested=%s returned=%s; stopping pagination.",
                account_id,
                page,
                response_page,
            )
            break
        expected_total = list_info.get("totalCount", expected_total)
        has_more = list_info.get("hasMore")
        if not isinstance(has_more, bool):
            logger.error(
                "Ticket response has invalid hasMore for client=%s page=%s value=%r; stopping pagination.",
                account_id,
                page,
                has_more,
            )
            break
        try:
            expected_total = int(expected_total) if expected_total is not None else None
        except (TypeError, ValueError):
            logger.warning(
                "Ticket response has invalid totalCount for client=%s page=%s value=%r",
                account_id,
                page,
                expected_total,
            )
            expected_total = None
        if progress_state is not None:
            progress_state["total"] = expected_total
        logger.info(
            "[SuperOps | %s | page %s] Found %s tickets (%s of %s; more pages: %s).",
            client_name or account_id,
            page,
            len(page_tickets),
            raw_tickets_fetched,
            expected_total if expected_total is not None else "unknown",
            "yes" if has_more else "no",
        )

        if has_more is True and not page_tickets:
            logger.error("Ticket pagination made no progress for client=%s page=%s", account_id, page)
            break

        for ticket in page_tickets:
            tickets_read += 1
            if tickets_read % 10 == 0:
                logger.info(
                    "[SuperOps] Reading tickets for %s: %s read%s.",
                    client_name or account_id,
                    tickets_read,
                    f" of {expected_total}" if expected_total is not None else "",
                )
            ticket_id = ticket.get("ticketId")
            if ticket_id in seen_ticket_ids:
                logger.warning(
                    "Duplicate ticket returned across pages; skipping client=%s ticket_id=%s page=%s",
                    account_id,
                    ticket_id,
                    page,
                )
                continue
            if ticket_id:
                seen_ticket_ids.add(ticket_id)

            if not ticket_is_within_created_window(ticket, cutoff_datetime):
                logger.info(
                    "Skipping ticket %s for client %s because createdTime=%s is older than cutoff=%s",
                    ticket.get("ticketId"),
                    account_id,
                    ticket.get("createdTime"),
                    cutoff_time,
                )
                continue

            if remaining_ticket_cap is not None and selected_tickets >= remaining_ticket_cap:
                logger.info(
                    "Reached remaining ticket cap for client %s at %s tickets.",
                    account_id,
                    remaining_ticket_cap,
                )
                if pagination_stats is not None:
                    pagination_stats["pages"] = pages_fetched
                    pagination_stats["raw_tickets"] = raw_tickets_fetched
                    pagination_stats["selected"] = selected_tickets
                    pagination_stats["api_total"] = expected_total
                return tickets

            ticket_id = ticket.get("ticketId")
            if not ticket_id:
                continue

            ticket["conversations"] = get_ticket_conversations(ticket_id)
            ticket["notes"] = get_ticket_notes(ticket_id)  

            if ticket_callback is not None:
                ticket_callback(ticket)
            else:
                tickets.append(ticket)
            selected_tickets += 1

        if has_more is not True:
            break

        page += 1  

    if expected_total is not None and raw_tickets_fetched < expected_total and remaining_ticket_cap is None:
        logger.warning(
            "Ticket pagination ended before API total for client=%s fetched=%s api_total=%s pages=%s",
            account_id,
            raw_tickets_fetched,
            expected_total,
            pages_fetched,
        )
    if pagination_stats is not None:
        pagination_stats["pages"] = pages_fetched
        pagination_stats["raw_tickets"] = raw_tickets_fetched
        pagination_stats["selected"] = selected_tickets
        pagination_stats["api_total"] = expected_total

    return tickets

def combine_notes_and_conversations(notes, conversations):
    """
    Merges notes and conversations into a single list sorted by timestamp.

    Args:
        notes (list): List of notes.
        conversations (list): List of conversations.

    Returns:
        list: A sorted list of combined notes and conversations.
    """
    merged_items = []

    # Process notes
    for note in notes:
        added_by = note.get("addedBy")
        if not isinstance(added_by, dict):
            logger.warning(f"Note entry is missing addedBy user info: {note}")
            added_by = {}

        merged_items.append({
            "type": "NOTE",
            "content": note.get("content", "No Content"),
            "user": added_by.get("name", "Unknown"),
            "time": note.get("addedOn", "Unknown Time")
        })

    # Process conversations safely
    for conv in conversations:
        user_info = conv.get("user")
        if not isinstance(user_info, dict):  # Ensure user info is valid
            logger.warning(f"⚠️ Conversation entry is missing user info: {conv}")
            user_info = {}  # Default to an empty dictionary to prevent attribute errors

        merged_items.append({
            "type": conv.get("type", "Unknown"),
            "content": conv.get("content", "No Content"),
            "user": user_info.get("name", "Unknown"),  # Safe access
            "time": conv.get("time", "Unknown Time")
        })

    # Sort the merged list by time (ascending order)
    merged_items.sort(key=lambda x: x["time"])
    logger.info(f"Combined notes and conversations: {len(merged_items)}")
    
    return merged_items if merged_items else ["No notes or conversations found."]



def compare_tickets_by_subject_and_date(superops_tickets, syncro_tickets):
    """
    Compare SuperOps tickets with Syncro tickets based on subject and created time.

    Args:
        superops_tickets (dict): SuperOps tickets from `customers_tickets.items()`
        syncro_tickets (list): List of Syncro tickets (subject & created_at)

    Returns:
        list: A list of matching ticket IDs from Syncro.
    """
    matched_tickets = []

    try:
        logger.info(f"Comparing {len(superops_tickets)} SuperOps tickets with {len(syncro_tickets)} Syncro tickets.")

        for ticket_id, ticket_info in superops_tickets.items():
            try:
                # Extract subject and created_time from SuperOps ticket
                subject = ticket_info.get("subject")
                created_time = ticket_info.get("created_time")
                

                if not subject or not created_time:
                    logger.warning(f"Skipping ticket {ticket_id}: Missing subject or created_time.")
                    continue

                #converted_created_time = get_syncro_created_date(created_time)  # Convert time format


                for syncro_ticket in syncro_tickets:
                    # Extract subject and created_at from Syncro ticket
                    syncro_subject = syncro_ticket.get("subject")
                    syncro_created_at = syncro_ticket.get("created_at")

                    if not syncro_subject or not syncro_created_at:
                        logger.warning(f"Skipping Syncro ticket {syncro_ticket.get('ticket_id')}: Missing subject or created_at.")
                        continue
                    
                    #created_time, syncro_created_at = strip_to_ymd(created_time, syncro_created_at)
                    # Compare both subject and created date
                    if subject == syncro_subject and created_time == syncro_created_at:                        
                        
                        if ticket_id not in matched_tickets:
                            matched_tickets.append(ticket_id)

                        logger.info(f"Match found: {ticket_id} (SuperOps) <-> {syncro_ticket['ticket_id']} (Syncro)")
                        #print((f"Match found: {ticket_id} (SuperOps) <-> {syncro_ticket['ticket_id']} (Syncro)"))
                    else:
                        logger.info(f"NO MATCH found: {subject} {created_time} (SuperOps) <-> {syncro_subject} {syncro_created_at}(Syncro)")
                        #print((f"NO MATCH found: {subject} {created_time} (SuperOps) <-> {syncro_subject} {syncro_created_at}(Syncro)"))
                        

            except Exception as e:
                logger.error(f"Error processing SuperOps ticket {ticket_id}: {e}", exc_info=True)
        
        logger.info(f"Comparison completed: {len(matched_tickets)} matched tickets found.")

    except Exception as e:
        logger.exception(f"Critical error in compare_tickets_by_subject_and_date: {e}")

    return matched_tickets

def compare_tickets_by_subject(superops_tickets, syncro_tickets):
    """
    Compare SuperOps tickets with Syncro tickets based on subject and created time.

    Args:
        superops_tickets (dict): SuperOps tickets from `customers_tickets.items()`
        syncro_tickets (list): List of Syncro tickets (subject & created_at)

    Returns:
        list: A list of matching ticket IDs from Syncro.
    """
    matched_tickets = []

    try:
        logger.info(f"Comparing {len(superops_tickets)} SuperOps tickets with {len(syncro_tickets)} Syncro tickets.")
      

        for ticket_id, ticket_info in superops_tickets.items():
            try:
                # Extract subject and created_time from SuperOps ticket
                subject = ticket_info.get("subject")  
                displayId = ticket_info.get("displayId")

                if not subject:
                    logger.warning(f"Skipping ticket {ticket_id}: Missing subject .")
                    continue
                if not displayId:
                    logger.warning(f"Skipping ticket {displayId}: Missing displayId .")
                    continue
                
                for syncro_ticket in syncro_tickets:
                    # Extract subject and created_at from Syncro ticket
                    syncro_subject = syncro_ticket.get("subject")

                    if not syncro_subject:
                        logger.warning(f"Skipping Syncro ticket {syncro_ticket.get('ticket_id')}: Missing subject")
                        continue
                    

                    if str(displayId) in syncro_subject:
                        if displayId not in matched_tickets:
                            matched_tickets.append(displayId)

                        logger.info(f"Match found: {displayId} (SuperOps) <-> {syncro_subject} (Syncro)")
                        #print((f"Match found: {displayId} (SuperOps) <-> {syncro_subject} (Syncro)"))
                    else:
                        logger.info(f"NO MATCH found: {subject} (SuperOps) <-> {syncro_subject} (Syncro)")
                        #print((f"NO MATCH found: {subject} (SuperOps) <-> {syncro_subject} (Syncro)"))
                        

            except Exception as e:
                logger.error(f"Error processing SuperOps ticket {displayId}: {e}", exc_info=True)
        
        logger.info(f"Comparison completed: {len(matched_tickets)} matched tickets found.")

    except Exception as e:
        logger.exception(f"Critical error in compare_tickets_by_subject: {e}")

    return matched_tickets
def process_customer_tickets(client, tickets):
    """
    Process tickets for a specific customer.

    Args:
        client (str): Customer name.
        tickets (dict): Dictionary containing ticket details.
    """
    try:
        logger.info(f"Fetching Syncro tickets for customer: {client}")
        

        syncro_customer_id = get_customer_id_by_name(client)
        if not syncro_customer_id:
            logger.warning(f"⚠️ Customer '{client}' not found in Syncro. Skipping.")
            return

        syncro_tickets = get_all_tickets_for_customer(client)
        syncro_tickets_subjects_dates = extract_ticket_subjects_and_dates(syncro_tickets)

        matched_ticket_ids = compare_tickets_by_subject(tickets, syncro_tickets_subjects_dates)
        logger.info(f"Matched Ticket Ids: {matched_ticket_ids}")
        
        
        ticket_items = list(tickets.items())  # Convert dict_items to a list
        logger.debug(f"Total Tickets {len(ticket_items)} being Processed.")
        if ticket_items:  # Ensure it's not empty
            first_ticket_id, first_ticket_info = ticket_items[0]  # Get the first item
            logger.info(f"Ticket ID: {first_ticket_id} Ticket Info: {first_ticket_info}")
            
        else:
            logger.info("No tickets found.")
        for ticket_id, ticket_info in tickets.items():            
            process_individual_ticket(client, ticket_id, ticket_info, matched_ticket_ids)

    except Exception as e:
        logger.error(f"❌ Error processing customer {client}: {e}", exc_info=True)


def process_individual_ticket(client, ticket_id, ticket_info, matched_ticket_ids):
    """
    Process an individual ticket.

    Args:
        client (str): Customer name.
        ticket_id (int): Ticket ID.
        ticket_info (dict): Dictionary containing ticket details.
        matched_ticket_ids (list): List of ticket IDs that exist in Syncro.
    """
    #logger.info(f"Processing  tickets for customer: {client}")
    try:
        subject = ticket_info.get("subject")
        displayId = ticket_info.get("displayId")
        subject = ticket_info.get("subject", "") + f" {displayId}"
        created_time = ticket_info.get("created_time")

        logger.info(f"Processing an individual ticket for customer: {client} Subject: {subject} displayId: {displayId}")

        if not subject or not created_time:
            logger.warning(f"Skipping ticket {ticket_id}: Missing subject or created_time.")
            return

        converted_created_time = get_syncro_created_date(created_time)
        # Force imported tickets to have a Resolved status in Syncro
        status = "Resolved"
        priority = ticket_info.get("priority", "Unknown")
        assigned_tech = extract_assigned_tech(ticket_id, ticket_info)
        description = ticket_info.get("description", "No description available.")
        contact = ticket_info.get("contact", "No contact available.")
        notes, conversations = extract_notes_and_conversations(ticket_id, ticket_info)

        # Handle cases where one or both are empty
        if notes or conversations:
            timeline = combine_notes_and_conversations(notes, conversations)
            logger.debug(f"Combined {len(timeline)} timeline entries for ticket {ticket_id}.")
        else:
            timeline = ["No notes or conversations found."]
            logger.info(f"⚠️ Ticket {displayId}: No notes or conversations found.")

        logger.info(f"Looking for Ticket ID {displayId}: in {matched_ticket_ids} .")

        if displayId in matched_ticket_ids:
            logger.warning(f"✅ Customer {client} Ticket {displayId} ({subject}) already exists in Syncro.")
            return
        else:
            logger.info(f"❌ Customer {client} Ticket {ticket_id} ({subject}) NOT found in Syncro.")
            new_syncro_ticket = syncro_prepare_ticket_json_superops(client, contact,ticket_id, subject, converted_created_time, status, priority, assigned_tech, description, timeline)
            logger.info(f"Attempting to create Ticket: {new_syncro_ticket}")
            created_ticket_response = syncro_create_ticket(new_syncro_ticket)
                
            

            if created_ticket_response and "ticket" in created_ticket_response:
                created_ticket_id = created_ticket_response["ticket"].get("id")
                created_ticket_number = created_ticket_response["ticket"].get("number")
                logger.info(f"✅ Successfully created Syncro Ticket: {created_ticket_number} (ID: {created_ticket_id})")
                if pauser_on:
                    input("Pausing for Ticket Creation - Press Enter to continue...")
                else:
                    logger.info(f"No Pause moving on")
                # Loop through timeline and create comments
                for entry in timeline:
                    if entry.get("type") == "DESCRIPTION":
                        logger.info(f"Skipping DESCRIPTION entry for ticket {created_ticket_number}: {entry}")
                        continue  # Skip DESCRIPTION type entries

                    logger.info(f"from process_individual_ticket - Now forming comment for ticket {created_ticket_number}: {entry}")
                    
                    try:
                        logger.info(f"from process_individual_ticket Creating comment for ticket ")
                        logger.info(entry)
                        formatted_comment = build_syncro_comment(entry)
                        logger.info(f"from process_individual_ticket Creating comment for ticket {created_ticket_number}: formatted_comment being passed into syncro_create_comment {formatted_comment}")
                        syncro_create_comment(formatted_comment,created_ticket_id)
                    except Exception as comment_error:
                        logger.error(f"❌ Error creating comment for ticket {created_ticket_number}: {comment_error}", exc_info=True)
                if pauser_on:
                    input("Pausing for Comments added to Ticket - Press Enter to continue...")
                else:
                    logger.info(f"No Pause for comments moving on")
                

    except Exception as e:
        logger.error(f"❌ Error processing ticket {ticket_id}: {e}", exc_info=True)

def extract_assigned_tech(ticket_id, ticket_info):
    """
    Extract assigned technician information.

    Args:
        ticket_id (int): Ticket ID.
        ticket_info (dict): Dictionary containing ticket details.

    Returns:
        str: Assigned technician's name or "Unassigned" if unavailable.
    """
    assigned_tech_info = ticket_info.get("assigned_tech")
    if isinstance(assigned_tech_info, str) and assigned_tech_info.strip():
        return assigned_tech_info
    if isinstance(assigned_tech_info, dict):
        return assigned_tech_info.get("name", "Unassigned")
    
    logger.warning(f"⚠️ Ticket {ticket_id}: assigned_tech is None or invalid format. Defaulting to 'Unassigned'.")
    return "Unassigned"

def extract_notes_and_conversations(ticket_id, ticket_info):
    """
    Extract notes and conversations for a ticket.

    Args:
        ticket_id (int): Ticket ID.
        ticket_info (dict): Dictionary containing ticket details.

    Returns:
        tuple: (list of notes, list of conversations)
    """
    try:
        notes = ticket_info.get("notes")
        if not isinstance(notes, list):  
            logger.warning(f"extract_notes_and_conversations ⚠️ Ticket {ticket_id}: Notes are None or invalid format. Defaulting to an empty list.")
            notes = []
    except Exception as e:
        logger.error(f"❌extract_notes_and_conversations Error retrieving notes for ticket {ticket_id}: {e}", exc_info=True)
        notes = []

    try:
        conversations = ticket_info.get("conversations")
        if not isinstance(conversations, list):  
            logger.warning(f"⚠️extract_notes_and_conversations Ticket {ticket_id}: Conversations are None or invalid format. Defaulting to an empty list.")
            conversations = []
    except Exception as e:
        logger.error(f"❌extract_notes_and_conversations Error retrieving conversations for ticket {ticket_id}: {e}", exc_info=True)
        conversations = []

    return notes, conversations


def build_ticket_result(
    client,
    ticket_id,
    display_id,
    result,
    reason=None,
    syncro_ticket_id=None,
    comment_failures=0,
    comment_count=0,
):
    """Build a structured outcome for one ticket import attempt."""
    return {
        "customer": client,
        "ticket_id": ticket_id,
        "display_id": display_id,
        "result": result,
        "reason": reason,
        "syncro_ticket_id": syncro_ticket_id,
        "comment_failures": comment_failures,
        "comment_count": comment_count,
    }


def log_ticket_result(ticket_result):
    """Log a ticket outcome with consistent context."""
    logger.info(
        "ticket_result customer=%s ticket_id=%s display_id=%s result=%s reason=%s syncro_ticket_id=%s comment_failures=%s comment_count=%s",
        ticket_result["customer"],
        ticket_result["ticket_id"],
        ticket_result["display_id"],
        ticket_result["result"],
        ticket_result["reason"],
        ticket_result["syncro_ticket_id"],
        ticket_result["comment_failures"],
        ticket_result["comment_count"],
    )


def log_import_summary(results):
    """Log a compact summary of the overall import run."""
    summary = {}
    for result in results:
        summary[result["result"]] = summary.get(result["result"], 0) + 1

    failure_count = sum(
        summary.get(result_name, 0)
        for result_name in (
            "skipped_missing_required_fields",
            "failed_date_conversion",
            "failed_payload_prepare",
            "failed_ticket_create",
            "failed_ticket_processing",
            "failed_customer_processing",
        )
    )
    logger.info(
        "[Summary] Import results: %s processed; %s would be created; %s created; "
        "%s duplicates skipped; %s missing customers; %s failures; %s created with comment failures.",
        len(results),
        summary.get("would_create", 0),
        summary.get("created", 0),
        summary.get("skipped_duplicate", 0),
        summary.get("skipped_missing_customer", 0),
        failure_count,
        summary.get("created_with_comment_failures", 0),
    )


def process_customer_tickets(client, tickets):
    """
    Process tickets for a specific customer.

    Returns:
        list: Structured ticket outcome dictionaries.
    """
    ticket_results = []
    try:
        logger.info(f"Fetching Syncro tickets for customer: {client}")

        syncro_customer_id = get_customer_id_by_name(client)
        if not syncro_customer_id:
            logger.warning(f"Customer '{client}' not found in Syncro. Skipping.")
            for ticket_id, ticket_info in tickets.items():
                result = build_ticket_result(
                    client,
                    ticket_id,
                    ticket_info.get("displayId"),
                    "skipped_missing_customer",
                    reason="customer_not_found_in_syncro",
                )
                log_ticket_result(result)
                ticket_results.append(result)
            return ticket_results

        syncro_tickets = get_all_tickets_for_customer(client)
        syncro_tickets_subjects_dates = extract_ticket_subjects_and_dates(syncro_tickets)
        matched_ticket_ids = compare_tickets_by_subject(tickets, syncro_tickets_subjects_dates)
        logger.info(f"Matched Ticket Ids: {matched_ticket_ids}")

        total_tickets = len(tickets)
        for ticket_number, (ticket_id, ticket_info) in enumerate(tickets.items(), start=1):
            if ticket_number % 10 == 0 or ticket_number == total_tickets:
                logger.info(
                    "[Syncro] Processing ticket %s of %s for client %s (checking duplicates and writing).",
                    ticket_number,
                    total_tickets,
                    client,
                )
            try:
                ticket_results.append(
                    process_individual_ticket(client, ticket_id, ticket_info, matched_ticket_ids)
                )
            except Exception as ticket_error:
                logger.error(
                    "Error processing ticket customer=%s ticket_id=%s display_id=%s: %s",
                    client,
                    ticket_id,
                    ticket_info.get("displayId"),
                    ticket_error,
                    exc_info=True,
                )
                result = build_ticket_result(
                    client,
                    ticket_id,
                    ticket_info.get("displayId"),
                    "failed_ticket_processing",
                    reason=str(ticket_error),
                )
                log_ticket_result(result)
                ticket_results.append(result)

            if ticket_number % 10 == 0 or ticket_number == total_tickets:
                created_count = sum(
                    item["result"] in ("created", "created_with_comment_failures")
                    for item in ticket_results
                )
                skipped_count = sum(item["result"].startswith("skipped_") for item in ticket_results)
                failed_count = sum(item["result"].startswith("failed_") for item in ticket_results)
                logger.info(
                    "[Progress] Completed %s of %s for %s in Syncro: %s created, %s skipped, %s failed.",
                    ticket_number,
                    total_tickets,
                    client,
                    created_count,
                    skipped_count,
                    failed_count,
                )

    except Exception as e:
        logger.error(f"Error processing customer {client}: {e}", exc_info=True)
        for ticket_id, ticket_info in tickets.items():
            result = build_ticket_result(
                client,
                ticket_id,
                ticket_info.get("displayId"),
                "failed_customer_processing",
                reason=str(e),
            )
            log_ticket_result(result)
            ticket_results.append(result)

    return ticket_results


def process_individual_ticket(client, ticket_id, ticket_info, matched_ticket_ids):
    """Process an individual ticket and return a structured outcome."""
    display_id = ticket_info.get("displayId")
    subject_base = ticket_info.get("subject")
    subject = ticket_info.get("subject", "") + f" {display_id}"
    created_time = ticket_info.get("created_time")

    logger.info(
        "Processing ticket customer=%s ticket_id=%s display_id=%s subject=%s",
        client,
        ticket_id,
        display_id,
        subject,
    )

    if not subject_base or not created_time:
        result = build_ticket_result(
            client,
            ticket_id,
            display_id,
            "skipped_missing_required_fields",
            reason="missing_subject_or_created_time",
        )
        log_ticket_result(result)
        return result

    try:
        converted_created_time = get_syncro_created_date(created_time)
    except Exception as date_error:
        logger.error(
            "Date conversion failed for customer=%s ticket_id=%s display_id=%s: %s",
            client,
            ticket_id,
            display_id,
            date_error,
            exc_info=True,
        )
        result = build_ticket_result(
            client,
            ticket_id,
            display_id,
            "failed_date_conversion",
            reason=str(date_error),
        )
        log_ticket_result(result)
        return result

    source_status = ticket_info.get("status")
    status = get_syncro_status(source_status, default_status="Resolved")
    priority = ticket_info.get("priority", "Unknown")
    assigned_tech = extract_assigned_tech(ticket_id, ticket_info)
    description = ticket_info.get("description", "No description available.")
    contact = ticket_info.get("contact")
    notes, conversations = extract_notes_and_conversations(ticket_id, ticket_info)

    if notes or conversations:
        timeline = combine_notes_and_conversations(notes, conversations)
    else:
        timeline = []
        logger.info(f"Ticket {display_id}: No notes or conversations found.")

    if display_id in matched_ticket_ids:
        result = build_ticket_result(
            client,
            ticket_id,
            display_id,
            "skipped_duplicate",
            reason="matched_existing_syncro_ticket",
        )
        log_ticket_result(result)
        return result

    try:
        comment_payloads, chronology_issues = build_and_validate_historical_comments(
            client,
            ticket_id,
            display_id,
            converted_created_time,
            description,
            contact,
            timeline,
        )
    except Exception as comment_payload_error:
        logger.error(
            "Comment payload preparation failed for customer=%s ticket_id=%s display_id=%s: %s",
            client,
            ticket_id,
            display_id,
            comment_payload_error,
            exc_info=True,
        )
        result = build_ticket_result(
            client,
            ticket_id,
            display_id,
            "failed_payload_prepare",
            reason=str(comment_payload_error),
        )
        log_ticket_result(result)
        return result

    if chronology_issues:
        for issue in chronology_issues:
            chronology_logger.warning(issue)
        logger.warning(
            "Chronology issues detected for customer=%s ticket_id=%s display_id=%s. See separate chronology log.",
            client,
            ticket_id,
            display_id,
        )

    preview_comment_count = len(comment_payloads)

    try:
        new_syncro_ticket = syncro_prepare_ticket_json_superops(
            client,
            contact,
            display_id,
            subject,
            converted_created_time,
            status,
            priority,
            assigned_tech,
            description,
            timeline,
        )
    except Exception as payload_error:
        logger.error(
            "Payload preparation failed for customer=%s ticket_id=%s display_id=%s: %s",
            client,
            ticket_id,
            display_id,
            payload_error,
            exc_info=True,
        )
        result = build_ticket_result(
            client,
            ticket_id,
            display_id,
            "failed_payload_prepare",
            reason=str(payload_error),
        )
        log_ticket_result(result)
        return result

    if DRY_RUN:
        result = build_ticket_result(
            client,
            ticket_id,
            display_id,
            "would_create",
            reason="dry_run_preview",
            comment_count=preview_comment_count,
        )
        log_ticket_result(result)
        return result

    logger.info(f"Attempting to create Ticket: {new_syncro_ticket}")
    created_ticket_response = syncro_create_ticket(new_syncro_ticket)
    if not created_ticket_response or "ticket" not in created_ticket_response:
        result = build_ticket_result(
            client,
            ticket_id,
            display_id,
            "failed_ticket_create",
            reason="syncro_create_ticket_returned_no_ticket",
        )
        log_ticket_result(result)
        return result

    created_ticket_id = created_ticket_response["ticket"].get("id")
    created_ticket_number = created_ticket_response["ticket"].get("number")
    logger.info("Ticket created successfully.")

    if pauser_on:
        input("Pausing for Ticket Creation - Press Enter to continue...")

    comment_failures = 0
    for payload in comment_payloads:
        try:
            comment_response = syncro_create_comment(payload, created_ticket_id)
            if comment_response is None:
                comment_failures += 1
                logger.error(
                    "Comment creation returned no response for customer=%s ticket_id=%s display_id=%s ticket_number=%s",
                    client,
                    ticket_id,
                    display_id,
                    created_ticket_number,
                )
        except Exception as comment_error:
            comment_failures += 1
            logger.error(
                "Comment creation failed for customer=%s ticket_id=%s display_id=%s ticket_number=%s: %s",
                client,
                ticket_id,
                display_id,
                created_ticket_number,
                comment_error,
                exc_info=True,
            )

    if pauser_on:
        input("Pausing for Comments added to Ticket - Press Enter to continue...")

    result_name = "created_with_comment_failures" if comment_failures else "created"
    reason = "comment_failures_present" if comment_failures else None
    if chronology_issues:
        reason = "chronology_issue_logged" if reason is None else f"{reason};chronology_issue_logged"
    result = build_ticket_result(
        client,
        ticket_id,
        display_id,
        result_name,
        reason=reason,
        syncro_ticket_id=created_ticket_id,
        comment_failures=comment_failures,
        comment_count=preview_comment_count,
    )
    log_ticket_result(result)
    return result


def prepare_syncro_client_context(client):
    """Load the Syncro customer and existing ticket subjects before streaming imports."""
    syncro_customer_id = get_customer_id_by_name(client)
    if not syncro_customer_id:
        logger.warning("Customer '%s' not found in Syncro. Tickets will be marked as skipped.", client)
        return None

    syncro_tickets = get_all_tickets_for_customer(client, customer_id=syncro_customer_id)
    return extract_ticket_subjects_and_dates(syncro_tickets)


def process_streamed_ticket(client, raw_ticket, syncro_ticket_subjects_dates, run_results):
    """Normalize and process one SuperOps ticket immediately after it is read."""
    ticket_id = raw_ticket.get("ticketId")
    display_id = raw_ticket.get("displayId")

    try:
        ticket_info = normalize_superops_ticket(raw_ticket)
        if syncro_ticket_subjects_dates is None:
            result = build_ticket_result(
                client,
                ticket_id,
                display_id,
                "skipped_missing_customer",
                reason="customer_not_found_in_syncro",
            )
        else:
            matched_ticket_ids = [
                display_id
            ] if display_id and any(
                str(display_id) in str(existing.get("subject", ""))
                for existing in syncro_ticket_subjects_dates
            ) else []
            result = process_individual_ticket(client, ticket_id, ticket_info, matched_ticket_ids)
        run_results.append(result)
        return result
    except Exception as ticket_error:
        logger.error(
            "Error processing streamed ticket customer=%s ticket_id=%s display_id=%s: %s",
            client,
            ticket_id,
            display_id,
            ticket_error,
            exc_info=True,
        )
        result = build_ticket_result(
            client,
            ticket_id,
            display_id,
            "failed_ticket_processing",
            reason=str(ticket_error),
        )
        log_ticket_result(result)
        run_results.append(result)
        return result


def process_all_clients():
    """Fetch clients and process their tickets one customer at a time."""
    run_results = []
    remaining_ticket_cap = MAX_TICKETS_TO_IMPORT
    clients_seen = 0
    client_pages = 0
    tickets_fetched = 0
    tickets_selected = 0
    seen_client_ids = set()
    logger.info(
        "[Summary] Import started: mode=%s; ticket limit=%s; date filter=%s.",
        "dry run" if DRY_RUN else "write",
        remaining_ticket_cap if remaining_ticket_cap is not None else "unlimited",
        f"last {SUPEROPS_TICKETS_CREATED_WITHIN_DAYS} days"
        if SUPEROPS_TICKETS_CREATED_WITHIN_DAYS is not None
        else "all dates",
    )
    client_page = 1
    client_page_size = 100
    while True:
        clients_response = make_api_call(
            QUERY_GET_CLIENT_LIST,
            {
                "input": {
                    "page": client_page,
                    "pageSize": client_page_size,
                    "sort": [{"attribute": "accountId", "order": "ASC"}],
                }
            },
        )

        if (
            clients_response is None
            or "data" not in clients_response
            or clients_response["data"].get("getClientList") is None
        ):
            logger.error("Failed to retrieve client list page=%s; stopping client pagination.", client_page)
            break

        client_data = clients_response["data"]["getClientList"]
        clients = client_data.get("clients")
        list_info = client_data.get("listInfo")
        if not isinstance(clients, list) or not isinstance(list_info, dict):
            logger.error("Client response missing clients/listInfo page=%s; stopping client pagination.", client_page)
            break

        client_pages += 1
        clients_seen += len(clients)
        has_more = list_info.get("hasMore")
        response_page = list_info.get("page")
        if response_page is not None and response_page != client_page:
            logger.error(
                "Client response page mismatch requested=%s returned=%s; stopping pagination.",
                client_page,
                response_page,
            )
            break
        if not isinstance(has_more, bool):
            logger.error(
                "Client response has invalid hasMore page=%s value=%r; stopping pagination.",
                client_page,
                has_more,
            )
            break
        logger.info(
            "[SuperOps | clients | page %s] Found %s clients (%s total so far; API total: %s; more pages: %s).",
            client_page,
            len(clients),
            clients_seen,
            list_info.get("totalCount", "unknown"),
            "yes" if has_more else "no",
        )

        if has_more is True and not clients:
            logger.error("Client pagination made no progress page=%s; stopping.", client_page)
            break

        for client in clients:
            if remaining_ticket_cap is not None and remaining_ticket_cap <= 0:
                logger.info("Global ticket cap reached. Stopping before client %s.", client.get("name"))
                break

            account_id = client.get("accountId")
            client_name = client.get("name", "Unknown")
            if not account_id:
                logger.warning("Skipping client without accountId name=%s", client_name)
                continue
            if account_id in seen_client_ids:
                logger.warning(
                    "Duplicate client returned across pages; skipping account_id=%s name=%s page=%s",
                    account_id,
                    client_name,
                    client_page,
                )
                continue
            seen_client_ids.add(account_id)

            logger.info(
                "[Progress] Preparing Syncro and starting ticket import for %s.",
                client_name,
            )
            syncro_ticket_subjects_dates = prepare_syncro_client_context(client_name)
            ticket_stats = {}
            client_progress = {"processed": 0, "total": None}
            client_results = []

            def log_stream_progress(force=False):
                if not client_progress["processed"]:
                    return
                if not force and client_progress["processed"] % 10 != 0:
                    return
                created_count = sum(
                    item["result"] in ("created", "created_with_comment_failures")
                    for item in client_results
                )
                skipped_count = sum(item["result"].startswith("skipped_") for item in client_results)
                failed_count = sum(item["result"].startswith("failed_") for item in client_results)
                logger.info(
                    "[Progress] Completed %s of %s for %s in Syncro: %s created, %s skipped, %s failed.",
                    client_progress["processed"],
                    client_progress["total"] or "unknown",
                    client_name,
                    created_count,
                    skipped_count,
                    failed_count,
                )

            def handle_streamed_ticket(ticket):
                result = process_streamed_ticket(
                    client_name,
                    ticket,
                    syncro_ticket_subjects_dates,
                    run_results,
                )
                client_progress["processed"] += 1
                client_results.append(result)
                log_stream_progress()

            tickets = get_tickets_for_client(
                account_id,
                remaining_ticket_cap=remaining_ticket_cap,
                pagination_stats=ticket_stats,
                client_name=client_name,
                ticket_callback=handle_streamed_ticket,
                progress_state=client_progress,
            )
            log_stream_progress(force=True)
            tickets_fetched += ticket_stats.get("raw_tickets", 0)
            tickets_selected += ticket_stats.get("selected", len(tickets))
            logger.info(
                "[SuperOps | client %s] Found %s tickets across %s page(s); %s selected for import (API total: %s).",
                client_name,
                ticket_stats.get("raw_tickets", 0),
                ticket_stats.get("pages", 0),
                ticket_stats.get("selected", len(tickets)),
                ticket_stats.get("api_total"),
            )

            if remaining_ticket_cap is not None:
                remaining_ticket_cap -= ticket_stats.get("selected", len(tickets))
                logger.info(
                    "Remaining global ticket cap after client %s: %s",
                    client_name,
                    remaining_ticket_cap,
                )
            logger.info(
                "[Progress] Run so far: %s client(s) processed; %s tickets fetched; %s selected; %s import result(s).",
                len(seen_client_ids),
                tickets_fetched,
                tickets_selected,
                len(run_results),
            )

        if remaining_ticket_cap is not None and remaining_ticket_cap <= 0:
            break
        if has_more is not True:
            break
        client_page += 1

    logger.info(
        "[Summary] Run totals: %s client(s) across %s page(s); %s tickets fetched; %s selected; "
        "%s processed; remaining ticket limit: %s.",
        clients_seen,
        client_pages,
        tickets_fetched,
        tickets_selected,
        len(run_results),
        remaining_ticket_cap,
    )
    log_import_summary(run_results)
    return run_results

# Main Execution
if __name__ == "__main__":
    try:
        process_all_clients()
    except KeyboardInterrupt:
        logger.warning("[Summary] Import interrupted before the final totals were available.")
        raise
    except Exception:
        logger.exception("[Summary] Import failed before the final totals were available.")
        raise
