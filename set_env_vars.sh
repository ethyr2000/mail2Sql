# set_env_vars.sh

# =========================================================================
# WARNING: DO NOT COMMIT THIS FILE TO GIT WITH ACTUAL CREDENTIALS.
# It is used locally to set secure environment variables.
# =========================================================================

# --- MariaDB Credentials --- currently not used
#export MARIADB_USER="eMailMaster"
#export MARIADB_PASSWORD="1A2B3C4D5E6F7A8B9C0D"
#export MARIADB_HOST="127.0.0.1"
#export MARIADB_DATABASE="eMailMasterDB"

# --- Gmail API Client Secret File Path ---
# NOTE: This should point to the absolute path of your downloaded
# OAuth 2.0 Client ID JSON file (e.g., /home/user/gmail-credentials.json)
export GMAIL_CLIENT_SECRET_PATH="~/PATH/TO/gmail1/credentials.json"

# --- Example Usage ---
# To run your Python script, first execute:
# source ./set_env_vars.sh
# uv run main.py
# after emails are loaded, uv run sqlite_db.py has the primary interface for working with messages
