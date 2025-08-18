# SuperOps to Syncro Ticket Importer

## Setup Instructions

1a. **Configure Syncro API Access**  
   - Add your **Syncro Subdomain** and **API Key** in the `syncro_configs` file.
   - Adjust your Timezone if needed

1b. **Configure SuperOps API Access**  
    - the fields are API_KEY and CUSTOMER_SUBDOMAIN located at the top of main_SuperOpsTickets_import.py file


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