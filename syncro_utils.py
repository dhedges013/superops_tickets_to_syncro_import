import os
import sys
import time
from datetime import datetime
import json
from functools import wraps
import requests
import logging
import time
from typing import Any, Dict, List
import csv
from syncro_configs import SYNCRO_API_BASE_URL, SYNCRO_API_KEY, get_logger, TEMP_FILE_PATH, RATE_LIMIT_SECONDS

import logging
import re

import pytz  # Make sure to install pytz if not already installed
from syncro_configs import SYNCRO_TIMEZONE
_temp_data_cache = None  # Global cache for temp data

# Establish a session for Syncro API interactions with default headers
session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {SYNCRO_API_KEY}",
    "accept": "application/json",
    "Content-Type": "application/json",
})


def load_or_fetch_temp_data(logger: logging.Logger, force_refresh: bool = False) -> dict:
    """
    Load temp data from a file or fetch from Syncro API if file doesn't exist, or if force_refresh is True.

    Args:
        logger (logging.Logger): Logger instance for logging.
        force_refresh (bool): If True, deletes the temp file and fetches new data.

    Returns:
        dict: Dictionary containing techs, issue types, customers, and contacts.
    """
    global _temp_data_cache  # Use a global variable to cache temp data

    # Handle force refresh or load from cache
    if force_refresh and os.path.exists(TEMP_FILE_PATH):
        try:
            logger.info(f"Temp data file {TEMP_FILE_PATH} exists. Deleting it to create a new one.")
            os.remove(TEMP_FILE_PATH)
        except Exception as e:
            logger.error(f"Failed to delete temp data file: {e}")
            raise

    # Check if data is already cached in memory
    if _temp_data_cache:
        logger.info("Using cached temp data.")
        return _temp_data_cache

    # Check if temp file exists
    if os.path.exists(TEMP_FILE_PATH):
        try:
            logger.info(f"Loading temp data from {TEMP_FILE_PATH}")
            with open(TEMP_FILE_PATH, "r") as file:
                _temp_data_cache = json.load(file)
                return _temp_data_cache
        except Exception as e:
            logger.error(f"Failed to load temp data from file: {e}")

    # Fetch data from Syncro API
    from syncro_read import syncro_get_all_techs, syncro_get_issue_types, syncro_get_all_customers, syncro_get_all_contacts, syncro_get_ticket_statuses

    logger.info("Fetching data from Syncro API...")
    try:
        techs = syncro_get_all_techs()
        issue_types = syncro_get_issue_types()
        customers = syncro_get_all_customers()
        contacts = syncro_get_all_contacts()
        statuses = syncro_get_ticket_statuses()

        _temp_data_cache = {
            "techs": techs,
            "issue_types": issue_types,
            "customers": customers,
            "contacts": contacts,
            "statuses": statuses
        }

        # Save to temp file
        logger.info(f"Saving temp data to {TEMP_FILE_PATH}")
        with open(TEMP_FILE_PATH, "w") as file:
            json.dump(_temp_data_cache, file)

    except Exception as e:
        logger.error(f"Failed to fetch data from Syncro API or save temp data: {e}")
        raise

    return _temp_data_cache

# Add parent directory to sys.path for imports
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, parent_dir)

# Get a logger for this module
logger = get_logger(__name__)
logger.debug(f"Handlers for {logger.name}: {logger.handlers}")
logger.debug(f"Handlers for root logger: {logging.getLogger().handlers}")

def get_customer_id_by_name(customer_name: str):#, logger: logging.Logger) -> int:
    """
    Retrieve customer ID from temp data based on matching customer name.

    Args:
        customer_name (str): Customer name to search for.
        logger (logging.Logger): Logger instance for logging.

    Returns:
        int: Customer ID if found, otherwise None.

    Logs:
        - Info for successful matches.
        - Warning if no match is found.
        - Error if an issue occurs during execution.
    """
    try:
        # Load temp data
        temp_data = load_or_fetch_temp_data(logger, force_refresh=False)
        customers = temp_data.get("customers", [])

        if not customers:
            logger.warning("No customer data available. Ensure temp data is correctly loaded.")
            return None

        # Normalize input for case-insensitive comparison
        normalized_customer_name = customer_name.strip().lower()

        # Search for the customer by name
        for customer in customers:
            customer_name_in_list = customer.get("business_name", "").strip().lower()
            if customer_name_in_list == normalized_customer_name:
                customer_id = customer.get("id")
                logger.info(f"Match found: Customer '{customer_name}' matches '{customer['business_name']}' with ID {customer_id}")
                return customer_id

        logger.warning(f"Customer not found: {customer_name}")
        return None

    except KeyError as e:
        logger.error(f"Key error while accessing customer data: {e}")
        return None

    except Exception as e:
        logger.error(f"An unexpected error occurred in get_customer_id_by_name: {e}")
        return None
  
def syncro_api_call(method: str, endpoint: str, data: dict = None, params: dict = None):
    """
    Generic API call to SyncroMSP.

    Args:
        method (str): HTTP method (e.g., 'POST', 'PUT', 'GET').
        endpoint (str): API endpoint.
        data (dict): JSON payload for the request (optional).
        params (dict): Query parameters for the request (optional).

    Returns:
        dict: JSON response from the API.
    """
    from syncro_read import increment_api_call_count
    increment_api_call_count()
    url = f"{SYNCRO_API_BASE_URL}{endpoint}"

    try:

       response = session.request(method, url, json=data, params=params)
        time.sleep(RATE_LIMIT_SECONDS)

        response.raise_for_status()  # Raise HTTPError for bad responses
        return response.json() if response.content else {}
    except requests.HTTPError as http_err:
        logger.error(f"HTTP error occurred: {http_err}")
        raise
    except requests.RequestException as req_err:
        logger.error(f"Request error occurred: {req_err}")
        raise

def check_duplicate_customer(customer_name: str, logger: logging.Logger) -> bool:
    """
    Check if a customer with the given name already exists using temp data.

    Args:
        customer_name (str): Name of the customer to check.
        logger (logging.Logger): Logger instance for logging.

    Returns:
        bool: True if the customer exists, False otherwise.

    Logs:
        - Info for successful checks.
        - Warning if a duplicate customer is found.
        - Error if any issue occurs during execution.
    """
    try:
        # Load temp data
        temp_data = load_or_fetch_temp_data(logger, force_refresh=False)
        customers = temp_data.get("customers", [])

        if not customers:
            logger.warning("No customer data available. Ensure temp data is correctly loaded.")
            return False

        # Normalize input for case-insensitive comparison
        normalized_customer_name = customer_name.strip().lower()

        # Extract and normalize business names from customers
        business_names = [customer.get("business_name", "").strip().lower() for customer in customers]

        logger.info(f"Retrieved normalized business names: {business_names}")
        logger.info(f"Checking for duplicate customer: {customer_name}")

        # Check for duplicate
        if normalized_customer_name in business_names:
            logger.warning(f"Duplicate customer found: {customer_name}")
            return True

        logger.info(f"No duplicate found for customer: {customer_name}")
        return False

    except KeyError as e:
        logger.error(f"Key error while accessing customer data: {e}")
        return False

    except Exception as e:
        logger.error(f"An unexpected error occurred in check_duplicate_customer: {e}")
        return False
def check_duplicate_contact(contact_name: str, logger: logging.Logger) -> bool:
    """
    Check if a contact with the given name already exists using temp data.

    Args:
        contact_name (str): Name of the contact to check.
        logger (logging.Logger): Logger instance for logging.

    Returns:
        bool: True if the contact exists, False otherwise.

    Logs:
        - Info for successful checks.
        - Warning if a duplicate contact is found.
        - Error if any issue occurs during execution.
    """
    try:
        # Load temp data
        temp_data = load_or_fetch_temp_data(logger, force_refresh=False)
        contacts = temp_data.get("contacts", [])

        if not contacts:
            logger.warning("No contact data available. Ensure temp data is correctly loaded.")
            return False

        # Normalize input for case-insensitive comparison
        normalized_contact_name = contact_name.strip().lower()

        # Extract and normalize contact names
        contact_names = [contact.get("name", "").strip().lower() for contact in contacts]

        logger.info(f"Retrieved normalized contact names: {contact_names}")
        logger.info(f"Checking for duplicate contact: {contact_name}")

        # Check for duplicate
        if normalized_contact_name in contact_names:
            logger.warning(f"Duplicate contact found: {contact_name}")
            return True

        logger.info(f"No duplicate found for contact: {contact_name}")
        return False

    except KeyError as e:
        logger.error(f"Key error while accessing contact data: {e}")
        return False

    except Exception as e:
        logger.error(f"An unexpected error occurred in check_duplicate_contact: {e}")
        return False

def extract_nested_key(data: dict, key_path: str):
    """
    Extract a nested key from a dictionary using dot notation.

    Args:
        data (dict): Dictionary to search.
        key_path (str): Dot-separated path to the key.

    Returns:
        Any: Value of the nested key if it exists, otherwise None.
    """
    keys = key_path.split('.')
    for key in keys:
        if isinstance(data, dict) and key in data:
            data = data[key]
        else:
            return None
    return data

def load_csv(filepath: str, required_fields: List[str] = None, logger: logging.Logger = None) -> List[Dict[str, Any]]:

    """
    Load data from a CSV file with validation for required fields.

    Args:
        filepath (str): The path to the CSV file.
        required_fields (List[str]): List of required field names to validate.
        logger (logging.Logger, optional): Logger instance for logging.

    Returns:
        List[Dict[str, Any]]: A list of dictionaries, where each dictionary represents a row in the CSV file.

    Raises:
        FileNotFoundError: If the file is not found.
        ValueError: If required fields are missing in the CSV file.
    """
    if logger is None:
        logger = logging.getLogger("syncro")

    try:
        logger.info(f"Loading data from CSV file: {filepath}")
        with open(filepath, mode="r", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            headers = reader.fieldnames
            if required_fields:
                missing_fields = [field for field in required_fields if field not in headers]
                if missing_fields:
                    raise ValueError(f"Missing required fields in CSV file: {missing_fields}")

            data = []
            for row_number, row in enumerate(reader, start=1):
                # Handle missing or blank values
                cleaned_row = {key: (value if value else None) for key, value in row.items()}

                # Log warning if required fields are blank
                if required_fields:
                    for field in required_fields:
                        if not cleaned_row.get(field):
                            logger.warning(f"Row {row_number}: Missing value for required field '{field}'.")

                data.append(cleaned_row)

            logger.info(f"Successfully loaded {len(data)} rows from {filepath}.")
            return data

    except FileNotFoundError:
        logger.error(f"CSV file not found: {filepath}")
        raise
    except ValueError as e:
        logger.error(f"Validation error in CSV file: {e}")
        raise
    except Exception as e:
        logger.error(f"Error reading CSV file {filepath}: {e}")
        raise

def get_syncro_ticket_number(ticketNumber: str) -> str:
    """
    Cleans the ticket number to ensure it contains only numeric characters.

    Args:
        ticketNumber (str): The input ticket number.

    Returns:
        str: A string with only numeric characters from the input.

    Logs:
        - Info if ticket number is cleaned successfully.
        - Error if an unexpected issue occurs.
    """
    try:
        # Remove any non-numeric characters
        cleaned_ticket_number = ''.join(filter(str.isdigit, ticketNumber))

        # Log the original and cleaned ticket number
        logger.info(f"Original ticket number: {ticketNumber}")
        logger.info(f"Cleaned ticket number: {cleaned_ticket_number}")

        return cleaned_ticket_number

    except Exception as e:
        # Log the error and raise it for further handling
        logger.error(f"Error processing ticket number '{ticketNumber}': {e}")
        raise

def get_syncro_tech(tech_name: str):
    """
    Get the ID of a technician by name (case-insensitive).

    Args:
        tech_name (str): Name of the technician to search for.
        logger (logging.Logger): Logger instance for logging.

    Returns:
        str: Technician ID, or None if not found.
    """
    try:
        # Load temp data
        temp_data = load_or_fetch_temp_data(logger)
        techs = temp_data.get("techs", [])

        # Check if tech data exists
        if not techs:
            logger.error("No technician data available. Ensure temp data is correctly loaded.")
            return None

        # Normalize input to lowercase for case-insensitive comparison
        normalized_tech_name = tech_name.strip().lower()

        # Search for the technician by name (case-insensitive)
        for tech in techs:
            tech_id = None
            tech_name_in_list = None

            if isinstance(tech, dict):
                # If entry is a dictionary, extract fields using keys
                tech_id = tech.get("id")
                tech_name_in_list = tech.get("name", "").strip().lower()
            elif isinstance(tech, list) and len(tech) >= 2:
                # If entry is a list, assume [id, name] structure
                tech_id, tech_name_in_list_raw = tech[0], tech[1]
                tech_name_in_list = str(tech_name_in_list_raw).strip().lower()
            else:
                # Log and skip unexpected entry formats
                logger.warning(f"Unexpected tech entry format: {tech}. Skipping entry.")
                continue

            if tech_name_in_list == normalized_tech_name:
                logger.info(f"Match found: Tech '{tech_name}' matches '{tech_name_in_list}' with ID {tech_id}")
                return str(tech_id)

        # Log a warning if the technician is not found
        logger.warning(f"Technician not found: {tech_name}")
        return None

    except KeyError as e:
        logger.error(f"Key error while accessing tech data: {e}")
        return None

    except Exception as e:
        logger.error(f"An unexpected error occurred in get_syncro_tech: {e}")
        return None



def build_syncro_initial_issue(initial_issue: str, syncroContact: str, created_at: str) -> list:
    """
    Build the JSON object for the initial issue in Syncro.

    Args:
        initial_issue (str): The issue description.
        syncroContact (str): The Syncro contact associated with the issue.
        created_at (str): Timestamp for the initial issue comment.

    Returns:
        list: A list representing the comments for the initial issue.

    Logs:
        - Info for successfully built JSON object.
        - Error if inputs are invalid or unexpected issues occur.
    """
    def extract_user_name(variable):
        """
        If the variable matches the expected structure (a list containing a dictionary with 'user' key),
        extract the 'name' field. Otherwise, return the original variable.

        Args:
            variable: The input variable.

        Returns:
            Extracted name if the structure matches, otherwise the original variable.
        """
        if isinstance(variable, list) and len(variable) > 0 and isinstance(variable[0], dict) and "user" in variable[0]:
            return variable[0]["user"].get("name", variable)  # Extract 'name' or return original variable if missing

        return variable  # Return original variable if structure doesn't match
    
    syncroContact = extract_user_name(syncroContact)
    
    try:
        # Validate inputs
        initial_issue = initial_issue if initial_issue else "none"
        syncroContact = syncroContact if syncroContact else "none"

        # Build the JSON structure as a list of comments
        initial_issue_comments = [
            {
                "subject": "initial issue",
                "body": initial_issue,
                "hidden": True,
                "do_not_email": True,
                "tech": syncroContact,
                "created_at": created_at
            }
        ]

        

        # Log the built JSON
        logger.info(f"Successfully built initial issue comments: {initial_issue_comments}")

        return initial_issue_comments

    except ValueError as ve:
        # Log value errors
        logger.error(f"Input validation error: {ve}")
        raise
    except Exception as e:
        # Log unexpected errors
        logger.error(f"Unexpected error occurred while building initial issue comments: {e}")
        raise

def build_syncro_comment(comment: dict) -> dict:
    """
    Build the JSON object for creating a Syncro comment.

    Args:
        comment (dict): A dictionary containing:
            - "content" (str): The comment body.
            - "time" (str): The timestamp (kept blank if missing).
            - "type" (str): The comment type (maps to subject).
            - "user" (str): The technician's name.

    Returns:
        dict: JSON object for Syncro comment creation.

    Logs:
        - Info for successfully built JSON object.
        - Error if inputs are invalid.
    """
    logger.debug(f"Calling build_syncro_comment in syncro_utils.py")
    logger.debug(f"Raw comment data received: {comment}")
    input("Waiting: ")
    try:
        logger.debug(f"Raw comment data received: {comment}")

        # Ensure required keys exist with correct defaults
        content = comment.get("content")
        time = comment.get("time", "")  # Keep empty if missing
        type_ = comment.get("type")
        user = comment.get("user")

        # 🛠️ Handle missing values properly
        if content is None:
            content = "none"

        if type_ is None:
            type_ = "none"

        if user is None:
            user = "none"

        # Create formatted Syncro comment
        syncro_comment = {
            "subject": type_,
            "body": content,
            "hidden": True,
            "do_not_email": True,
            "tech": user,
            "created_at": time
        }

        # Log the built JSON
        logger.info(f"Successfully built Syncro comment: {syncro_comment}")

        return syncro_comment

    except Exception as e:
        logger.error(f"Unexpected error occurred while building Syncro comment: {e}", exc_info=True)
        raise



def get_syncro_created_date(created: str) -> str:
    """
    Process a date or string that looks like a date and reformat it to ISO 8601 format with the local timezone.

    Args:
        created (str): Input date string.

    Returns:
        str: Reformatted date string in ISO 8601 format with timezone offset (e.g., 2024-12-15T00:00:00-05:00).

    Logs:
        - Info for successfully parsed and formatted dates.
        - Error if the input cannot be processed.
    """
    def is_iso8601_with_tz(date_str: str) -> bool:
        """
        Check if a given string is in ISO 8601 format with timezone offset.

        Expected format: YYYY-MM-DDTHH:MM:SS±HHMM (e.g., 2025-02-07T19:21:47-0500)

        Args:
            date_str (str): Input date string.

        Returns:
            bool: True if the string matches the format, otherwise False.
        """
        iso8601_tz_pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4}$"

        return bool(re.match(iso8601_tz_pattern, date_str))
    
    test = is_iso8601_with_tz(created)
    if test == True:
        logger.info(f"✅ Date '{created}' is in the correct ISO 8601 format with timezone.")
        return created
    else:
        logger.warning(f"⚠️ Date '{created}' is NOT in the correct ISO 8601 format with timezone.")    
    try:
        #logger.info(f"Attempting to parse and format date: {created}")

        # Define common input formats for parsing
        formats = [
            "%Y-%m-%d",               # Date only (YYYY-MM-DD)
            "%m/%d/%Y",               # MM/DD/YYYY
            "%d-%m-%Y",               # DD-MM-YYYY
            "%Y-%m-%d %H:%M:%S",      # Full datetime (YYYY-MM-DD HH:MM:SS)
            "%Y/%m/%d %H:%M",         # Datetime without seconds (YYYY/MM/DD HH:MM)
            "%m/%d/%Y %H:%M",         # MM/DD/YYYY with time
            "%m-%d-%y",               # MM-DD-YY
            "%Y-%m-%dT%H:%M:%S",      # ISO 8601 without milliseconds
            "%Y-%m-%dT%H:%M:%S.%f"    # ISO 8601 with milliseconds
        ]
        
        # Attempt to parse with provided formats
        parsed_date = None
        for fmt in formats:
            try:
                parsed_date = datetime.strptime(created, fmt)
                break
            except ValueError:
                continue

        if parsed_date is None:
            raise ValueError(f"Unrecognized date format: {created}")

        # Localize the date to SYNCRO_TIMEZONE
        local_timezone = pytz.timezone(SYNCRO_TIMEZONE)
        localized_date = local_timezone.localize(parsed_date)

        # Format the date with timezone offset
        formatted_date = localized_date.strftime("%Y-%m-%dT%H:%M:%S%z")

        #logger.info(f"Formatted date with timezone offset: {formatted_date}")
        return formatted_date

    except ValueError as ve:
        logger.error(f"ValueError: {ve}")
        raise
    except Exception as e:
        logger.error(f"Error processing date '{created}': {e}")
        raise

def get_syncro_customer_contact(customerid: str, contact: str):
    """
    Find the closest matching contact ID for a contact name within a specific customer.

    Args:
        customerid (str): The ID of the customer.
        contact (str): The name of the contact to find.

    Returns:
        int: The ID of the closest matching contact, or None if no match is found.

    Logs:
        - Info for customer and contact searches.
        - Warning if no matching contact is found.
        - Info on the closest match and its similarity score.
        - Error if any issue occurs during execution.
    """
    from difflib import get_close_matches, SequenceMatcher

    try:
        # Validate inputs
        if not contact:
            logger.warning(f"Contact name is missing or None for customer ID: {customerid}")
            return None

        # Load temp data
        temp_data = load_or_fetch_temp_data(logger)
        contacts_data = temp_data.get("contacts", [])

        # Log the search process
        logger.info(f"Looking up customer ID for customer: {customerid}")

        if not customerid:
            logger.warning(f"Customer '{customerid}' not found.")
            return None

        # Filter contacts for the given customer ID
        customer_contacts = [
            c for c in contacts_data if c.get("customer_id") == customerid
        ]

        if not customer_contacts:
            logger.warning(f"No contacts found for customer ID: {customerid}")
            return None

        # Normalize the input contact name and contact keys
        normalized_contact = contact.strip().lower()
        normalized_contact_dict = {
            c.get("name", "").strip().lower(): c.get("id")
            for c in customer_contacts
            if c.get("name") and c.get("id")
        }

        # Find closest match using difflib
        closest_matches = get_close_matches(normalized_contact, normalized_contact_dict.keys(), n=1, cutoff=0.4)

        if closest_matches:
            closest_match = closest_matches[0]
            contact_id = normalized_contact_dict[closest_match]
            logger.info(f"Closest match found: '{closest_match}' for contact '{contact}' with ID {contact_id}")
            return contact_id

        # Compute similarity scores for all contacts
        logger.info(f"No exact match found. Computing similarity scores for '{contact}'.")
        scores = [
            (name, SequenceMatcher(None, normalized_contact, name).ratio())
            for name in normalized_contact_dict.keys()
        ]

        # Check if scores is empty
        if not scores:
            logger.warning(f"No valid scores available for contact '{contact}' in customer '{customerid}'")
            return None

        # Find the best score
        best_match, best_score = max(scores, key=lambda item: item[1])
        logger.info(f"Closest match (by score): '{best_match}' with similarity score {best_score:.2f}")

        # Fallback to substring search
        logger.info(f"Trying substring matching for '{contact}'")
        for name, contact_id in normalized_contact_dict.items():
            if normalized_contact in name:
                logger.info(f"Substring match found: '{name}' for contact '{contact}' with ID {contact_id}")
                return contact_id

        # Log a warning if no close or substring match is found
        logger.warning(f"No close or substring match found for contact '{contact}' in customer '{customerid}'")
        return None

    except Exception as e:
        # Log any unexpected errors
        logger.error(f"Error occurred while finding contact '{contact}' for customer '{customerid}': {e}")
        raise

def get_syncro_priority(priority: str) -> str:
    """
    Match a given priority string with the corresponding Syncro priority.

    Args:
        priority (str): The priority level (e.g., 'Urgent', 'High', 'Normal', 'Low').

    Returns:
        str: The matched priority string (e.g., '1 High').

    Logs:
        - Info for successful matching.
        - Warning if no match is found.
    """
    try:
        # Define priority mappings
        priority_map = {
            "urgent": "0 Urgent",
            "high": "1 High",
            "normal": "2 Medium",
            "low": "3 Low"
        }

        # Normalize the input to lowercase for case-insensitive matching
        normalized_priority = priority.strip().lower()

        # Attempt to find the match
        matched_priority = priority_map.get(normalized_priority)

        if matched_priority:
            logger.info(f"Priority '{priority}' matched to '{matched_priority}'")
            return matched_priority
        else:
            logger.warning(f"No match found for priority: {priority}")
            return None

    except Exception as e:
        # Log any unexpected errors
        logger.error(f"Error occurred while matching priority '{priority}': {e}")
        raise

def get_syncro_issue_type(issue_type: str):
    """
    Match the given issue type with Syncro issue types and return the matched type.

    Args:
        issue_type (str): The issue type to find.
        logger (logging.Logger): Logger instance for logging.

    Returns:
        str: The matched Syncro issue type if found, otherwise None.

    Logs:
        - Info for successful matching.
        - Warning if no match is found.
        - Error if any issue occurs during execution.
    """
    try:
        # Load temp data
        temp_data = load_or_fetch_temp_data(logger, force_refresh=False)
        issue_types = temp_data.get("issue_types", [])

        if not issue_types:
            logger.warning("No issue types found in Syncro settings.")
            return None

        # Normalize the input for case-insensitive comparison
        normalized_issue_type = issue_type.strip().lower()

        # Search for a match in the retrieved issue types
        for syncro_issue_type in issue_types:
            if syncro_issue_type.strip().lower() == normalized_issue_type:
                logger.info(f"Match found: Input '{issue_type}' matches Syncro issue type '{syncro_issue_type}'.")
                return syncro_issue_type

        # Log a warning if no match is found
        logger.warning(f"No match found for issue type: {issue_type}")
        return None

    except KeyError as e:
        logger.error(f"Key error while accessing issue types: {e}")
        return None

    except Exception as e:
        logger.error(f"Error occurred while matching issue type '{issue_type}': {e}")
        return None

def syncro_get_all_tickets_from_csv(logger: logging.Logger = None) -> List[Dict[str, Any]]:
    """
    Load all tickets from a CSV file.

    Args:
        logger (logging.Logger, optional): Logger instance for logging.

    Returns:
        List[Dict[str, Any]]: A list of dictionaries, where each dictionary represents a ticket.

    Raises:
        Exception: If loading tickets fails for any reason.
    """
    from syncro_configs import TICKETS_CSV_PATH

    required_fields = [
        "ticket customer",
        "ticket number",
        "ticket subject",
        "tech",
        "ticket initial issue",
        "ticket status",
        "ticket issue type",
        "ticket created"
    ]

    # Ensure logger is initialized
    if logger is None:
        logger = logging.getLogger("syncro")
    
    try:
        logger.info("Attempting to load tickets from CSV...")
        tickets = load_csv(TICKETS_CSV_PATH, required_fields=required_fields, logger=logger)
        logger.info(f"Successfully loaded {len(tickets)} tickets from {TICKETS_CSV_PATH}.")
        return tickets

    except FileNotFoundError:
        logger.error(f"CSV file not found: {TICKETS_CSV_PATH}")
        raise

    except ValueError as e:
        logger.error(f"Validation error in CSV file: {e}")
        raise

    except Exception as e:
        logger.error(f"An unexpected error occurred while loading tickets: {e}")
        raise

def syncro_get_all_comments_from_csv(logger: logging.Logger = None) -> List[Dict[str, Any]]:
    """
    Load all comments from a CSV file.

    Args:
        logger (logging.Logger, optional): Logger instance for logging.

    Returns:
        List[Dict[str, Any]]: A list of dictionaries, where each dictionary represents a ticket.

    Raises:
        Exception: If loading comments fails for any reason.
    """
    from syncro_configs import COMMENTS_CSV_PATH

    required_fields = [
        "ticket customer",
        "ticket number",                
        "ticket subject",
        "ticket comment",
        "comment contact", 
        "comment created"
    ]

    # Ensure logger is initialized
    if logger is None:
        logger = logging.getLogger("syncro")
    
    try:
        logger.info("Attempting to load comments from CSV...")
        comments = load_csv(COMMENTS_CSV_PATH, required_fields=required_fields, logger=logger)
        logger.info(f"Successfully loaded {len(comments)} comments from {COMMENTS_CSV_PATH}.")
        return comments

    except FileNotFoundError:
        logger.error(f"CSV file not found: {COMMENTS_CSV_PATH}")
        raise

    except ValueError as e:
        logger.error(f"Validation error in CSV file: {e}")
        raise

    except Exception as e:
        logger.error(f"An unexpected error occurred while loading comments: {e}")
        raise



def syncro_prepare_ticket_json_superops(client, contact, ticket_id, subject, converted_created_time, status, priority, assigned_tech, description, timeline):
    """
    Extract ticket data into variables and create a JSON package for Syncro ticket creation.
    """
    # Extract individual fields
    customer = client
    subject = subject
    tech = assigned_tech
    initial_issue = description
    status = status    
    created = converted_created_time
    contact = contact if contact else "None"
    priority = priority if priority else "Normal"

    # Process fields
    customer_id = get_customer_id_by_name(customer)    
    syncro_tech = get_syncro_tech(tech)
    syncro_created_date = get_syncro_created_date(created)
    syncro_contact = get_syncro_customer_contact(customer_id, contact)
    initial_issue_comments = build_syncro_initial_issue(initial_issue, contact, syncro_created_date)
    syncro_priority = get_syncro_priority(priority)

    # Create JSON payload
    ticket_json = {
        "ticket_number": None,
        "customer_id": customer_id,        
        "subject": subject,
        "user_id": syncro_tech,
        "comments_attributes": initial_issue_comments,
        "status": status,        
        "created_at": syncro_created_date,
        "contact_id": syncro_contact,
        "priority": syncro_priority,
    }

    # Remove keys with None values
    ticket_json = {key: value for key, value in ticket_json.items() if value is not None}

    return ticket_json

def syncro_prepare_ticket_json(ticket):
    """
    Extract ticket data into variables and create a JSON package for Syncro ticket creation.
    Removes fields with None values.

    Args:
        ticket (dict): Ticket data dictionary.

    Returns:
        dict: JSON payload for Syncro ticket creation.
    """
    # Extract individual fields
    customer = ticket.get("ticket customer")
    ticket_number = ticket.get("ticket number")
    subject = ticket.get("ticket subject")
    tech = ticket.get("tech")
    initial_issue = ticket.get("ticket initial issue")
    status = ticket.get("ticket status")
    issue_type = ticket.get("ticket issue type")
    created = ticket.get("ticket created")
    contact = ticket.get("ticket contact")
    priority = ticket.get("ticket priority")

    # Process fields
    customer_id = get_customer_id_by_name(customer)
    syncro_ticket_number = get_syncro_ticket_number(ticket_number)
    syncro_tech = get_syncro_tech(tech)
    syncro_created_date = get_syncro_created_date(created)
    syncro_contact = get_syncro_customer_contact(customer_id, contact)
    initial_issue_comments = build_syncro_initial_issue(initial_issue, contact, syncro_created_date)
    syncro_issue_type = get_syncro_issue_type(issue_type)
    syncro_priority = get_syncro_priority(priority)

    # Create JSON payload
    ticket_json = {
        "customer_id": customer_id,
        "number": syncro_ticket_number,
        "subject": subject,
        "user_id": syncro_tech,
        "comments_attributes": initial_issue_comments,
        "status": status,
        "problem_type": syncro_issue_type,
        "created_at": syncro_created_date,
        "contact_id": syncro_contact,
        "priority": syncro_priority,
    }

    # Remove keys with None values
    ticket_json = {key: value for key, value in ticket_json.items() if value is not None}

    return ticket_json



def syncro_prepare_comments_json(comment):
    """
    Extract ticket data into variables and create a JSON package for Syncro ticket creation.
    Removes fields with None values.

    Args:
        ticket (dict): Ticket data dictionary.

    Returns:
        dict: JSON payload for Syncro ticket creation.
    """

    
    required_fields = [
        "ticket customer",
        "ticket number",                
        "ticket subject",
        "ticket comment",
        "comment contact",
        "comment created"
    ]

    # Extract individual fields

    customer = comment.get("ticket customer") #need for contact lookup
    ticket_number = comment.get("ticket number") 
    customer_id = get_customer_id_by_name(customer)
    syncro_ticket_number = get_syncro_ticket_number(ticket_number) 
    subject = comment.get("ticket subject")    
    

    

    ticket_comment = comment.get("ticket comment") # I tried but it didnt work
    comment_created =  comment.get("comment created")       
    comment_contact = comment.get("comment contact") # System, Daniel Hedges, Sally Joe

    # Process fields
    #ticket_comment = build_syncro_initial_issue(ticket_comment, comment_contact)
    syncro_created_date = get_syncro_created_date(comment_created)

    # Create JSON payload
    comment_json = {
        "ticket_number": ticket_number,
        "subject": "API Import",
        "created_at": syncro_created_date,
        "tech": comment_contact,
        "body": ticket_comment,
        "hidden": True,
        "do_not_email": True
    }

    # Remove keys with None values
    comment_json = {key: value for key, value in comment_json.items() if value is not None}

    return comment_json



if __name__ == "__main__":
    print(get_syncro_created_date("2025-02-07T19:21:47.451"))
