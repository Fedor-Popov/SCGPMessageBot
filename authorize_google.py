"""Run on a computer with a browser to create the token used by the remote bot."""

import os
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from google_credentials import GOOGLE_SCOPES

client_file = Path(os.environ.get("GOOGLE_OAUTH_CLIENT_FILE", "oauth-client.json"))
token_file = Path(os.environ.get("GOOGLE_OAUTH_TOKEN_FILE", "google-token.json"))
flow = InstalledAppFlow.from_client_secrets_file(client_file, GOOGLE_SCOPES)
credentials = flow.run_local_server(port=0)
if credentials.granted_scopes is not None and not set(GOOGLE_SCOPES).issubset(credentials.granted_scopes):
    raise RuntimeError("Both Sheets and Calendar permissions must be approved. The existing token file has not been changed.")
token_file.write_text(credentials.to_json())
token_file.chmod(0o600)
print(f"Saved Google token to {token_file}")
