import requests
import time
from pprint import pprint
from bs4 import BeautifulSoup  # Import BeautifulSoup for HTML stripping
from syncro_configs import get_logger, RATE_LIMIT_SECONDS  # Import logger and rate limit
from syncro_read import get_all_tickets_for_customer, extract_ticket_subjects_and_dates
from syncro_utils import get_customer_id_by_name, get_syncro_created_date
from syncro_utils import syncro_prepare_ticket_json_superops, build_syncro_comment
from syncro_write import syncro_create_ticket, syncro_create_comment
from datetime import datetime

#change to pauser_on to "yes" to have the import wait for each ticket and/or comment to review
#pauser_on = "yes"
pauser_on = None

# Initialize Logger
logger = get_logger(__name__)

# Define the cutoff date
cutoff_date = datetime(2024, 4, 1)

# API Configuration
API_KEY = "Your SuperOps API Key"
BASE_URL = "https://api.superops.ai/msp"
CUSTOMER_SUBDOMAIN = "Your superops_subdomain"

# Headers
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
    "Customersubdomain": CUSTOMER_SUBDOMAIN
}

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
        response = requests.post(BASE_URL, headers=HEADERS, json=payload)
        response.raise_for_status()
        return response.json()

    except requests.exceptions.RequestException as err:
        logger.error(f"Request failed: {err}")
        return None

# Function to strip HTML content
def strip_html(content):
    """Strips HTML tags and returns plain text."""
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
            oldest_tech_reply = max(tech_replies, key=lambda x: x['time'])  # Find oldest TECH_REPLY
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
def get_tickets_for_client(account_id):
    """Fetches all tickets for a given client using `condition` filter."""
    tickets = []
    page = 1
    page_size = 10
    logger.info(f"Getting tickets for client {account_id}")
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

# Fetch all clients and their tickets
def get_all_clients_with_tickets():
    """Fetch all clients and their tickets"""
    clients = make_api_call(QUERY_GET_CLIENT_LIST, {"input": {"page": 1, "pageSize": 100}})
    #logger.info(f"Total clients in SuperOpsfound: {len(clients)}")

    if clients is None or "data" not in clients or clients["data"].get("getClientList") is None:
        return {}

    client_tickets = {}
    #logger.info(f"Total client Tickets found: {len(client_tickets)}")
    for client in clients["data"]["getClientList"]["clients"]:
        account_id = client["accountId"]
        client_name = client["name"]

        tickets = get_tickets_for_client(account_id)
        logger.info(f"Tickets found for {client_name}: {len(tickets)}")

        # Structure tickets
        client_ticket_info = {}
        for ticket in tickets:
            ticket_id = ticket.get("ticketId")
            if ticket_id:
                ticket_info = extract_ticket_details({"ticketData": ticket})
                ticket_info["assigned_tech"],ticket_info["contact"] = get_assigned_tech_and_user(ticket_info["conversations"])
                ticket_info["description"] = get_description_content(ticket_info["conversations"])

                client_ticket_info[ticket_id] = ticket_info

        client_tickets[client_name] = client_ticket_info

    return client_tickets


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

def process_tickets(customers_tickets):
    """
    Process and display tickets with extracted details.

    Args:
        customers_tickets (dict): Dictionary containing customer names as keys and their tickets as values.
    """
    try:
        logger.info(f"Processing tickets for {len(customers_tickets)} customers.")

        for client, tickets in customers_tickets.items():
            process_customer_tickets(client, tickets)

    except Exception as e:
        logger.critical(f"🔥 Critical error processing all tickets: {e}", exc_info=True)


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
        status = ticket_info.get("status", "Unknown")
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

            
            # Convert created_at string to datetime object
            ticket_created_time = datetime.strptime(new_syncro_ticket['created_at'], "%Y-%m-%dT%H:%M:%S%z")

            # Check if the ticket is older than April 2024
            if ticket_created_time < cutoff_date.replace(tzinfo=ticket_created_time.tzinfo):
                logger.info(f"Skipping ticket creation: Ticket {ticket_id} ({subject}) is too old (Created: {new_syncro_ticket['created_at']}) compared to cutoff date {cutoff_date}.")
                #input("Press Enter to continue...")
            else:
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
                    pprint(f"from process_individual_ticket - Now forming comment for ticket {created_ticket_number}: {entry}")
                    print(type(entry))
                    pprint(entry)
                    
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

# Main Execution
if __name__ == "__main__":
    customers_tickets = get_all_clients_with_tickets()
    process_tickets(customers_tickets)
