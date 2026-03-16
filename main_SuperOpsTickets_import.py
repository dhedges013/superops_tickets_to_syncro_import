import requests
import time
from bs4 import BeautifulSoup  # Import BeautifulSoup for HTML stripping
from syncro_configs import get_logger, RATE_LIMIT_SECONDS  # Import logger and rate limit
from syncro_read import get_all_tickets_for_customer, extract_ticket_subjects_and_dates
from syncro_utils import get_customer_id_by_name, get_syncro_created_date, get_syncro_status
from syncro_utils import syncro_prepare_ticket_json_superops, build_syncro_comment
from syncro_write import syncro_create_ticket, syncro_create_comment

#change to pauser_on to "yes" to have the import wait for each ticket and/or comment to review
#pauser_on = "yes"
pauser_on = None

# Initialize Logger
logger = get_logger(__name__)



# API Configuration
# API_KEY = "Your SuperOps API Key"
# BASE_URL = "https://api.superops.ai/msp"
# CUSTOMER_SUBDOMAIN = "Your superops_subdomain"
DEFAULT_API_KEY = "Your SuperOps API Key"
DEFAULT_BASE_URL = "https://api.superops.ai/msp"
DEFAULT_CUSTOMER_SUBDOMAIN = "Your superops_subdomain"
DEFAULT_DRY_RUN = False
DEFAULT_MAX_TICKETS_TO_IMPORT = None
API_KEY = DEFAULT_API_KEY
BASE_URL = DEFAULT_BASE_URL
CUSTOMER_SUBDOMAIN = DEFAULT_CUSTOMER_SUBDOMAIN
DRY_RUN = DEFAULT_DRY_RUN
MAX_TICKETS_TO_IMPORT = DEFAULT_MAX_TICKETS_TO_IMPORT

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


MAX_TICKETS_TO_IMPORT = normalize_ticket_cap(MAX_TICKETS_TO_IMPORT)

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
QUERY_GET_CLIENT_LIST = """query getClientList($input: ListInfoInput!) { getClientList(input: $input) { clients { accountId name }}}"""
QUERY_GET_TICKETS = """query getTicketList($input: ListInfoInput!) { getTicketList(input: $input) { tickets { ticketId displayId subject status priority createdTime } listInfo { hasMore totalCount }}}"""
QUERY_GET_TICKET_CONVERSATIONS = """query getTicketConversationList($input: TicketIdentifierInput!) { getTicketConversationList(input: $input) { conversationId content time user toUsers { user } ccUsers { user } bccUsers { user } attachments { fileName originalFileName fileSize } type }}"""
QUERY_GET_TICKET_NOTES = """query getTicketNoteList($input: TicketIdentifierInput!) { getTicketNoteList(input: $input) { noteId addedBy addedOn content attachments { fileName originalFileName fileSize } privacyType }}"""

# Function to make API calls
def make_api_call(query, variables=None):

    time.sleep(RATE_LIMIT_SECONDS)

    """Generic function to make GraphQL requests to SuperOps API"""
    payload = {"query": query, "variables": variables or {}}

    try:
        response = session.request("POST", BASE_URL, json=payload)
        response.raise_for_status()
        return response.json()

    except requests.exceptions.RequestException as err:
        logger.error(f"Request failed: {err}")
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
def get_tickets_for_client(account_id, remaining_ticket_cap=None):
    """Fetches all tickets for a given client using `condition` filter."""
    tickets = []
    page = 1
    page_size = 10
    logger.info(
        "Getting tickets for client %s remaining_ticket_cap=%s",
        account_id,
        remaining_ticket_cap,
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
                    "operands": [{"attribute": "client.accountId", "operator": "contains", "value": account_id}]
                }
            }
        }

        response = make_api_call(QUERY_GET_TICKETS, variables)

        if response is None or "data" not in response or response["data"].get("getTicketList") is None:
            return []

        ticket_data = response["data"]["getTicketList"]
        if "tickets" not in ticket_data:
            return []

        logger.info(f"Tickets for client {account_id}: {len(ticket_data['tickets'])}")
        for ticket in ticket_data["tickets"]:
            if remaining_ticket_cap is not None and len(tickets) >= remaining_ticket_cap:
                logger.info(
                    "Reached remaining ticket cap for client %s at %s tickets.",
                    account_id,
                    remaining_ticket_cap,
                )
                return tickets

            ticket_id = ticket.get("ticketId")
            if not ticket_id:
                continue

            ticket["conversations"] = get_ticket_conversations(ticket_id)
            ticket["notes"] = get_ticket_notes(ticket_id)  

            tickets.append(ticket)

        if not ticket_data.get("listInfo", {}).get("hasMore", False):
            break

        page += 1  

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
        merged_items.append({
            "type": "NOTE",
            "content": note.get("content", "No Content"),
            "user": note.get("addedBy", {}).get("name", "Unknown"),
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

    logger.info(
        "import_summary total=%s would_create=%s created=%s skipped_duplicate=%s skipped_missing_customer=%s "
        "skipped_missing_required_fields=%s failed_date_conversion=%s failed_payload_prepare=%s "
        "failed_ticket_create=%s failed_customer_processing=%s created_with_comment_failures=%s",
        len(results),
        summary.get("would_create", 0),
        summary.get("created", 0),
        summary.get("skipped_duplicate", 0),
        summary.get("skipped_missing_customer", 0),
        summary.get("skipped_missing_required_fields", 0),
        summary.get("failed_date_conversion", 0),
        summary.get("failed_payload_prepare", 0),
        summary.get("failed_ticket_create", 0),
        summary.get("failed_customer_processing", 0),
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

        for ticket_id, ticket_info in tickets.items():
            ticket_results.append(
                process_individual_ticket(client, ticket_id, ticket_info, matched_ticket_ids)
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

    preview_comment_count = sum(
        1 for entry in timeline if isinstance(entry, dict) and entry.get("type") != "DESCRIPTION"
    )

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
    logger.info(f"Successfully created Syncro Ticket: {created_ticket_number} (ID: {created_ticket_id})")

    if pauser_on:
        input("Pausing for Ticket Creation - Press Enter to continue...")

    comment_failures = 0
    for entry in timeline:
        if not isinstance(entry, dict):
            logger.warning(
                "Skipping non-dict timeline entry for customer=%s ticket_id=%s display_id=%s entry=%s",
                client,
                ticket_id,
                display_id,
                entry,
            )
            comment_failures += 1
            continue

        if entry.get("type") == "DESCRIPTION":
            logger.info(f"Skipping DESCRIPTION entry for ticket {created_ticket_number}: {entry}")
            continue

        try:
            formatted_comment = build_syncro_comment(entry)
            comment_response = syncro_create_comment(formatted_comment, created_ticket_id)
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


def process_all_clients():
    """Fetch clients and process their tickets one customer at a time."""
    run_results = []
    remaining_ticket_cap = MAX_TICKETS_TO_IMPORT
    logger.info(
        "Starting import run mode=%s max_tickets_to_import=%s",
        "dry_run" if DRY_RUN else "write",
        remaining_ticket_cap,
    )
    clients_response = make_api_call(
        QUERY_GET_CLIENT_LIST, {"input": {"page": 1, "pageSize": 100}}
    )

    if (
        clients_response is None
        or "data" not in clients_response
        or clients_response["data"].get("getClientList") is None
    ):
        logger.error("Failed to retrieve client list from SuperOps.")
        return run_results

    for client in clients_response["data"]["getClientList"]["clients"]:
        if remaining_ticket_cap is not None and remaining_ticket_cap <= 0:
            logger.info("Global ticket cap reached. Stopping before client %s.", client["name"])
            break

        account_id = client["accountId"]
        client_name = client["name"]

        tickets = get_tickets_for_client(account_id, remaining_ticket_cap=remaining_ticket_cap)
        logger.info(f"Tickets found for {client_name}: {len(tickets)}")

        client_ticket_info = {}
        for ticket in tickets:
            ticket_id = ticket.get("ticketId")
            if ticket_id:
                client_ticket_info[ticket_id] = normalize_superops_ticket(ticket)

        run_results.extend(process_customer_tickets(client_name, client_ticket_info))
        if remaining_ticket_cap is not None:
            remaining_ticket_cap -= len(client_ticket_info)
            logger.info(
                "Remaining global ticket cap after client %s: %s",
                client_name,
                remaining_ticket_cap,
            )

    log_import_summary(run_results)
    return run_results

# Main Execution
if __name__ == "__main__":
    process_all_clients()
