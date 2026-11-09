import io
import json
import os
import re
import tempfile
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

DOWNLOADS_DIR = Path.home() / "Downloads"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
FILE_URL = os.environ["BUILD_ARCHIVE_URL"]
OUTPUT = DOWNLOADS_DIR / "Build.zip"
APP_DIR = Path.home() / ".adb_fleet_deploy"
APP_DIR.mkdir(exist_ok=True)
TOKEN_PATH = APP_DIR / "token.json"


# Cole aqui o conteúdo do seu credentials.json
# Não coloque token.json aqui.c
CREDENTIALS_DATA = {
    "installed": {
        "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        "project_id": "example-project",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
        "redirect_uris": ["http://localhost"]
    }
}


def create_temp_credentials_file():
    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        delete=False,
        encoding="utf-8"
    )

    json.dump(CREDENTIALS_DATA, temp_file)
    temp_file.close()

    return temp_file.name


def get_creds():
    creds = None

    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            credentials_path = create_temp_credentials_file()

            try:
                flow = InstalledAppFlow.from_client_secrets_file(
                    credentials_path,
                    SCOPES
                )
                creds = flow.run_local_server(port=0)

            finally:
                if os.path.exists(credentials_path):
                    os.remove(credentials_path)

        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")

    return creds


def extract_id(url):
    m = (
        re.search(r"/(?:file/d|folders)/([a-zA-Z0-9_-]+)", url)
        or re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url)
    )

    if not m:
        raise ValueError("No file/folder ID found in URL")

    return m.group(1)


def download(service, file_id, out_path):
    request = service.files().get_media(fileId=file_id)

    with io.FileIO(out_path, "wb") as fh:
        downloader = MediaIoBaseDownload(
            fh,
            request,
            chunksize=10 * 1024 * 1024
        )

        done = False

        while not done:
            status, done = downloader.next_chunk()
            print(f"{int(status.progress() * 100)}%")


if __name__ == "__main__":
    creds = get_creds()

    service = build("drive", "v3", credentials=creds)

    download(
        service,
        extract_id(FILE_URL),
        OUTPUT
    )

    print(f"Saved to {OUTPUT}")