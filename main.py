"""
This module serves as the main entry point for the command-line application.

It uses the Typer library to create a CLI that connects to the Gmail API,
fetches emails based on specified labels, and inserts or updates them in a
SQLite database. The application handles fetching all labels, filtering them,
and processing messages in an idempotent manner to avoid duplicates unless an
update is explicitly requested.
"""
import typer
from typing import List, Optional
import os

from gmail_api import GmailAPI
from sqlite_db import SQLiteDB
from config import DATABASE_PATH

# Initialize the Typer application
app = typer.Typer()

@app.command()
def main(
    update: bool = typer.Option(False, "--update", "-u", help="Update existing messages in the database. Default is to only insert new messages."),
    label: Optional[List[str]] = typer.Option(None, "--label", "-l", help="Specify one or more labels to process. If not provided, all labels will be processed."),
    db_directory: str = typer.Option(DATABASE_PATH, "--db-directory", "-d", help="The directory where the mail_database.db file will be stored.")
):
    """
    Connects to Gmail, fetches emails by label, and inserts them into a SQLite database.
    """
    # Construct the full path to the database file.
    db_path = os.path.join(db_directory, "mail_database.db")
    
    # Instantiate the API and database handler classes.
    gmail = GmailAPI()
    db = SQLiteDB(db_path)

    try:
        # --- 1. Connect to Services ---
        print("Connecting to Gmail API...")
        gmail.connect()
        print("Opening database connection...")
        db.open_db()

        # --- 2. Determine Labels to Process ---
        print("Fetching all available labels from Gmail...")
        all_available_labels = gmail.list_tags()
        if not all_available_labels:
            print("Could not retrieve any labels from Gmail. Exiting.")
            return

        # Exclude special "Delete_Status" labels from processing.
        filtered_labels = [l for l in all_available_labels if "Delete_Status" not in l]

        labels_to_process = []
        if label:
            # If user provides specific labels, validate them.
            valid_user_labels = []
            invalid_user_labels = []
            for l in label:
                if l in filtered_labels:
                    valid_user_labels.append(l)
                else:
                    invalid_user_labels.append(l)
            
            if invalid_user_labels:
                print(f"Warning: The following requested labels do not exist or were excluded: {', '.join(invalid_user_labels)}")
            
            if not valid_user_labels:
                print("None of the requested labels are available for processing. Exiting.")
                return
            labels_to_process = valid_user_labels
        else:
            # If no labels are specified, process all available (and non-excluded) labels.
            labels_to_process = filtered_labels
        
        print(f"\nLabels to be processed: {', '.join(labels_to_process)}")

        # --- 3. Fetch Existing Message IDs for Idempotency ---
        # To avoid re-inserting emails, get all existing IDs from the DB first.
        existing_ids = set()
        if not update:
            print("\nFetching existing message IDs from the database for comparison...")
            rows = db.query_db("SELECT message_id FROM emails")
            if rows:
                existing_ids = {row[0] for row in rows}
            print(f"Found {len(existing_ids)} existing messages to skip.")

        # --- 4. Iterate Through Labels and Process Messages ---
        for lbl in labels_to_process:
            print(f"\n--- Processing Label: {lbl} ---")
            query = f"in:{lbl}"
            
            # Get all message IDs for the current label from the Gmail API.
            message_infos = gmail.get_message_ids_and_thread_ids_by_query(query)
            if not message_infos:
                print("No messages found for this label.")
                continue
            
            print(f"Found {len(message_infos)} total messages for this label. Comparing with database...")
            new_messages_found = 0
            
            # Iterate through each message ID found.
            for message_info in message_infos:
                message_id = message_info['id']
                
                # Skip this message if it's already in the database and we're not in update mode.
                if not update and message_id in existing_ids:
                    continue
                
                # Fetch the full email data from the Gmail API.
                print(f"  Fetching full email for message ID: {message_id}")
                email_data = gmail.get_email_by_message_id(message_id)
                
                # Insert or update the message in the SQLite database.
                if email_data:
                    db.insert_message(email_data, update_if_exists=update)
                    new_messages_found += 1

            print(f"--- Finished for label: {lbl}. Processed {new_messages_found} new/updated emails. ---")

    except Exception as e:
        # Catch any unexpected errors during the main process.
        print(f"An unexpected error occurred: {e}")
    finally:
        # --- 5. Clean Up ---
        # Ensure connections are closed properly.
        print("\nClosing database connection.")
        db.close_db()
        print("Disconnecting from Gmail API.")
        gmail.disconnect()

if __name__ == "__main__":
    # Run the Typer application.
    app()
