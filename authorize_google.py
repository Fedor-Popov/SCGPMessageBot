"""Run on a computer with a browser to create the token used by the remote bot."""

import os
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

client_file = Path(os.environ.get("GOOGLE_OAUTH_CLIENT_FILE", "oauth-client.json"))
token_file = Path(os.environ.get("GOOGLE_OAUTH_TOKEN_FILE", "google-token.json"))
flow = InstalledAppFlow.from_client_secrets_file(client_file, SCOPES)
credentials = flow.run_local_server(port=0)
token_file.write_text(credentials.to_json())
token_file.chmod(0o600)
print(f"Saved Google token to {token_file}")
