import os
import logging
import sys
from syncro_utils import syncro_api_call, check_duplicate_customer, check_duplicate_contact
from syncro_configs import get_logger
from syncro_read import syncro_get_all_contacts
import requests


# Add parent directory to sys.path for imports
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, parent_dir)

# Get a logger for this module
logger = get_logger(__name__)
print(f"Handlers for {logger.name}: {logger.handlers}")
print(f"Handlers for root logger: {logging.getLogger().handlers}")

def syncro_create_customer(customer_data: dict):
    """
    Create a new customer in SyncroMSP.

    Args:
        customer_data (dict): Dictionary containing customer details (e.g., name, email, phone, address).

    Returns:
        dict: Response data from the API, or None if an error occurs.
    """
    endpoint = "/customers"
    
    # Get existing customers and check for duplicates

    new_customer = {"business_name": customer_data.get("business_name")}
    customer_name = list(new_customer.values())[0]

    duplicate = check_duplicate_customer(customer_name )
    
    if duplicate:
        logger.warning(f"Duplicate customer found: {duplicate}")
        return None
    
    # Create new customer if no duplicate found
    response = syncro_api_call("POST", endpoint, data=customer_data)
    if response:
        logger.info(f"Successfully created customer: {response.get('customer', {}).get('name', 'Unknown')}")
    else:
        logger.error("Failed to create customer.")
    return response

def syncro_create_contact(contact_data: dict):
    """
    Create a new contact in SyncroMSP.

    Args:
        contact_data (dict): Dictionary containing contact details (e.g., first_name, last_name, email, customer_id).

    Returns:
        dict: Response data from the API, or None if an error occurs.
    """
    endpoint = "/contacts"
    existing_contacts = syncro_get_all_contacts()
    duplicate = check_duplicate_contact(existing_contacts, contact_data, "email", customer_id=contact_data.get("customer_id"))
    if duplicate:
        logger.warning(f"Duplicate contact found under customer ID {contact_data.get('customer_id')}: {duplicate}")
        return None
    response = syncro_api_call("POST", endpoint, data=contact_data)
    if response:
        logger.info(f"Successfully created contact: {response.get('contact', {}).get('first_name', 'Unknown')} {response.get('contact', {}).get('last_name', '')}")
    else:
        logger.error("Failed to create contact.")
    return response

def syncro_create_ticket(ticket_data: dict) -> dict:
    """
    Create a new ticket in SyncroMSP using the specified fields.

    Args:
        ticket_data (dict): Dictionary containing ticket details.

    Returns:
        dict: Response data from the API, or None if an error occurs.
    """
    from syncro_read import get_syncro_ticket_by_number, increment_api_call_count

    endpoint = "/tickets"
    try:
        # Extract the ticket number from the payload
        ticket_number = ticket_data.get("number")
        if ticket_number:
            # Check if the ticket number already exists
            existing_ticket = get_syncro_ticket_by_number(ticket_number)
            if existing_ticket:
                logger.warning(f"Ticket number '{ticket_number}' already taken. Skipping ticket creation.")
                return None
        else:
            logger.error("Ticket number is missing from the payload.")
        # Prepare the ticket payload using the provided fields
        payload = ticket_data        

        # Log the prepared payload
        logger.info(f"Creating a ticket with payload: {payload}")

        # Send the API call
        increment_api_call_count()
        response = syncro_api_call("POST", endpoint, data=payload)

        # Handle the response
        if response and "error" not in response:
            logger.info(f"Successfully created ticket: {response.get('ticket', {}).get('number', 'Unknown')}")
            return response
        else:
            logger.error(f"Failed to create ticket. Response: {response}")
            return None

    except requests.exceptions.HTTPError as http_err:
        # Log HTTP error details
        logger.error(f"HTTP error occurred: {http_err}")
        if hasattr(http_err, 'response') and http_err.response is not None:
            logger.error(f"Response content: {http_err.response.text}")
        return None

    except Exception as e:
        # Log unexpected errors
        logger.error(f"Unexpected error occurred while creating ticket: {e}")
        return None


def syncro_create_comment(comment_data: dict, created_ticket_id: int = None) -> dict:
    """
    Create a new comment in SyncroMSP using the specified fields.

    Args:
        comment_data (dict): Dictionary containing comment details.
        created_ticket_id (int, optional): ID of the newly created ticket (if available).

    Returns:
        dict: Response data from the API, or None if an error occurs.
    """
    from syncro_read import get_syncro_ticket_by_number, increment_api_call_count
    from pprint import pprint

    try:
        # Extract the ticket number from the payload
        ticket_number = comment_data.get("ticket_number")

        # Use created_ticket_id if provided, otherwise fetch from Syncro
        if created_ticket_id:
            ticket_id = created_ticket_id
            logger.info(f"Using provided created_ticket_id: {ticket_id}")
        else:
            if not ticket_number:
                logger.error("Ticket number is missing from the payload.")
                return None

            # Check if the ticket number already exists
            existing_ticket = get_syncro_ticket_by_number(ticket_number)
            if existing_ticket is None:
                logger.warning(f"Ticket number '{ticket_number}' is not found. Skipping comment creation.")
                return None

            # Extract ticket ID
            ticket_id = existing_ticket.get("id")
            if not ticket_id:
                logger.error(f"Failed to retrieve ticket ID for ticket number '{ticket_number}'.")
                return None

        endpoint = f"/tickets/{ticket_id}/comment"

        # Prepare the comment payload
        payload = {
            "subject": comment_data.get("subject", "none"),
            "body": comment_data.get("body", "none"),
            "hidden": comment_data.get("hidden", True),
            "do_not_email": comment_data.get("do_not_email", True),
            "tech": comment_data.get("tech", "none"),
            "created_at": comment_data.get("created_at", ""),
        }

        # Log the prepared payload
        logger.info(f"Creating a comment for ticket {ticket_id} with payload: {payload}")

        # Send the API call
        increment_api_call_count()
        response = syncro_api_call("POST", endpoint, data=payload)

        # Handle the response
        if response and "error" not in response:
            logger.info(f"✅ Successfully created comment for ticket {ticket_id}.")
            return response
        else:
            logger.error(f"❌ Failed to create comment. Response: {response}")
            return None

    except requests.exceptions.HTTPError as http_err:
        # Log HTTP error details
        logger.error(f"HTTP error occurred: {http_err}")
        if hasattr(http_err, 'response') and http_err.response is not None:
            logger.error(f"Response content: {http_err.response.text}")
        return None

    except Exception as e:
        # Log unexpected errors
        logger.error(f"Unexpected error occurred while creating comment: {e}", exc_info=True)
        return None




if __name__ == "__main__":
   
    ticket_data = {
        "ticket customer": "Test Company Name",
        "ticket subject": "Example Ticket",
        "ticket priority": "High",
        "ticket status": "Open"
    }
    created_ticket = syncro_create_ticket(ticket_data)
    
