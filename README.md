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

2. **Import Process & Temporary Data**
   - To speed up the import process, the importer generates a `syncro_temp_data.json` file on the first run of `main_tickets.py`.  
   - If you add new **Techs, Customers, Contacts, Ticket Issue Types, Statuses, etc.**, you **must delete this file** to allow the importer to rebuild it on the next run.

3. **Logs & File Management**  
   - Log files are stored in the `logs` folder.  
   - A new log file is created for each run.


4. SuperOps Ticket Importer Notes
     - All deduping is based of the SuperOps ticket's displayId being in the Subject of the Syncro ticket
     - Ticket Creation and all Conversations / Notes happen together
     - all converstations / notes are added as Private Comment to help avoid mistakes of emailing all your end users
     - Dry-run mode uses the same compare and payload-preparation path as the real import, but skips all Syncro write calls
