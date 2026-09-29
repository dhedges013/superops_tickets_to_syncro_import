# SuperOps to Syncro Ticket Importer

## Setup Instructions

1a. **Create a Local Config File**
   - Copy `local_config.example.py` to `local_config.py`.
   - `local_config.py` is gitignored and is the preferred place for real credentials.

1b. **Configure Syncro API Access**
   - Set `SYNCRO_SUBDOMAIN` and `SYNCRO_API_KEY` in `local_config.py`.
   - Adjust `SYNCRO_TIMEZONE` in `syncro_configs.py` if needed.
   - The values in `syncro_configs.py` remain as commented placeholder defaults and can be used as a fallback example.

1c. **Configure SuperOps API Access**
   - Set `SUPEROPS_API_KEY`, `SUPEROPS_BASE_URL`, and `SUPEROPS_CUSTOMER_SUBDOMAIN` in `local_config.py`.
   - The values at the top of `main_SuperOpsTickets_import.py` remain as commented placeholder defaults and can be used as a fallback example.

1d. **Choose Import Mode**
   - Set `DRY_RUN = True` in `local_config.py` to compare SuperOps and Syncro data without creating any tickets or comments.
   - Set `DRY_RUN = False` in `local_config.py` when you are ready to perform the actual import.
   - In dry-run mode, the tool will report which tickets `would_create`, which are `skipped_duplicate`, and which would fail before import.
   - Set `MAX_TICKETS_TO_IMPORT` to a positive number to cap a run, or `None` for no cap.
   - Set `SUPEROPS_TICKETS_CREATED_WITHIN_DAYS` to limit the source tickets by age, or `None` to include all dates.

2. **How the Import Works**
   - The importer works through one client at a time.
   - It reads a ticket, checks whether it is already in Syncro, and then creates it or skips it before moving on.
   - The importer slows down when needed and retries temporary connection problems.
   - If a ticket appears more than once, the duplicate is skipped.
   - The importer saves some Syncro information in `syncro_temp_data.json` to make later runs faster. Delete this file after adding new technicians, customers, contacts, issue types, or statuses.

4. **Logs & File Management**
   - Log files are stored in the `logs` folder.  
   - A new log file is created for each run.
   - The console shows concise, human-readable progress from SuperOps and Syncro. More detailed messages remain in the run log.
   - Progress is reported every 10 completed tickets per client, with additional client and end-of-run totals.
   - Typical progress messages include the number of clients found, tickets read from SuperOps, tickets processed in Syncro, and created/skipped/failed counts.


4. **Notes**
     - Conversations and notes are added as private comments to avoid accidentally emailing end users.
     - Dry-run mode checks what would happen without changing Syncro.
     - If a run is interrupted, already-created tickets remain in Syncro and can be recognized as duplicates on a later run.
