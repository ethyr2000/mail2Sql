"""
This module provides a high-level interface for interacting with the Gmail API.

It simplifies the process of authenticating, connecting, and performing common
operations such as listing labels, fetching emails, and parsing message data.
The `GmailAPI` class encapsulates the logic for handling OAuth 2.0 flow,
building the API service, and extracting structured data from raw email messages.
"""
import os.path
import base64
import re
import datetime
from email.mime.text import MIMEText
from typing import Optional, List, Dict
import json
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from mailStructs import (
    ExtractedEmailData, RecipientTuple, EmailAttachmentModel,
    EmailXHeaderModel, EmailLabelModel, EmailAuthenticationModel, AdditionalPart
)
from config import GMAIL_SCOPES, API_TOKEN_FILE, CLIENT_SECRET_FILE


class GmailAPI:
    """
    A wrapper class for the Gmail API to simplify authentication and data fetching.
    """
    def __init__(self, credentials_file=CLIENT_SECRET_FILE, token_file=API_TOKEN_FILE):
        """
        Initializes the GmailAPI client.

        Args:
            credentials_file (str): The path to the credentials JSON file.
            token_file (str): The path to the token JSON file.
        """
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.creds = None
        self.service = None

    def connect(self):
        """
        Establishes a connection to the Gmail API.

        Handles the OAuth 2.0 authorization flow, including refreshing
        expired tokens and saving new ones.
        """
        # Load existing credentials if a token file exists.
        if os.path.exists(self.token_file):
            self.creds = Credentials.from_authorized_user_file(self.token_file, GMAIL_SCOPES)
        
        # If credentials are not valid (or don't exist), initiate the auth flow.
        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                # If credentials have expired, refresh them.
                self.creds.refresh(Request())
            else:
                # Otherwise, start a new authorization flow.
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_file, GMAIL_SCOPES
                )
                self.creds = flow.run_local_server(port=0)
            
            # Save the new or refreshed credentials to the token file.
            with open(self.token_file, "w") as token:
                token.write(self.creds.to_json())

        # Build the Gmail API service object.
        self.service = build("gmail", "v1", credentials=self.creds)

    def disconnect(self):
        """
        Disconnects from the Gmail API and removes the token file.
        """
        self.creds = None
        self.service = None
        # For security, remove the token file upon disconnection.
        if os.path.exists(self.token_file):
            os.remove(self.token_file)

    def list_tags(self) -> Optional[List[str]]:
        """
        Lists all available labels (tags) in the user's Gmail account.

        Returns:
            Optional[List[str]]: A list of label names, or None if an error occurs.
        """
        if not self.service:
            print("Not connected. Call connect() first.")
            return

        try:
            # Execute the API call to list labels.
            results = self.service.users().labels().list(userId="me").execute()
            labels = results.get("labels", [])
            
            if not labels:
                print("No labels found.")
                return []
            
            # Extract and return the names of the labels.
            label_names = [label["name"] for label in labels]
            print("Labels:")
            for name in label_names:
                print(name)
            return label_names
        except HttpError as error:
            print(f"An error occurred: {error}")
            return None

    def get_email_by_message_id(self, message_id) -> Optional[ExtractedEmailData]:
        """
        Fetches and parses a single email by its message ID.

        Args:
            message_id (str): The unique identifier of the email message.

        Returns:
            Optional[ExtractedEmailData]: A dictionary containing the parsed email data,
                                          or None if an error occurs.
        """
        if not self.service:
            print("Not connected. Call connect() first.")
            return None

        try:
            # Fetch the raw email source for complete parsing.
            message = (
                self.service
                .users()
                .messages()
                .get(userId="me", id=message_id, format="raw")
                .execute()
            )
            
            # Fetch the full message metadata for additional details.
            metadata_message = (
                self.service
                .users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )

            # Extract structured data from the fetched message.
            extracted_data = self.extract_email_data(metadata_message, message.get("raw"))
            return extracted_data

        except HttpError as error:
            print(f"An error occurred: {error}")
            return None

    def extract_email_data(self, message: dict, raw_source: str) -> ExtractedEmailData:
        """
        Parses the raw message dictionary from the Gmail API into a structured format.

        Args:
            message (dict): The message resource from the API (format='full').
            raw_source (str): The raw, base64-encoded email source (format='raw').

        Returns:
            ExtractedEmailData: A dictionary containing structured email data.
        """
        payload = message.get("payload", {})
        headers = payload.get("headers", [])
        
        def get_header(name):
            # Helper to find a header value by its name (case-insensitive).
            return next((h["value"] for h in headers if h["name"].lower() == name.lower()), None)

        def parse_recipients(header_value: str) -> list[RecipientTuple]:
            # Parses recipient strings (e.g., "Name <email@example.com>") into tuples.
            if not header_value:
                return []
            recipients = []
            # Regex to handle both "Name <email>" and just "email" formats.
            matches = re.findall(r'([^<,"\'\s]+(?:\s+[^<,"\'\s]+)*)\s*<([^>]+)>|(\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b)', header_value)
            for match in matches:
                if match[1]: # "Name <email>" format
                    recipients.append((match[0].strip().replace('"', ''), match[1]))
                elif match[2]: # "email" format
                    recipients.append(("", match[2]))
            return recipients

        def parse_auth_results(header_value: str) -> Optional[EmailAuthenticationModel]:
            # Parses the 'Authentication-Results' header for SPF, DKIM, and DMARC status.
            if not header_value:
                return None
            
            auth_results: EmailAuthenticationModel = {}
            
            # Regex to extract status and domain for each authentication method.
            spf_match = re.search(r'spf=(\w+)\s.*header\.from=([\w\.\-]+)', header_value)
            if spf_match:
                auth_results["spf_status"] = spf_match.group(1)
                auth_results["spf_domain"] = spf_match.group(2)

            dkim_match = re.search(r'dkim=(\w+)\s.*header\.d=([\w\.\-]+)', header_value)
            if dkim_match:
                auth_results["dkim_status"] = dkim_match.group(1)
                auth_results["dkim_domain"] = dkim_match.group(2)

            dmarc_match = re.search(r'dmarc=(\w+)', header_value)
            if dmarc_match:
                auth_results["dmarc_status"] = dmarc_match.group(1)

            return auth_results if auth_results else None

        def get_body_part(parts, mime_type):
            # Recursively searches for a message part with a specific MIME type.
            for part in parts:
                if part.get("mimeType") == mime_type and part.get("body", {}).get("data"):
                    # Decode the base64-encoded body data.
                    return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
                if "parts" in part:
                    # Recurse into nested parts.
                    result = get_body_part(part["parts"], mime_type)
                    if result:
                        return result
            return None
        
        body_text = None
        body_html = None
        attachments: list[EmailAttachmentModel] = []
        additional_parts: list[AdditionalPart] = []

        if "parts" in payload:
            # Find primary text and HTML content.
            body_text = get_body_part(payload["parts"], "text/plain")
            body_html = get_body_part(payload["parts"], "text/html")

            # Identify attachments and other non-primary parts.
            for part in payload["parts"]:
                if part.get("filename"):
                    body = part.get("body", {})
                    attachments.append({
                        "message_id": message["id"],
                        "filename": part.get("filename"),
                        "mime_type": part.get("mimeType"),
                        "attachment_size": body.get("size"),
                    })
                elif not any(x in part.get("mimeType", "") for x in ["text/plain", "text/html"]):
                    additional_parts.append({
                        "part_id": part.get("partId"),
                        "mime_type": part.get("mimeType"),
                        "filename": part.get("filename"),
                        "size": part.get("body", {}).get("size", 0)
                    })
        elif payload.get("body", {}).get("data"):
             # Handle single-part messages.
             if payload.get("mimeType") == "text/plain":
                 body_text = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
             elif payload.get("mimeType") == "text/html":
                 body_html = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

        # Extract all 'X-' headers.
        xheaders: list[EmailXHeaderModel] = [
            {"message_id": message["id"], "header_name": h["name"], "header_value": h["value"]}
            for h in headers if h["name"].lower().startswith("x-")
        ]

        # Extract all labels associated with the message.
        labels: list[EmailLabelModel] = [
            {"message_id": message["id"], "label_name": label}
            for label in message.get("labelIds", [])
        ]
        
        # Parse the 'Date' header into a datetime object.
        sent_timestamp_str = get_header("Date")
        sent_timestamp = None
        if sent_timestamp_str:
            try:
                # Attempt to parse various common date formats.
                sent_timestamp = datetime.datetime.fromisoformat(sent_timestamp_str.replace(" (UTC)", "+00:00").replace(" (GMT)", "+00:00").replace("T", " "))
            except (ValueError, TypeError):
                try:
                    sent_timestamp = datetime.datetime.strptime(sent_timestamp_str, '%a, %d %b %Y %H:%M:%S %z')
                except (ValueError, TypeError):
                    sent_timestamp = None # Could not parse the date.

        # Parse sender's name and email from the 'From' header.
        sender_header = get_header("From")
        sender_email = ""
        sender_name = None
        if sender_header:
            match = re.search(r'<([^>]+)>', sender_header)
            if match:
                sender_email = match.group(1)
                sender_name = sender_header.split('<')[0].strip().replace('"', '')
            else:
                sender_email = sender_header.strip()

        # Assemble the final structured data dictionary.
        return {
            "message_id": message.get("id"),
            "thread_id": message.get("threadId"),
            "sender_email": sender_email,
            "subject": get_header("Subject"),
            "body_text": body_text,
            "body_html": body_html,
            "sent_timestamp": sent_timestamp,
            "internal_date_ms": int(message.get("internalDate", 0)),
            "date_received": get_header("Received"),
            "mime_type": payload.get("mimeType"),
            "content_transfer_encoding": get_header("Content-Transfer-Encoding"),
            "to_recipients": parse_recipients(get_header("To")),
            "cc_recipients": parse_recipients(get_header("Cc")),
            "bcc_recipients": parse_recipients(get_header("Bcc")),
            "sender": sender_header,
            "sender_name": sender_name,
            "snippet": message.get("snippet"),
            "raw_source": base64.urlsafe_b64decode(raw_source).decode('utf-8', errors='ignore') if raw_source else None,
            "attachments": attachments,
            "xheaders": xheaders,
            "labels": labels,
            "authentication_results": parse_auth_results(get_header("Authentication-Results")),
            "additional_parts": additional_parts,
            "return_path": get_header("Return-Path"),
            "header_sender": get_header("Sender"),
        }

    def save_extracted_email_as_json(self, extracted_email: ExtractedEmailData):
        """
        Saves the extracted email data to a JSON file.

        The filename is the message_id of the email.
        """
        message_id = extracted_email.get("message_id")
        if not message_id:
            print("Error: Extracted email data must have a 'message_id' to save.")
            return

        filename = f"{message_id}.json"
        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(extracted_email, f, indent=4, default=str, ensure_ascii=False)
            print(f"Successfully saved email data to {filename}")
        except Exception as e:
            print(f"An error occurred while saving to {filename}: {e}")

    def get_email_by_query(self, query, max_count=100, offset=0):
        """
        Fetches and displays emails matching a given query.
        
        NOTE: This is a simple demonstration function. For robust fetching,
        use `yield_emails_from_query`.
        """
        if not self.service:
            print("Not connected. Call connect() first.")
            return

        try:
            results = (
                self.service
                .users()
                .messages()
                .list(userId="me", q=query, maxResults=max_count)
                .execute()
            )
            messages = results.get("messages", [])
            if not messages:
                print("No messages found.")
                return

            for i in range(offset, len(messages)):
                msg = (
                    self.service
                    .users()
                    .messages()
                    .get(userId="me", id=messages[i]["id"])
                    .execute()
                )
                self.show_message(msg)
        except HttpError as error:
            print(f"An error occurred: {error}")

    def yield_emails_from_query(self, query: str, max_count: int = 100):
        """
        A generator that yields ExtractedEmailData for emails matching a query.
        Handles API pagination automatically.

        Args:
            query (str): The search query (e.g., "in:INBOX").
            max_count (int): The number of results to fetch per page.

        Yields:
            ExtractedEmailData: A dictionary of parsed email data for each message.
        """
        if not self.service:
            print("Not connected. Call connect() first.")
            return

        try:
            page_token = None
            while True:
                results = (
                    self.service
                    .users()
                    .messages()
                    .list(userId="me", q=query, maxResults=max_count, pageToken=page_token)
                    .execute()
                )
                messages = results.get("messages", [])
                if not messages:
                    break

                for message_info in messages:
                    email_data = self.get_email_by_message_id(message_info['id'])
                    if email_data:
                        yield email_data
                
                # Move to the next page if one exists.
                page_token = results.get("nextPageToken")
                if not page_token:
                    break
        except HttpError as error:
            print(f"An error occurred while fetching emails: {error}")
            return

    def get_message_ids_and_thread_ids_by_query(self, query: str, max_results_per_page: int = 500) -> List[Dict[str, str]]:
        """
        Fetches all message IDs and thread IDs for a query, handling pagination.

        Args:
            query (str): The search query (e.g., "in:INBOX").
            max_results_per_page (int): Maximum results to return per API page.

        Returns:
            List[Dict[str, str]]: A list of dictionaries, each with 'id' and 'threadId'.
        """
        if not self.service:
            print("Not connected. Call connect() first.")
            return []

        all_message_ids_and_threads = []
        page_token = None

        try:
            while True:
                # Request a page of message IDs.
                results = (
                    self.service
                    .users()
                    .messages()
                    .list(userId="me", q=query, maxResults=max_results_per_page, pageToken=page_token)
                    .execute()
                )
                messages = results.get("messages", [])
                
                for message_info in messages:
                    all_message_ids_and_threads.append({
                        "id": message_info["id"],
                        "threadId": message_info["threadId"]
                    })
                
                # Check for the next page.
                page_token = results.get("nextPageToken")
                if not page_token:
                    break
            
        except HttpError as error:
            print(f"An error occurred while fetching message IDs: {error}")
        
        return all_message_ids_and_threads


    def show_snippets(self, query, max_count=100):
        """
        Fetches and displays message snippets for a given query.
        """
        if not self.service:
            print("Not connected. Call connect() first.")
            return

        try:
            results = (
                self.service
                .users()
                .messages()
                .list(userId="me", q=query, maxResults=max_count)
                .execute()
            )
            messages = results.get("messages", [])
            if not messages:
                print("No messages found.")
                return

            print("Snippets:")
            for message in messages:
                # Fetch metadata only for efficiency.
                msg = (
                    self.service
                    .users()
                    .messages()
                    .get(userId="me", id=message["id"], format="metadata")
                    .execute()
                )
                print(f"ID: {msg['id']} - Snippet: {msg['snippet']}")
        except HttpError as error:
            print(f"An error occurred: {error}")

    def show_message(self, message):
        """
        A simple helper to print the subject, sender, and body of a message.
        """
        payload = message["payload"]
        headers = payload["headers"]
        subject = next(
            (h["value"] for h in headers if h["name"] == "Subject"), "No Subject"
        )
        sender = next((h["value"] for h in headers if h["name"] == "From"), "No Sender")

        print(f"Subject: {subject}")
        print(f"From: {sender}")
        print("Body:")

        # Find and print the plain text body part.
        if "parts" in payload:
            for part in payload["parts"]:
                if part["mimeType"] == "text/plain":
                    data = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8")
                    print(data)
        else:
            data = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8")
            print(data)

if __name__ == "__main__":
    # This block demonstrates the usage of the GmailAPI class.
    gmail = GmailAPI()
    gmail.connect()
    print("Connected to Gmail API.")

    print("\nListing tags:")
    gmail.list_tags()

    print("\nGetting email snippets:")
    gmail.show_snippets(query="is:unread", max_count=5)

    # Prompt the user to enter a message ID for detailed viewing.
    message_id_input = input("\nEnter a message ID to view its content (or press Enter to skip): ")
    if message_id_input:
        print(f"\nGetting email by message ID: {message_id_input}")
        extracted_email = gmail.get_email_by_message_id(message_id_input)
        if extracted_email:
            # Avoid printing the very long raw source by default.
            raw_source_len = len(extracted_email.get("raw_source", ""))
            extracted_email_copy = extracted_email.copy()
            if raw_source_len > 0:
                extracted_email_copy["raw_source"] = f"<Raw source of length {raw_source_len}>"
            
            print(json.dumps(extracted_email_copy, indent=4, default=str))

            # Offer to save the extracted data to a file.
            save_input = input("\nSave this email data to a JSON file? (y/N): ")
            if save_input.lower() == 'y':
                gmail.save_extracted_email_as_json(extracted_email)


    gmail.disconnect()
    print("\nDisconnected from Gmail API.")
