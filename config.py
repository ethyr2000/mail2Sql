"""
This module centralizes configuration settings for the application.

It retrieves sensitive information, such as API keys and database credentials,
from environment variables to avoid hardcoding them in the source code.
The script will exit with a fatal error if critical variables are not set.
"""

import os
import sys

# --- Gmail API Configuration ---

# The path to your downloaded OAuth 2.0 Client ID JSON file.
# This must be set as an environment variable for security.
_client_secret_path = os.getenv('GMAIL_CLIENT_SECRET_PATH')

# Ensure the client secret file path is configured before proceeding.
if not _client_secret_path:
    print("FATAL: Missing GMAIL_CLIENT_SECRET_PATH environment variable.")
    sys.exit(1)

CLIENT_SECRET_FILE = os.path.expanduser(_client_secret_path)

# The file where the API will store your access and refresh tokens.
# This file is created automatically upon the first successful authorization.
API_TOKEN_FILE = 'token.json'

# Define the scopes required for the application's functionality.
# These scopes grant permissions to read emails and manage labels.
GMAIL_SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',  # For data extraction
    'https://www.googleapis.com/auth/gmail.modify',   # For adding/removing labels
    'https://www.googleapis.com/auth/gmail.labels'    # For label creation/deletion
]

# --- SQLite Database Configuration ---
# The path for the SQLite database file.
# Can be set via an environment variable to override the default.
# set MAIL_DB_PATH as environment variable and disable line below
MAIL_DB_PATH = '.'
DATABASE_PATH = os.getenv('MAIL_DB_PATH', 'mail_database.db')

# --- MariaDB Configuration (Commented Out) ---
# The following section is an example of how to configure a MariaDB connection.
# It reads sensitive credentials from environment variables.
# These variables should be set in your shell startup script (e.g., ~/.bashrc)
# or exported before running the application.

# DB_CONFIG = {
#     'user': os.getenv('MARIADB_USER', 'default_user'),
#     'password': os.getenv('MARIADB_PASSWORD'),
#     'host': os.getenv('MARIADB_HOST', '127.0.0.1'),
#     'database': os.getenv('MARIADB_DATABASE')
# }

# Check for critical database environment variables.
# if not all([DB_CONFIG['password'], DB_CONFIG['database']]):
#     print("FATAL: Missing one or more critical MariaDB environment variables (MARIADB_PASSWORD, MARIADB_DATABASE).")
#     sys.exit(1)
