This project is meant to be used with the uv utility.  The .python-version and pyproject.toml will make the requirements known for the environment.

To implement the gmail api, it needs to be activated with google.  The credential file needs to be saved and the set_env_vars.sh file needs to have the GMAIL_CLIENT_SECRET_PATH assigned with the path of the file.  Also, in the Linux environment, type "source set_env_vars.sh" to assign GMAIL_CLIENT_SECRET_PATH in the environment.  

To have a gmail account have its messages extracted and placed in a database, in the terminal type "uv run main.py"  This will generate the database.

If a database exists, create a mail_database.db directory in the folder containing main.py and sqlite_db.py.  In the terminal, type "uv run sqlite_db.py" to get a text based menu for working with the database.  Also, sqlite3 can be used for unique database queries.  