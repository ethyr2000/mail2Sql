"""This module provides a class, `SQLiteDB`, for managing a SQLite database
that stores email data.

The class handles:
- Opening and closing the database connection.
- Creating the required tables based on data structures defined in `mailStructs`.
- Inserting and updating email messages and their related data, such as
  attachments, headers, and labels, in an idempotent manner.
- Executing queries against the database.
- A utility for loading email data from a JSON file.

The schema is designed to normalize email data, with foreign key relationships
connecting emails to contacts, attachments, and other metadata.
"""
import re
import sqlite3
import json
import datetime
import os
import random
from typing import Dict, Any, List, Optional
import pandas as pd
import pickle
from email.utils import parseaddr, formataddr

from mailStructs import (
    ExtractedEmailData, EmailAddressModel, ContactModel, EmailModel,
    EmailAttachmentModel, EmailXHeaderModel, EmailLabelModel,
    EmailAuthenticationModel
)

import spacy
import numpy as np
from spacy.tokens import DocBin
from scipy.special import softmax

def remove_html(html_string: str) -> str:
    """A simple function to remove HTML tags from a string."""
    return re.sub(r'<[^>]+>', '', html_string)

class SQLiteDB:
    """
    A handler for all SQLite database operations related to email storage.
    """
    def __init__(self, db_path: str):
        """
        Initializes the SQLiteDB handler.

        Args:
            db_path (str): The file path for the SQLite database.
        """
        self.db_path = db_path
        self.conn = None

        self.nlp = spacy.load("en_core_web_md")
        self.category_names = []
        self.category_vectors = []


    def open_db(self):
        """
        Opens the database connection and creates tables if they don't exist.
        
        Ensures the directory for the database file exists before connecting.
        """
        # Ensure the directory for the database file exists.
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
            
        self.conn = sqlite3.connect(self.db_path)
        # Enable foreign key support.
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.create_tables()

    def create_tables(self):
        """
        Creates all necessary tables based on the mailStructs data models.
        
        This method is idempotent; it will not recreate tables that already exist.
        """
        if not self.conn:
            return

        cursor = self.conn.cursor()

        # --- Table: contacts ---
        # Stores information about individual contacts.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            contact_id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT,
            last_name TEXT,
            common_name TEXT,
            interest_keywords TEXT,
            family_members TEXT,
            church TEXT,
            employer TEXT,
            family_proximity INTEGER,
            physical_proximity INTEGER,
            business_proximity INTEGER,
            digital_proximity INTEGER,
            interest_proximity INTEGER,
            church_proximity INTEGER
        )"""
        )

        # --- Table: email_address ---
        # Stores unique email addresses and links them to contacts.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_address (
            email TEXT PRIMARY KEY,
            display_name TEXT,
            contact_id INTEGER,
            interest_keywords TEXT,
            business_keywords TEXT,
            is_unknown_email INTEGER, is_personal INTEGER, is_business INTEGER,
            is_marketing INTEGER, is_membership INTEGER, is_family INTEGER,
            is_hobby INTEGER, is_retail INTEGER, is_education INTEGER,
            is_certification INTEGER, is_spam INTEGER, is_invalid INTEGER,
            is_interest INTEGER, is_mentor INTEGER, is_colleague INTEGER,
            is_professional INTEGER,
            fromGmailHistory INTEGER,
            fromContactList INTEGER,
            is_medical INTEGER,
            is_financial INTEGER,
            FOREIGN KEY (contact_id) REFERENCES contacts (contact_id)
        )"""
        )

        # --- Table: emails ---
        # The central table for storing core email content.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS emails (
            message_id TEXT PRIMARY KEY,
            thread_id TEXT,
            sender_email TEXT,
            subject TEXT,
            body_text TEXT,
            body_html TEXT,
            sent_timestamp TEXT,
            internal_date_ms INTEGER,
            date_received TEXT,
            mime_type TEXT,
            content_transfer_encoding TEXT,
            charset TEXT,
            to_recipients TEXT,
            cc_recipients TEXT,
            bcc_recipients TEXT,
            return_path TEXT,
            header_sender TEXT,
            -- New boolean flags for labels
            is_labeled_spam INTEGER DEFAULT 0,
            is_labeled_promotions INTEGER DEFAULT 0,
            is_labeled_social INTEGER DEFAULT 0,
            is_labeled_forums INTEGER DEFAULT 0,
            is_labeled_personal INTEGER DEFAULT 0,
            FOREIGN KEY (sender_email) REFERENCES email_address (email)
        )"""
        )

        # --- Table: email_attachments ---
        # Stores metadata about email attachments.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_attachments (
            attachment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT,
            filename TEXT,
            mime_type TEXT,
            attachment_size INTEGER,
            FOREIGN KEY (message_id) REFERENCES emails (message_id) ON DELETE CASCADE
        )"""
        )

        # --- Table: email_xheaders ---
        # Stores non-standard 'X-' headers from emails.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_xheaders (
            xheader_id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT,
            header_name TEXT,
            header_value TEXT,
            FOREIGN KEY (message_id) REFERENCES emails (message_id) ON DELETE CASCADE
        )"""
        )

        # --- Table: email_labels ---
        # Links emails to their assigned Gmail labels.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_labels (
            label_id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT,
            label_name TEXT,
            FOREIGN KEY (message_id) REFERENCES emails (message_id) ON DELETE CASCADE
        )"""
        )
        
        # --- Table: email_routing_headers ---
        # Stores sequential 'Received:' headers to trace an email's path.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_routing_headers (
            route_id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT,
            header_name TEXT,
            header_value TEXT,
            hop_order INTEGER,
            FOREIGN KEY (message_id) REFERENCES emails (message_id) ON DELETE CASCADE
        )"""
        )
        
        # --- Table: email_authentication ---
        # Stores SPF, DKIM, and DMARC authentication results.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_authentication (
            auth_id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT,
            spf_status TEXT,
            spf_domain TEXT,
            dkim_status TEXT,
            dkim_domain TEXT,
            dkim_selector TEXT,
            dmarc_status TEXT,
            dmarc_policy TEXT,
            FOREIGN KEY (message_id) REFERENCES emails (message_id) ON DELETE CASCADE
        )"""
        )
        
        self.conn.commit()

    def create_label_dataframe(self) -> pd.DataFrame | None:
        """
        Generates a DataFrame where the first column is 'message_id' and subsequent
        columns are unique label names, with boolean values indicating if the
        email has that label.

        Returns:
            A pandas DataFrame with the specified structure, or None if an error occurs.
        """
        if not self.conn:
            print("Database connection is not open.")
            return None

        try:
            # 1. Get a DataFrame with all unique message_ids
            all_messages_df = pd.read_sql_query(
                "SELECT DISTINCT message_id FROM emails", self.conn
            )

            # 2. Get a DataFrame of message_id and label_name associations
            labels_assoc_df = pd.read_sql_query(
                "SELECT message_id, label_name FROM email_labels", self.conn
            )
            
            if labels_assoc_df.empty:
                print("No labels found in the database.")
                # Create a dataframe with just message_id and no label columns
                return all_messages_df

            # Add a helper column to indicate the presence of a label
            labels_assoc_df['has_label'] = True

            # 3. Pivot the table to make labels the columns
            pivoted_df = labels_assoc_df.pivot_table(
                index='message_id',
                columns='label_name',
                values='has_label',
                fill_value=False # Use False for missing values
            ).reset_index() # Make message_id a column again

            # 4. Merge with the full list of messages to include those with no labels
            final_df = pd.merge(all_messages_df, pivoted_df, on='message_id', how='left')
            
            # Fill NaN values for messages that had no labels at all with False
            # Get all column names that are labels (all except 'message_id')
            label_columns = [col for col in final_df.columns if col != 'message_id']
            final_df[label_columns] = final_df[label_columns].fillna(False).astype(bool)

            return final_df

        except sqlite3.Error as e:
            print(f"Database error: {e}")
            return None
        except Exception as e:
            print(f"An error occurred: {e}")
            return None

    def get_sender_emails_not_in_contacts(self) -> List[str]:
        """
        Retrieves sender emails that are not yet linked to a contact.

        Returns:
            A list of sender email addresses.
        """
        if not self.conn:
            return []
        
        query = """
        SELECT DISTINCT e.sender_email
        FROM emails e
        LEFT JOIN email_address ea ON e.sender_email = ea.email
        WHERE ea.contact_id IS NULL OR ea.contact_id = ''
        """
        cursor = self.conn.cursor()
        cursor.execute(query)
        return [row[0] for row in cursor.fetchall()]

    def add_pure_spam_contact(self, email: str):
        """
        Creates a 'PURE SPAM' contact if it doesn't exist, and updates the
        email address with 'PURE_SPAM' attributes, linking it to the contact.
        """
        if not self.conn:
            print("Database connection is not open.")
            return

        cursor = self.conn.cursor()
        try:
            # 1. Find or create the PURE SPAM contact
            cursor.execute("SELECT contact_id FROM contacts WHERE first_name = 'PURE' AND last_name = 'SPAM'")
            result = cursor.fetchone()
            if result:
                contact_id = result[0]
            else:
                cursor.execute("INSERT INTO contacts (first_name, last_name) VALUES ('PURE', 'SPAM')")
                contact_id = cursor.lastrowid

            # 2. Insert or ignore the email address to ensure it exists
            cursor.execute("INSERT OR IGNORE INTO email_address (email) VALUES (?)", (email,))

            # 3. Update the email address with PURE_SPAM details
            update_query = """
            UPDATE email_address
            SET
                display_name = 'PURE_SPAM',
                contact_id = ?,
                interest_keywords = 'PURE_SPAM',
                business_keywords = 'PURE_SPAM',
                is_unknown_email = 100,
                is_personal = -100,
                is_business = -100,
                is_marketing = -100,
                is_membership = -100,
                is_family = -100,
                is_hobby = -100,
                is_retail = -100,
                is_education = -100,
                is_certification = -100,
                is_spam = 100,
                is_invalid = 0,
                is_interest = -100,
                is_mentor = -100,
                is_colleague = -100,
                is_professional = -100,
                is_medical = -100,
                is_financial = -100,
                fromGmailHistory = NULL,
                fromContactList = NULL
            WHERE email = ?
            """
            cursor.execute(update_query, (contact_id, email))
            self.conn.commit()
            print(f"Email {email} has been marked as PURE_SPAM.")

        except Exception as e:
            self.conn.rollback()
            print(f"Failed to process pure spam contact for {email}: {e}")

    def get_spam_sender_emails_not_in_contacts(self):
        """
        Retrieves sender emails from messages marked as SPAM or with authentication
        failures, which are not yet linked to a contact. It then interactively
        prompts the user to classify them as 'pure spam' or process them normally.
        """
        if not self.conn:
            return

        query = """
        SELECT DISTINCT e.sender_email
        FROM emails e
        LEFT JOIN email_address ea ON e.sender_email = ea.email
        LEFT JOIN email_labels el ON e.message_id = el.message_id
        LEFT JOIN email_authentication auth ON e.message_id = auth.message_id
        WHERE (ea.contact_id IS NULL OR ea.contact_id = '')
          AND (
            el.label_name = 'SPAM'
            OR auth.spf_status = 'failed'
            OR auth.dkim_status = 'failed'
            OR auth.dmarc_status = 'failed'
          )
        """
        cursor = self.conn.cursor()
        cursor.execute(query)
        spam_senders = [row[0] for row in cursor.fetchall()]

        if not spam_senders:
            print("\nNo new potential SPAM sender emails to process.")
            return

        print(f"\nFound {len(spam_senders)} potential SPAM sender(s) to process.")
        for email in spam_senders:
            print(f"\nProcessing sender: {email}")
            is_pure_spam = input("Is this pure spam? (y/N): ").lower()
            
            if is_pure_spam == 'y':
                self.add_pure_spam_contact(email)
            else:
                print("Proceeding with normal contact editing...")
                self.edit_contact_and_email_interactive(email)

    def get_contact_and_email_details(self, email: str) -> Dict[str, Any] | None:
        """
        Retrieves full contact and email address details for a given email.

        Args:
            email (str): The email address to look up.

        Returns:
            A dictionary containing the joined data from email_address and contacts,
            or None if the email does not exist.
        """
        if not self.conn:
            return None
        
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        
        cursor.execute("""
            SELECT *
            FROM email_address ea
            LEFT JOIN contacts c ON ea.contact_id = c.contact_id
            WHERE ea.email = ?
        """, (email,))
        
        row = cursor.fetchone()
        self.conn.row_factory = None  # Reset row factory
        
        return dict(row) if row else None

    def get_all_contacts(self) -> List[Dict[str, Any]]:
        """
        Retrieves all records from the contacts table.

        Returns:
            A list of dictionaries, where each dictionary represents a contact.
        """
        if not self.conn:
            return []
            
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM contacts ORDER BY last_name, first_name")
        rows = cursor.fetchall()
        self.conn.row_factory = None
        
        return [dict(row) for row in rows]

    def show_sender_contact_status(self):
        """Displays the sender email, display name, and contact ID for all emails."""
        if not self.conn:
            print("Database connection is not open.")
            return
            
        print("\n--- Sender Email and Display Names (Pre-contact Info) ---")
        try:
            query = """
            SELECT
                e.sender_email,
                ea.display_name,
                ea.contact_id
            FROM
                emails e
            JOIN
                email_address ea ON e.sender_email = ea.email
            GROUP BY
                e.sender_email, ea.display_name, ea.contact_id
            ORDER BY
                e.sender_email;
            """
            results = self.query_db(query)
            if results:
                print(f"{'Sender Email':<40} {'Display Name':<30} {'Contact ID':<12}")
                print(f"{'-'*40:<40} {'-'*30:<30} {'-'*12:<12}")
                for row in results:
                    sender_email, display_name, contact_id = row
                    # Handle potential None values for display
                    display_name_str = display_name if display_name is not None else "N/A"
                    contact_id_str = str(contact_id) if contact_id is not None else "N/A"
                    print(f"{sender_email:<40} {display_name_str:<30} {contact_id_str:<12}")
            else:
                print("No sender emails found in the database.")
        except Exception as e:
            print(f"An error occurred while fetching sender/display names: {e}")

    def upsert_contact(self, contact: ContactModel) -> int | None:
        """
        Inserts a new contact or updates an existing one based on the ContactModel.
        Converts list fields to JSON strings for database storage.

        Args:
            contact (ContactModel): The contact data.

        Returns:
            int | None: The contact_id of the inserted/updated contact, or None if
                        the operation fails.
        """
        if not self.conn:
            print("Database connection is not open.")
            return None

        cursor = self.conn.cursor()

        # Prepare data for insertion/update
        contact_data = dict(contact)
        contact_id = contact_data.pop("contact_id", None)

        # Convert list fields to JSON strings
        if "interest_keywords" in contact_data and contact_data["interest_keywords"] is not None:
            contact_data["interest_keywords"] = json.dumps(contact_data["interest_keywords"])
        if "family_members" in contact_data and contact_data["family_members"] is not None:
            contact_data["family_members"] = json.dumps(contact_data["family_members"])

        # Default proximity scores to 0 if not provided
        for key in ["family_proximity", "physical_proximity", "business_proximity",
                    "digital_proximity", "interest_proximity", "church_proximity"]:
            if contact_data.get(key) is None:
                contact_data[key] = 0

        columns = ", ".join(contact_data.keys())
        placeholders = ", ".join("?" * len(contact_data))
        values = tuple(contact_data.values())

        try:
            if contact_id:
                # Check if contact_id exists for an update
                cursor.execute("SELECT 1 FROM contacts WHERE contact_id = ?", (contact_id,))
                if cursor.fetchone():
                    # Update existing contact
                    set_clause = ", ".join([f"{key} = ?" for key in contact_data.keys()])
                    cursor.execute(f"UPDATE contacts SET {set_clause} WHERE contact_id = ?",
                                   (*values, contact_id))
                    self.conn.commit()
                    return contact_id
                else:
                    # contact_id provided but not found, proceed with insert
                    print(f"Warning: contact_id {contact_id} not found for update, inserting new contact.")

            # Insert new contact
            cursor.execute(f"INSERT INTO contacts ({columns}) VALUES ({placeholders})", values)
            self.conn.commit()
            return cursor.lastrowid
        except Exception as e:
            self.conn.rollback()
            print(f"Failed to upsert contact: {e}")
            return None

    def _prompt_for_data(self, prompt_text: str, existing_value: Any) -> str:
        """
        Helper function to prompt for user input with a default value.
        """
        if existing_value is None:
            existing_value = ""
        return input(f"{prompt_text} [{existing_value}]: ") or existing_value

    def edit_contact_and_email_interactive(self, email: str):
        """
        Interactively edits contact and email_address details for a given email in a two-step process.
        Step 1: Edit fields in the email_address table.
        Step 2: Edit fields in the contacts table, with an option to create or link a contact.
        """
        if not self.conn:
            print("Database connection is not open.")
            return

        cursor = self.conn.cursor()
        cursor.execute("SELECT 1 FROM email_address WHERE email = ?", (email,))
        if not cursor.fetchone():
            # As per user request, attempting to find descriptive names.
            # Note: At this point, the email is not in the database, so details are not expected.
            details = self.get_contact_and_email_details(email)
            if details:
                print(f"Email Address: {details.get('email')}")
                print(f"Display Name: {details.get('display_name')}")
                print(f"Full Name: {details.get('first_name')} {details.get('last_name')}")
                print(f"Common Name: {details.get('common_name')}")
                print(f"Employer: {details.get('employer')}")
            else:
                print(f"No descriptive names found for {email} as it is not in the database yet.")

            print(f"Email '{email}' not found in the database.")
            if input("Would you like to add it? (y/N): ").lower() == 'y':
                cursor.execute("INSERT INTO email_address (email) VALUES (?)", (email,))
                self.conn.commit()
                print(f"Added new email: {email}")
            else:
                return

        # --- Step 1: Edit Email Address details ---
        while True:
            details = self.get_contact_and_email_details(email)
            if not details:
                print("Could not fetch details for the email.")
                return

            print(f"\n--- Step 1 of 2: Editing Email Address for: {email} ---")
            print("Current Email Address Info:")
            for key in ['display_name', 'interest_keywords', 'business_keywords', 'is_unknown_email', 'is_personal', 'is_business', 'is_marketing', 'is_membership', 'is_family', 'is_hobby', 'is_retail', 'is_education', 'is_certification', 'is_spam', 'is_invalid', 'is_interest', 'is_mentor', 'is_colleague', 'is_professional', 'is_medical', 'is_financial', 'fromGmailHistory', 'fromContactList']:
                print(f"  {key}: {details.get(key)}")

            print("\nEnter new values or press Enter to keep the current value.")
            email_updates = {
                "display_name": self._prompt_for_data("Display Name", details.get('display_name')),
                "interest_keywords": self._prompt_for_data("Interest Keywords (Email)", details.get('interest_keywords')),
                "business_keywords": self._prompt_for_data("Business Keywords (Email)", details.get('business_keywords')),
                "is_unknown_email": int(self._prompt_for_data("Is Unknown", details.get('is_unknown_email') or 0)),
                "is_personal": int(self._prompt_for_data("Is Personal", details.get('is_personal') or 0)),
                "is_business": int(self._prompt_for_data("Is Business", details.get('is_business') or 0)),
                "is_marketing": int(self._prompt_for_data("Is Marketing", details.get('is_marketing') or 0)),
                "is_membership": int(self._prompt_for_data("Is Membership", details.get('is_membership') or 0)),
                "is_family": int(self._prompt_for_data("Is Family", details.get('is_family') or 0)),
                "is_hobby": int(self._prompt_for_data("Is Hobby", details.get('is_hobby') or 0)),
                "is_retail": int(self._prompt_for_data("Is Retail", details.get('is_retail') or 0)),
                "is_education": int(self._prompt_for_data("Is Education", details.get('is_education') or 0)),
                "is_certification": int(self._prompt_for_data("Is Certification", details.get('is_certification') or 0)),
                "is_spam": int(self._prompt_for_data("Is Spam", details.get('is_spam') or 0)),
                "is_invalid": int(self._prompt_for_data("Is Invalid", details.get('is_invalid') or 0)),
                "is_interest": int(self._prompt_for_data("Is Interest", details.get('is_interest') or 0)),
                "is_mentor": int(self._prompt_for_data("Is Mentor", details.get('is_mentor') or 0)),
                "is_colleague": int(self._prompt_for_data("Is Colleague", details.get('is_colleague') or 0)),
                "is_professional": int(self._prompt_for_data("Is Professional", details.get('is_professional') or 0)),
                "is_medical": int(self._prompt_for_data("Is Medical", details.get('is_medical') or 0)),
                "is_financial": int(self._prompt_for_data("Is Financial", details.get('is_financial') or 0)),
                "fromGmailHistory": int(self._prompt_for_data("From Gmail History", details.get('fromGmailHistory') or 0)),
                "fromContactList": int(self._prompt_for_data("From Contact List", details.get('fromContactList') or 0)),
            }

            print("\n--- Review Email Address Changes ---")
            print(json.dumps(email_updates, indent=2))
            action = input("\n[S]ave and Continue to Contact, [R]evise, or [C]ancel? (S/r/c): ").lower()

            if action == 'r':
                continue
            if action == 'c':
                print("Changes cancelled.")
                return

            try:
                email_set_clause = ", ".join([f"{key} = ?" for key in email_updates])
                cursor.execute(f"UPDATE email_address SET {email_set_clause} WHERE email = ?", (*email_updates.values(), email))
                self.conn.commit()
                print("Successfully saved email address changes.")
                break
            except Exception as e:
                self.conn.rollback()
                print(f"An error occurred: {e}. Please try again.")

        # --- Step 2: Edit Contact details ---
        details = self.get_contact_and_email_details(email)
        contact_id = details.get("contact_id")

        if not contact_id:
            print("\nThis email is not linked to a contact.")
            action = input("[C]reate a new contact, [L]ink to an existing one, or [S]kip contact editing? (c/l/s): ").lower()

            if action == 'c':
                print("\nEnter details for the new contact. Press Enter to skip.")
                contact_data = {
                    "first_name": input("First Name: "), "last_name": input("Last Name: "),
                    "common_name": input("Common Name: "), "interest_keywords": input("Interest Keywords: "),
                    "family_members": input("Family Members: "), "church": input("Church: "),
                    "employer": input("Employer: "),
                    "family_proximity": int(input("Family Proximity [-100 to 100]: ") or 0),
                    "physical_proximity": int(input("Physical Proximity [-100 to 100]: ") or 0),
                    "business_proximity": int(input("Business Proximity [-100 to 100]: ") or 0),
                    "digital_proximity": int(input("Digital Proximity [-100 to 100]: ") or 0),
                    "interest_proximity": int(input("Interest Proximity [-100 to 100]: ") or 0),
                    "church_proximity": int(input("Church Proximity [-100 to 100]: ") or 0),
                }
                contact_id = self.upsert_contact(contact_data)
                if contact_id:
                    cursor.execute("UPDATE email_address SET contact_id = ? WHERE email = ?", (contact_id, email))
                    self.conn.commit()
                    print(f"Created new contact and linked it to {email}.")
                else:
                    print("Failed to create new contact.")
                    return
            
            elif action == 'l':
                all_contacts = self.get_all_contacts()
                if not all_contacts:
                    print("No existing contacts to link to.")
                else:
                    print("\nAvailable contacts:")
                    for i, c in enumerate(all_contacts):
                        print(f"  {i+1}. {c['first_name']} {c['last_name']} ({c['employer'] or 'N/A'})")
                    
                    try:
                        choice = int(input("Select a contact to link: ")) - 1
                        if 0 <= choice < len(all_contacts):
                            contact_id = all_contacts[choice]['contact_id']
                            cursor.execute("UPDATE email_address SET contact_id = ? WHERE email = ?", (contact_id, email))
                            self.conn.commit()
                            print(f"Linked {email} to existing contact.")
                        else:
                            print("Invalid selection.")
                    except ValueError:
                        print("Invalid input.")
            else:
                print("Skipping contact editing.")
                return
        
        while True:
            details = self.get_contact_and_email_details(email)
            contact_id = details.get("contact_id")

            if not contact_id:
                print("No contact linked. Cannot edit contact details.")
                break

            print(f"\n--- Step 2 of 2: Editing Contact for: {email} ---")
            print("Current Contact Info:")
            for key in ['first_name', 'last_name', 'common_name', 'interest_keywords', 'family_members', 'church', 'employer', 'family_proximity', 'physical_proximity', 'business_proximity', 'digital_proximity', 'interest_proximity', 'church_proximity']:
                print(f"  {key}: {details.get(key)}")

            print("\nEnter new values or press Enter to keep the current value. Range is -100 to 100 for integer fields.")
            contact_updates = {
                "first_name": self._prompt_for_data("First Name", details.get('first_name')),
                "last_name": self._prompt_for_data("Last Name", details.get('last_name')),
                "common_name": self._prompt_for_data("Common Name", details.get('common_name')),
                "interest_keywords": self._prompt_for_data("Interest Keywords (Contact)", details.get('interest_keywords')),
                "family_members": self._prompt_for_data("Family Members", details.get('family_members')),
                "church": self._prompt_for_data("Church", details.get('church')),
                "employer": self._prompt_for_data("Employer", details.get('employer')),
                "family_proximity": int(self._prompt_for_data("Family Proximity", details.get('family_proximity') or 0)),
                "physical_proximity": int(self._prompt_for_data("Physical Proximity", details.get('physical_proximity') or 0)),
                "business_proximity": int(self._prompt_for_data("Business Proximity", details.get('business_proximity') or 0)),
                "digital_proximity": int(self._prompt_for_data("Digital Proximity", details.get('digital_proximity') or 0)),
                "interest_proximity": int(self._prompt_for_data("Interest Proximity", details.get('interest_proximity') or 0)),
                "church_proximity": int(self._prompt_for_data("Church Proximity", details.get('church_proximity') or 0)),
            }
            
            print("\n--- Review Contact Changes ---")
            print(json.dumps(contact_updates, indent=2))
            action = input("\n[S]ave, [R]evise, or [C]ancel? (S/r/c): ").lower()

            if action == 'r':
                continue
            if action == 'c':
                print("Changes cancelled.")
                break

            try:
                # Need to pass a ContactModel to upsert_contact
                contact_updates_model = contact_updates.copy()
                contact_updates_model['contact_id'] = contact_id
                
                # Handle list-like fields from user input
                for key in ["interest_keywords", "family_members"]:
                    value = contact_updates_model.get(key)
                    if isinstance(value, str):
                        try:
                            # First, try to load as JSON (for existing, unchanged values)
                            contact_updates_model[key] = json.loads(value)
                        except json.JSONDecodeError:
                            # If that fails, assume it's new comma-separated input
                            contact_updates_model[key] = [item.strip() for item in value.split(',') if item.strip()]

                self.upsert_contact(contact_updates_model)
                print("Successfully saved contact changes.")
                break
            except Exception as e:
                self.conn.rollback()
                print(f"An error occurred: {e}. Please try again.")

    def close_db(self):
        """Closes the database connection if it is open."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def query_db(self, query: str, params: tuple = (())):
        """
        Executes a given SQL query and returns the results.

        Args:
            query (str): The SQL query to execute.
            params (tuple): Optional parameters to substitute into the query.

        Returns:
            A list of tuples representing the fetched rows, or None if not connected.
        """
        if not self.conn:
            return None
        cursor = self.conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchall()

    def import_ExtractedEmailData(self, filepath: str) -> ExtractedEmailData | None:
        """
        Loads and parses email data from a specified JSON file.

        Args:
            filepath (str): The path to the JSON file.

        Returns:
            An ExtractedEmailData dictionary, or None if an error occurs.
        """
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                email_data: ExtractedEmailData = json.load(f)
            return email_data
        except FileNotFoundError:
            print(f"Error: File not found at {filepath}")
            return None
        except json.JSONDecodeError:
            print(f"Error: Could not decode JSON from {filepath}")
            return None
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            return None

    def insert_message(self, email_data: ExtractedEmailData, update_if_exists: bool = True):
        """
        Inserts or updates a message and all its related data into the database.

        This process is transactional and idempotent. If `update_if_exists` is true,
        it will replace existing data for a given message ID.

        Args:
            email_data (ExtractedEmailData): The dictionary of parsed email data.
            update_if_exists (bool): If True, replaces existing message data.
                                     If False, skips insertion if the message ID exists.
        """
        if not self.conn:
            print("Database connection is not open.")
            return

        cursor = self.conn.cursor()
        message_id = email_data.get("message_id")

        # If not updating, check for existence and skip if found.
        if not update_if_exists:
            cursor.execute("SELECT 1 FROM emails WHERE message_id = ?", (message_id,))
            if cursor.fetchone():
                print(f"Message {message_id} already exists. Skipping insertion.")
                return

        try:
            # --- 1. Ensure Email Addresses Exist ---
            # Gather all unique email addresses from the message and add them to the
            # email_address table if they don't already exist.
            all_recipients = (
                [(email_data.get('sender_name'), email_data.get('sender_email'))] +
                email_data.get('to_recipients', []) +
                email_data.get('cc_recipients', []) +
                email_data.get('bcc_recipients', [])
            )
            for name, email in set(tuple(i) for i in all_recipients if i and i[1]):
                cursor.execute("INSERT OR IGNORE INTO email_address (email, display_name) VALUES (?, ?)", (email, name))

            # --- 2. Insert or Replace the Main Email Record ---
            email_model_data = {
                "message_id": message_id,
                "thread_id": email_data.get("thread_id"),
                "sender_email": email_data.get("sender_email"),
                "subject": email_data.get("subject"),
                "body_text": email_data.get("body_text"),
                "body_html": email_data.get("body_html"),
                "sent_timestamp": email_data.get("sent_timestamp"),
                "internal_date_ms": email_data.get("internal_date_ms"),
                "date_received": email_data.get("date_received"),
                "mime_type": email_data.get("mime_type"),
                "content_transfer_encoding": email_data.get("content_transfer_encoding"),
                "charset": email_data.get("charset"),
                "to_recipients": json.dumps(email_data.get("to_recipients", [])),
                "cc_recipients": json.dumps(email_data.get("cc_recipients", [])),
                "bcc_recipients": json.dumps(email_data.get("bcc_recipients", [])),
                "return_path": email_data.get("return_path"),
                "header_sender": email_data.get("header_sender"),
            }
            cursor.execute("""
                INSERT OR REPLACE INTO emails (message_id, thread_id, sender_email, subject, body_text, body_html, sent_timestamp, internal_date_ms, date_received, mime_type, content_transfer_encoding, charset, to_recipients, cc_recipients, bcc_recipients, return_path, header_sender)
                VALUES (:message_id, :thread_id, :sender_email, :subject, :body_text, :body_html, :sent_timestamp, :internal_date_ms, :date_received, :mime_type, :content_transfer_encoding, :charset, :to_recipients, :cc_recipients, :bcc_recipients, :return_path, :header_sender)
            """, email_model_data)
            
            # --- 3. Insert Related Data (deleting old records first for idempotency) ---
            
            # Attachments
            cursor.execute("DELETE FROM email_attachments WHERE message_id = ?", (message_id,))
            for att in email_data.get("attachments", []):
                cursor.execute("INSERT INTO email_attachments (message_id, filename, mime_type, attachment_size) VALUES (?, ?, ?, ?)",
                               (message_id, att.get("filename"), att.get("mime_type"), att.get("attachment_size")))

            # X-Headers
            cursor.execute("DELETE FROM email_xheaders WHERE message_id = ?", (message_id,))
            for xh in email_data.get("xheaders", []):
                cursor.execute("INSERT INTO email_xheaders (message_id, header_name, header_value) VALUES (?, ?, ?)",
                               (message_id, xh.get("header_name"), xh.get("header_value")))
            
            # Labels
            cursor.execute("DELETE FROM email_labels WHERE message_id = ?", (message_id,))
            for label in email_data.get("labels", []):
                cursor.execute("INSERT INTO email_labels (message_id, label_name) VALUES (?, ?)",
                               (message_id, label.get("label_name")))
            
            # Routing Headers
            cursor.execute("DELETE FROM email_routing_headers WHERE message_id = ?", (message_id,))
            for rh in email_data.get("routing_headers", []):
                cursor.execute("INSERT INTO email_routing_headers (message_id, header_name, header_value, hop_order) VALUES (?, ?, ?, ?)",
                               (message_id, rh.get("header_name"), rh.get("header_value"), rh.get("hop_order")))

            # Authentication Results
            auth_data = email_data.get("authentication_results")
            if auth_data:
                cursor.execute("DELETE FROM email_authentication WHERE message_id = ?", (message_id,))
                cursor.execute("""
                    INSERT INTO email_authentication (message_id, spf_status, spf_domain, dkim_status, dkim_domain, dkim_selector, dmarc_status, dmarc_policy)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    message_id,
                    auth_data.get("spf_status"), auth_data.get("spf_domain"),
                    auth_data.get("dkim_status"), auth_data.get("dkim_domain"), auth_data.get("dkim_selector"),
                    auth_data.get("dmarc_status"), auth_data.get("dmarc_policy")
                ))

            # Commit the transaction.
            self.conn.commit()
            print(f"Successfully inserted/updated message {message_id}")

        except Exception as e:
            # If any error occurs, roll back the entire transaction.
            if self.conn:
                self.conn.rollback()
            print(f"Failed to insert message {message_id}: {e}")

    def get_all_labels(self) -> List[str]:
        """
        Retrieves a list of all unique label names from the email_labels table.

        Returns:
            List[str]: A list of unique label names, sorted alphabetically.
        """
        if not self.conn:
            print("Database connection is not open.")
            return []
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT DISTINCT label_name FROM email_labels ORDER BY label_name")
            # The result of fetchall is a list of tuples, e.g., [('INBOX',), ('SENT',)]
            return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            print(f"Error retrieving all labels: {e}")
            return []

    def random_msg_ids(self, quantity: int, label: Optional[str] = None) -> List[str]:
        """
        Retrieves a list of random message IDs from the database.

        Args:
            quantity (int): The number of random message IDs to return.
            label (Optional[str]): If provided, retrieves message IDs associated
                                   with this label. Otherwise, retrieves from all
                                   messages.

        Returns:
            List[str]: A list of random message IDs.
        """
        if not self.conn:
            print("Database connection is not open.")
            return []

        cursor = self.conn.cursor()

        if label:
            cursor.execute("SELECT message_id FROM email_labels WHERE label_name = ?", (label,))
        else:
            cursor.execute("SELECT message_id FROM emails")

        all_ids = [row[0] for row in cursor.fetchall()]

        if not all_ids:
            return []

        if len(all_ids) <= quantity:
            return all_ids
        
        return random.sample(all_ids, quantity)
            
    def display_message_summary(self, message_id: str):
        """
        Displays a summary of a message given its ID.

        Args:
            message_id (str): The ID of the message to display.
        """
        if not self.conn:
            print("Database connection is not open.")
            return

        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("SELECT message_id, thread_id, sender_email, subject, sent_timestamp, body_text, body_html FROM emails WHERE message_id = ?", (message_id,))
        
        row = cursor.fetchone()
        self.conn.row_factory = None

        if not row:
            print(f"No message found with ID: {message_id}")
            return

        print("\n--- Message Summary ---")
        print(f"Message ID: {row['message_id']}")
        print(f"Thread ID:  {row['thread_id']}")
        print(f"From:       {row['sender_email']}")
        print(f"Subject:    {row['subject']}")
        print(f"Date:       {row['sent_timestamp']}")
        
        print("\n--- Message Body ---")
        body_text = row['body_text']
        body_html = row['body_html']

        if body_text and body_text.strip():
            print(body_text)
        elif body_html and body_html.strip():
            print(remove_html(body_html))
        else:
            print("[No message body found]")
        print("--------------------")

    def search_emails(self, search_string: str) -> List[str]:
        """
        Searches for a partial string in email fields and returns matching message IDs.

        Args:
            search_string (str): The string to search for.

        Returns:
            List[str]: A list of message IDs from emails that match the search string.
        """
        if not self.conn:
            print("Database connection is not open.")
            return []

        query = """
        SELECT message_id FROM emails
        WHERE sender_email LIKE ?
           OR subject LIKE ?
           OR body_text LIKE ?
           OR body_html LIKE ?
        """
        
        search_term = f'%{search_string}%'
        params = (search_term, search_term, search_term, search_term)
        
        results = self.query_db(query, params)
        
        return [row[0] for row in results] if results else []

    def export_messages_by_label(self):
        """
        Exports formatted message content to separate files for each label.

        For each label, a .txt file is created. Inside, every message tagged
        with that label is written in a specific block format.
        """
        print("Starting export process...")
        
        # 1. Get the dataframe of messages and their labels
        label_df = self.create_label_dataframe()
        if label_df is None or label_df.empty:
            print("Could not generate label DataFrame or it is empty. Aborting export.")
            return
        
        print(f"Found {len(label_df.columns) - 1} labels to process.")

        # 2. Fetch all necessary email content in one go for efficiency
        try:
            print("Fetching all email content...")
            emails_df = pd.read_sql_query(
                "SELECT message_id, sender_email, sent_timestamp, internal_date_ms, body_text, body_html FROM emails",
                self.conn
            )
            # Use message_id as index for fast lookups
            emails_df.set_index('message_id', inplace=True)
            print("Email content loaded successfully.")
        except Exception as e:
            print(f"Failed to fetch email content: {e}")
            return
            
        # 3. Get the list of label columns to iterate over
        label_columns = [col for col in label_df.columns if col != 'message_id']

        # 4. Iterate through each label and export messages
        for label_name in label_columns:
            # Sanitize the label name for a valid filename
            sanitized_filename = f"{label_name.replace('/', '_')}.txt"
            
            # Get the list of message_ids that have the current label
            message_ids_for_label = label_df[label_df[label_name] == True]['message_id']
            
            if message_ids_for_label.empty:
                print(f"No messages found for label '{label_name}'. Skipping file creation.")
                continue

            print(f"Processing label '{label_name}': Found {len(message_ids_for_label)} messages. Writing to {sanitized_filename}...")
            
            try:
                with open(sanitized_filename, 'w', encoding='utf-8') as f:
                    for message_id in message_ids_for_label:
                        try:
                            # Look up email details from the pre-fetched DataFrame
                            email_details = emails_df.loc[message_id]
                            
                            sender_email = email_details.get('sender_email', '') or ''
                            sent_timestamp = email_details.get('sent_timestamp', '') or ''
                            internal_date_ms = email_details.get('internal_date_ms', 0) or 0
                            
                            try:
                                internal_date = datetime.datetime.fromtimestamp(internal_date_ms / 1000)
                                human_readable_date = internal_date.strftime('%Y-%m-%d %H:%M:%S')
                            except (ValueError, TypeError):
                                human_readable_date = "Invalid Date"

                            body_text = email_details.get('body_text', '') or ''
                            body_html = email_details.get('body_html', '') or ''

                            # Write the formatted block to the file
                            f.write("nnnnnnnnnn\n")
                            f.write(f"{message_id}\n")
                            f.write(f"{sender_email}\n")
                            f.write(f"{sent_timestamp}\n")
                            f.write(f"{human_readable_date}\n")
                            f.write("tttttttttt\n")
                            f.write(f"{body_text}\n")
                            f.write("hhhhhhhhhh\n")
                            f.write(f"{body_html}\n")
                            f.write("eeeeeeeeee\n")
                            
                        except KeyError:
                            print(f"  - Warning: Could not find details for message_id '{message_id}'. Skipping.")
                        except Exception as e:
                            print(f"  - Error writing message {message_id} to file: {e}")
                            
                print(f"Successfully created file: {sanitized_filename}")

            except IOError as e:
                print(f"Error opening or writing to file {sanitized_filename}: {e}")
        
        print("Export process completed.")

    def update_email_label_booleans(self):
        """
        Backfills the boolean label flags (is_labeled_spam, is_labeled_promotions, etc.) in the 'emails' table
        based on the labels stored in the 'email_labels' table.
        """
        if not self.conn:
            print("Database connection is not open.")
            return

        print("Starting to update email label flags...")
        cursor = self.conn.cursor()

        # A dictionary mapping label names to their corresponding column in the emails table.
        # Note: Gmail's promotions label is 'CATEGORY_PROMOTIONS', etc.
        label_to_column_map = {
            'SPAM': 'is_labeled_spam',
            'CATEGORY_PROMOTIONS': 'is_labeled_promotions',
            'CATEGORY_SOCIAL': 'is_labeled_social',
            'CATEGORY_FORUMS': 'is_labeled_forums',
            'CATEGORY_PERSONAL': 'is_labeled_personal'
        }

        try:
            for label_name, column_name in label_to_column_map.items():
                print(f"  - Updating '{column_name}' flag for label '{label_name}'...")
                
                # First, reset the column to 0 for all emails to handle cases where a label might have been removed.
                cursor.execute(f"UPDATE emails SET {column_name} = 0")

                # Set the flag to 1 for all emails that have the specified label
                query = f"""
                UPDATE emails
                SET {column_name} = 1
                WHERE message_id IN (
                    SELECT message_id FROM email_labels WHERE label_name = ?
                )
                """
                cursor.execute(query, (label_name,))
                print(f"    ...done. {cursor.rowcount} rows affected.")

            self.conn.commit()
            print("\nEmail label flags updated successfully.")

        except Exception as e:
            self.conn.rollback()
            print(f"An error occurred during the update: {e}")

    def activate_nlp(self):
        self.category_names = ["Forums", "Promotions", "Social", "Updates", "Spam"]
        self.category_vectors = {} # Initialize as dictionary

        for cat in self.category_names:
            doc_bin = DocBin().from_disk(f"nlp_spacy/nlp_results_{cat}.spacy")
            docs = list(doc_bin.get_docs(self.nlp.vocab))

            all_vectors = [doc.vector for doc in docs if doc.vector_norm > 0]
    
            if all_vectors:
                self.category_vectors[cat] = np.mean(all_vectors, axis=0)
            else:
                self.category_vectors[cat] = None


    def classify_new_message(self, text):
        new_doc = self.nlp(text)
    
        # If the message has no vector (e.g., all stop words/punctuation), return None
        if new_doc.vector_norm == 0:
            return "Unknown"

        # 4. Compare the new message vector to each category vector
        scores = {}
        for cat, cat_vec in self.category_vectors.items():
            if cat_vec is not None:
                # Manual cosine similarity between the new doc and the category average
                # (spaCy's .similarity() usually requires two Doc/Token objects)
                norm_new = new_doc.vector_norm
                norm_cat = np.linalg.norm(cat_vec)
                similarity = np.dot(new_doc.vector, cat_vec) / (norm_new * norm_cat)
                scores[cat] = similarity

        # Return the category with the highest similarity score
        if scores:
            return max(scores, key=scores.get)
        return "Unknown"
    
    def classify_with_probabilities(self, text):
        new_doc = self.nlp(text)
        if new_doc.vector_norm == 0:
            return "Unknown", {}

        # 2. Collect raw similarity scores
        raw_scores = []
        available_cats = []
    
        for cat, cat_vec in self.category_vectors.items():
            if cat_vec is not None:
                # Standard cosine similarity
                sim = np.dot(new_doc.vector, cat_vec) / (new_doc.vector_norm * np.linalg.norm(cat_vec))
                raw_scores.append(sim)
                available_cats.append(cat)
        
        if not raw_scores:
            return "Unknown", {}

        # 3. Apply Softmax to convert similarities to probabilities
        # We multiply by a 'temperature' (e.g., 10 or 20) to make the probabilities 
        # more distinct; otherwise, scores close to each other stay flat.
        prob_values = softmax(np.array(raw_scores) * 15) 
    
        # 4. Map results back to category names
        category_probs = dict(zip(available_cats, prob_values))
        best_cat = max(category_probs, key=category_probs.get)
    
        return best_cat, category_probs

    
    def redact_sensitive_info(self):
        """
        Redacts sensitive information (email usernames, phone numbers, addresses, zip codes)
        from the body_text and body_html fields in the emails table.

        This method is irreversible and will permanently change the data.
        """
        if not self.conn:
            print("Database connection is not open.")
            return

        # Regular expressions for redaction
        # Email: Redacts the local-part of an email, ignoring common no-reply addresses.
        email_regex = re.compile(
            r'\b(?!no-?reply|support|admin|billing|noreply|donotreply|automated|mailer-daemon|postmaster)\b([a-zA-Z0-9._%+-]+)@',
            re.IGNORECASE
        )
        # Phone: Matches various phone number formats.
        phone_regex = re.compile(
            r'(\+?\d{1,2}[-.\s]?)?(\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b)'
        )
        # Address: A best-effort attempt to find street addresses (e.g., 123 Main St).
        # This is not perfect and may have false positives/negatives.
        address_regex = re.compile(
            r'(\b\d{1,5}\s+)([\w\s.-]+)(\s+St\.?|\s+Street|\s+Ave\.?|\s+Avenue|\s+Rd\.?|\s+Road|\s+Dr\.?|\s+Drive|\s+Ln\.?|\s+Lane|\s+Ct\.?|\s+Court|\s+Blvd\.?|\s+Boulevard\b)',
            re.IGNORECASE
        )
        # City: Attempt to find city names in a "City, ST" format.
        city_regex = re.compile(
            r'\b([A-Za-z\s]+),\s*([A-Z]{2})\b'
        )
        # Zip Code: Matches 5-digit or 9-digit zip codes.
        zip_regex = re.compile(r'\b\d{5}(?:-\d{4})?\b')

        def redact_street(match):
            # Vary length of street name
            street_len = len(match.group(2))
            variance = random.randint(-3, 3)
            new_len = max(1, street_len + variance)
            return match.group(1) + 'X' * new_len + match.group(3)

        def redact_city(match):
            # Vary length of city name
            city_len = len(match.group(1))
            variance = random.randint(-2, 2)
            new_len = max(1, city_len + variance)
            return 'X' * new_len + ", " + match.group(2)

        def redact(text: str) -> str:
            if not isinstance(text, str):
                return text
            
            text = email_regex.sub(r'XXXXXXXX@', text)
            text = phone_regex.sub('XXXXXXXXXX', text)
            text = address_regex.sub(redact_street, text)
            text = city_regex.sub(redact_city, text)
            text = zip_regex.sub('XXXXX', text)
            return text

        try:
            cursor = self.conn.cursor()
            
            # Fetch all emails
            cursor.execute("SELECT message_id, body_text, body_html FROM emails")
            rows = cursor.fetchall()
            
            if not rows:
                print("No emails to redact.")
                return

            total_rows = len(rows)
            print(f"Found {total_rows} emails to process for redaction...")
            
            updates = []
            processed_count = 0
            for message_id, body_text, body_html in rows:
                processed_count += 1
                redacted_text = redact(body_text)
                redacted_html = redact(body_html)
                # Only update if a change was actually made
                if redacted_text != body_text or redacted_html != body_html:
                    updates.append((redacted_text, redacted_html, message_id))

                if processed_count % 500 == 0:
                    print(f"Processed {processed_count}/{total_rows} emails...")

            if not updates:
                print("Processing complete. No sensitive information found to redact.")
                return

            print("Redaction analysis complete. Applying updates to the database...")
            # Perform bulk update
            cursor.executemany(
                "UPDATE emails SET body_text = ?, body_html = ? WHERE message_id = ?",
                updates
            )
            
            self.conn.commit()
            print(f"Successfully redacted sensitive information in {len(updates)} out of {total_rows} processed emails.")

        except Exception as e:
            self.conn.rollback()
            print(f"An error occurred during redaction: {e}")

    def delete_email(self, message_id: str, confirm: bool = True):
        """
        Deletes an email and all its related data from the database.
        Since related tables use ON DELETE CASCADE, only the entry in the 'emails'
        table needs to be deleted.
        Args:
            message_id (str): The ID of the message to delete.
            confirm (bool): If True, prompt for confirmation before deleting.
        """
        if not self.conn:
            print("Database connection is not open.")
            return

        cursor = self.conn.cursor()
        cursor.execute("SELECT 1 FROM emails WHERE message_id = ?", (message_id,))
        if not cursor.fetchone():
            print(f"No message found with ID: {message_id}")
            return

        if confirm:
            user_confirmation = input(f"Are you sure you want to delete email '{message_id}' and all its related data? (y/N): ").lower()
            if user_confirmation != 'y':
                print("Deletion cancelled.")
                return

        try:
            cursor.execute("DELETE FROM emails WHERE message_id = ?", (message_id,))
            self.conn.commit()
            print(f"Successfully deleted message {message_id} and all related data.")
        except Exception as e:
            self.conn.rollback()
            print(f"Failed to delete message {message_id}: {e}")

    # Example usage:
    # msg = "I need help with my monthly billing statement."
    # best, probs = classify_with_probabilities(msg)
    #
    # print(f"Predicted: {best}")
    # for cat, p in probs.items():
    #     print(f"{cat}: {p:.2%}")


from config import DATABASE_PATH

if __name__ == '__main__':
    # --- Example Usage ---
    # This block demonstrates how to use the SQLiteDB class for various tasks via a menu.
    
    # Construct the full path to the database file, mirroring the logic in main.py.
    db_path = os.path.join(DATABASE_PATH, "mail_database.db")
    db = SQLiteDB(db_path)
    db.open_db()
    print("Database opened and tables created if they didn't exist.")

    while True:
        print("\n--- SQLiteDB Main Menu ---")
        print("1. Process new sender emails and add/update contacts")
        print("2. Insert email from JSON file (if new)")
        print("3. Find and edit an email address")
        print("4. List all labels from database")
        print("5. Export email bodies by filter to JSON")
        print("6. Process potential SPAM sender emails")
        print("7. Generate Email Label DataFrame")
        print("8. Export Formatted Messages by Label")
        print("9. UPDATE email label flags in emails table")
        print("10. Classify 10 random messages")
        print("11. Search, display, and manage emails")
        print("0. Exit")
        
        choice = input("Enter your choice: ")
        
        if choice == '1':
            print("\n--- Processing new sender emails ---")
            db.show_sender_contact_status()
            new_senders = db.get_sender_emails_not_in_contacts()
            
            if not new_senders:
                print("\nAll sender email addresses are up-to-date with contact info.")
            else:
                print(f"\nFound {len(new_senders)} new sender(s) to process.")
                for email in new_senders:
                    db.edit_contact_and_email_interactive(email)

        elif choice == '2':
            print("\n--- Insert email from JSON file (if new) ---")
            
            json_files = [f for f in os.listdir('.') if f.endswith('.json')]
            if not json_files:
                print("No JSON files found in the current directory.")
                continue

            print("Available JSON files:")
            for i, filename in enumerate(json_files):
                print(f"  {i + 1}. {filename}")
            
            try:
                file_choice = int(input("Select a file by number: ")) - 1
                if 0 <= file_choice < len(json_files):
                    selected_file = json_files[file_choice]
                    email_data = db.import_ExtractedEmailData(selected_file)
                    if email_data:
                        db.insert_message(email_data, update_if_exists=False)
                    else:
                        print(f"Could not load or parse {selected_file}.")
                else:
                    print("Invalid selection.")
            except ValueError:
                print("Invalid input. Please enter a number.")
        
        elif choice == '3':
            print("\n--- Find and Edit Email Address ---")
            email_to_edit = input("Enter the full email address to edit: ")
            if email_to_edit:
                db.edit_contact_and_email_interactive(email_to_edit)
            else:
                print("No email address provided.")
            
        elif choice == '4':
            print("\n--- Listing all Labels from Database ---")
            all_labels = db.get_all_labels()
            if all_labels:
                print("Labels:")
                for label in all_labels:
                    print(f"- {label}")
            else:
                print("No labels found in the database.")

        elif choice == '5':
            print("\n--- Export Email Bodies to JSON ---")
            print("Select filter type:")
            print("  1. By Label")
            print("  2. By Sender Email Address")
            print("  3. By Sender Domain")
            filter_choice = input("Enter your choice: ")

            query = ""
            params = ()
            json_filename = ""

            if filter_choice == '1':
                label = input("Enter label: ")
                query = "SELECT e.message_id, e.body_text, e.body_html FROM emails e JOIN email_labels el ON e.message_id = el.message_id WHERE el.label_name = ?"
                params = (label,)
                json_filename = f"export_label_{label.replace('/', '_')}.json"
            elif filter_choice == '2':
                email = input("Enter sender email address: ")
                query = "SELECT message_id, body_text, body_html FROM emails WHERE sender_email = ?"
                params = (email,)
                json_filename = f"export_email_{email}.json"
            elif filter_choice == '3':
                domain = input("Enter sender domain (e.g., google.com): ")
                query = "SELECT message_id, body_text, body_html FROM emails WHERE sender_email LIKE ?"
                params = (f'%@{domain}',)
                json_filename = f"export_domain_{domain}.json"
            else:
                print("Invalid choice.")
                continue

            results = db.query_db(query, params)

            if not results:
                print("No emails found for the given filter.")
                continue

            export_data = []
            for message_id, body_text, body_html in results:
                export_data.append({
                    'message_id': message_id,
                    'body_text': body_text,
                    'body_html': body_html,
                })
            
            try:
                with open(json_filename, "w", encoding="utf-8") as f:
                    json.dump(export_data, f, indent=4)
                print(f"Successfully exported {len(export_data)} email bodies to '{json_filename}'.")
            except Exception as e:
                print(f"An error occurred while writing to JSON file: {e}")
            
        elif choice == '6':
            print("\n--- Processing potential SPAM sender emails ---")
            db.get_spam_sender_emails_not_in_contacts()

        elif choice == '7':
            print("\n--- Generating Email Label DataFrame ---")
            label_df = db.create_label_dataframe()
            if label_df is not None:
                print("Generated Email Label DataFrame (first 5 rows):")
                print(label_df.head())
                print("\nDataFrame Info:")
                label_df.info()
            else:
                print("Failed to generate Email Label DataFrame.")

        elif choice == '8':
            print("\n--- Exporting Formatted Messages by Label ---")
            db.export_messages_by_label()

        elif choice == '9':
            print("\n--- Updating Email Label Flags ---")
            db.update_email_label_booleans()

        elif choice == '10':
            print("\n--- Classifying 10 Random Messages ---")
            
            # NOTE: NLP functionality is currently disabled because the required
            # data files ('nlp_spacy/*.spacy') are missing.
            # To enable, create the data files and uncomment the following line:
            db.activate_nlp()

            random_ids = db.random_msg_ids(quantity=10)
            if not random_ids:
                print("No messages found in the database.")
                continue

            question_marks = ','.join('?' * len(random_ids))
            df = pd.read_sql_query(
                f"SELECT message_id, subject, body_html, body_text FROM emails WHERE message_id IN ({question_marks})",
                db.conn,
                params=random_ids
            )

            if df.empty:
                print("Could not retrieve details for the random messages.")
                continue
            
            print("\n--- Group 1: Simple Classification ---")
            for i in random_ids[:5]:
                row = df[df['message_id'] == i].iloc[0]
                print(f"\nProcessing: {row['message_id']} : {row['subject']}")
                
                html_val = row['body_html']
                text_val = row['body_text']
                
                if pd.notna(html_val) and str(html_val).strip() != "":
                    new_msg = remove_html(str(html_val))
                else:
                    new_msg = str(text_val)

                # Uncomment the following lines to enable classification
                predicted_category = db.classify_new_message(new_msg)
                print(f"  -> Predicted Category: {predicted_category}")

            print("\n--- Group 2: Classification with Probabilities ---")
            for i in random_ids[5:]:
                row = df[df['message_id'] == i].iloc[0]
                print(f"\nProcessing: {row['message_id']} : {row['subject']}")

                html_val = row['body_html']
                text_val = row['body_text']

                if pd.notna(html_val) and str(html_val).strip() != "":
                    new_msg = remove_html(str(html_val))
                else:
                    new_msg = str(text_val)
                
                # Uncomment the following lines to enable classification
                best, probs = db.classify_with_probabilities(new_msg)
                print(f"  -> Predicted: {best}")
                for cat, p in probs.items():
                    print(f"     {cat}: {p:.2%}")

        elif choice == '11':
            search_term = input("Enter the string to search for: ")
            if not search_term:
                print("No search term provided.")
                continue

            message_ids = db.search_emails(search_term)
            if not message_ids:
                print("No matching messages found.")
                continue

            # Fetch subjects for all messages once
            message_details = []
            for msg_id in message_ids:
                cursor = db.conn.cursor()
                cursor.execute("SELECT subject FROM emails WHERE message_id = ?", (msg_id,))
                result = cursor.fetchone()
                subject = result[0] if result else "No Subject"
                message_details.append({'message_id': msg_id, 'subject': subject})

            while True:
                print("\n--- Search Results ---")
                for i, detail in enumerate(message_details):
                    print(f"{i + 1}. {detail['message_id']} - {detail['subject']}")
                
                initial_action = input("\n[D]elete all shown, [S]elect a message, or [E]xit? (d/s/e): ").lower()

                if initial_action == 'e':
                    break # Exit to main menu
                elif initial_action == 'd':
                    if message_details:
                        confirm_delete_all = input(f"Are you sure you want to delete ALL {len(message_details)} messages shown? (y/N): ").lower()
                        if confirm_delete_all == 'y':
                            print("\n--- Deleting all messages ---")
                            # Iterate over a copy because message_details will be modified
                            for detail in message_details[:]:
                                db.delete_email(detail['message_id'], confirm=False)
                                message_details.remove(detail) # Remove from list after successful deletion
                            print("--- All messages deleted ---")
                            break # Go back to main menu after deleting all
                        else:
                            print("Deletion of all messages cancelled.")
                    else:
                        print("No messages to delete.")
                    continue # Continue to show search results menu

                elif initial_action == 's':
                    selection = input("\nEnter the number of the message to view, or 'e' to exit selection: ")

                    if selection.lower() == 'e':
                        continue # Go back to initial_action prompt

                    try:
                        selected_index = int(selection) - 1
                        if 0 <= selected_index < len(message_details):
                            selected_id = message_details[selected_index]['message_id']
                            db.display_message_summary(selected_id)

                            action = input("\n[E]dit, [D]elete, or [C]ontinue? (e/d/c): ").lower()
                            if action == 'd':
                                db.delete_email(selected_id, confirm=True)
                                # Refresh the list of message_details after deletion
                                message_details.pop(selected_index)
                            elif action == 'e':
                                print("Editing is not yet implemented.")
                        else:
                            print("Invalid selection.")
                    except ValueError:
                        print("Invalid input.")
                else:
                    print("Invalid choice.")

        elif choice == '12':
            print("\n--- Redact Sensitive Information ---")
            confirm = input("This will permanently redact information and is irreversible. Are you sure? (y/N): ").lower()
            if confirm == 'y':
                db.redact_sensitive_info()
            else:
                print("Redaction cancelled.")
        
        elif choice == '0':
            print("Exiting application.")
            break
        else:
            print("Invalid choice. Please try again.")

    db.close_db()
    print("\nDatabase connection closed.")

