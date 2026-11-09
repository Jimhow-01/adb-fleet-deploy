import io
import ipaddress
import json
import os
import platform
import re
import socket
import subprocess
import tempfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# =========================
# CONFIG
# =========================

ADB_PORT = 5555
MAX_WORKERS = 50
TIMEOUT_SECONDS = 2

PACKAGE_NAME = "com.example.app"

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
FILE_URL = os.environ["BUILD_ARCHIVE_URL"]

APP_DIR = Path.home() / ".adb_fleet_deploy"
APP_DIR.mkdir(exist_ok=True)

TOKEN_PATH = APP_DIR / "token.json"
DOWNLOADS_DIR = Path.home() / "Downloads"

ZIP_PATH = DOWNLOADS_DIR / "Build.zip"
EXTRACT_DIR = APP_DIR / "extracted_build"

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


# =========================
# ADB
# =========================

def get_adb_path():
    system = platform.system()

    if system == "Windows":
        adb_path = Path(str(Path.home() / 'platform-tools' / 'adb.exe'))
        # Futuro empacotado:
        # adb_path = Path("./tools/windows/adb.exe")

    elif system == "Darwin":
        adb_path = Path("./tools/macos/adb")

    else:
        adb_path = Path("./tools/linux/adb")

    if not adb_path.exists():
        raise FileNotFoundError(f"ADB não encontrado em: {adb_path}")

    return str(adb_path)


def run_adb(adb_path, args, timeout=30):
    return subprocess.run(
        [adb_path] + args,
        capture_output=True,
        text=True,
        timeout=timeout
    )


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        s.connect(("1.1.1.1", 80))
        return s.getsockname()[0]
    finally:
        s.close()

local_ip = os.environ.get("FORCED_LOCAL_IP") or None  # sub-rede a varrer; None = detectar

def generate_subnet_ips(local_ip):
    network = ipaddress.ip_network(f"{local_ip}/24", strict=False)
    return [str(ip) for ip in network.hosts()]


def try_connect_adb(adb_path, ip):
    serial = f"{ip}:{ADB_PORT}"

    try:
        run_adb(adb_path, ["disconnect", serial], timeout=TIMEOUT_SECONDS)
        run_adb(adb_path, ["connect", serial], timeout=TIMEOUT_SECONDS)

        state = run_adb(
            adb_path,
            ["-s", serial, "get-state"],
            timeout=TIMEOUT_SECONDS
        )

        if state.stdout.strip().lower() == "device":
            return serial

    except Exception:
        pass

    return None


def find_device(adb_path):
    #local_ip = get_local_ip()
    local_ip = os.environ.get("FORCED_LOCAL_IP") or None  # sub-rede a varrer; None = detectar
    
    print(f"IP local detectado: {local_ip}")

    ips = generate_subnet_ips(local_ip)

    print(f"Escaneando {len(ips)} IPs na rede...")

    found_devices = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(try_connect_adb, adb_path, ip): ip
            for ip in ips
        }

        for future in as_completed(futures):
            result = future.result()

            if result:
                print(f"[DISPOSITIVO ENCONTRADO] {result}")
                found_devices.append(result)

    if not found_devices:
        raise RuntimeError("Nenhum dispositivo encontrado na rede.")

    return found_devices[0]



def wait_for_device(adb_path, serial, timeout_seconds=120):
    print(f"Aguardando device: {serial}")

    start = time.time()

    while time.time() - start < timeout_seconds:
        try:
            state = run_adb(
                adb_path,
                ["-s", serial, "get-state"],
                timeout=5
            )

            if state.stdout.strip().lower() == "device":
                print("Device pronto.")
                return True

        except Exception:
            pass

        time.sleep(2)

    raise TimeoutError(f"Device não ficou pronto dentro de {timeout_seconds}s")


# =========================
# GOOGLE DRIVE
# =========================

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


def extract_drive_id(url):
    m = (
        re.search(r"/(?:file/d|folders)/([a-zA-Z0-9_-]+)", url)
        or re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url)
    )

    if not m:
        raise ValueError("ID do arquivo do Drive não encontrado na URL.")

    return m.group(1)


def download_from_drive():
    print("Iniciando download da build...")

    creds = get_creds()
    service = build("drive", "v3", credentials=creds)

    file_id = extract_drive_id(FILE_URL)
    request = service.files().get_media(fileId=file_id)

    with io.FileIO(ZIP_PATH, "wb") as fh:
        downloader = MediaIoBaseDownload(
            fh,
            request,
            chunksize=10 * 1024 * 1024
        )

        done = False

        while not done:
            status, done = downloader.next_chunk()
            print(f"Download: {int(status.progress() * 100)}%")

    print(f"Build salva em: {ZIP_PATH}")

    return ZIP_PATH


# =========================
# BUILD / APK
# =========================

def extract_zip(zip_path):
    if EXTRACT_DIR.exists():
        for item in EXTRACT_DIR.rglob("*"):
            if item.is_file():
                item.unlink()

    EXTRACT_DIR.mkdir(exist_ok=True)

    print("Extraindo ZIP...")

    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(EXTRACT_DIR)

    print(f"Extraído em: {EXTRACT_DIR}")

    return EXTRACT_DIR


def find_apk(folder):
    apks = list(folder.rglob("*.apk"))

    if not apks:
        raise FileNotFoundError("Nenhum APK encontrado dentro do ZIP.")

    if len(apks) > 1:
        print("Mais de um APK encontrado. Usando o primeiro:")
        for apk in apks:
            print(f"- {apk}")

    apk_path = apks[0]

    print(f"APK encontrado: {apk_path}")

    return apk_path


# =========================
# DEPLOY
# =========================

def reconnect_until_ready(adb_path, serial, timeout_seconds=180):
    ip = serial.split(":")[0]
    start = time.time()

    print(f"Tentando reconectar em {serial}...")

    while time.time() - start < timeout_seconds:
        try:
            run_adb(
                adb_path,
                ["connect", f"{ip}:{ADB_PORT}"],
                timeout=5
            )

            state = run_adb(
                adb_path,
                ["-s", serial, "get-state"],
                timeout=5
            )

            if state.stdout.strip().lower() == "device":
                print("Device reconectado e pronto.")
                return True

        except Exception:
            pass

        time.sleep(3)

    raise TimeoutError(f"O dispositivo não voltou após {timeout_seconds}s")


def deploy_to_device(adb_path, serial, apk_path):
    print("Iniciando deploy...")

    run_adb(adb_path, ["kill-server"], timeout=10)

    ip = serial.split(":")[0]

    run_adb(adb_path, ["connect", f"{ip}:{ADB_PORT}"], timeout=10)

    wait_for_device(adb_path, serial, timeout_seconds=60)

    print("Reiniciando o dispositivo...")
    run_adb(adb_path, ["-s", serial, "reboot"], timeout=20)

    reconnect_until_ready(adb_path, serial, timeout_seconds=180)

    print("Desinstalando versão antiga...")

    uninstall = run_adb(
        adb_path,
        ["-s", serial, "uninstall", PACKAGE_NAME],
        timeout=60
    )

    if uninstall.returncode != 0:
        print("Aviso: falha ao desinstalar ou app não estava instalado.")

    print("Instalando APK...")

    install = run_adb(
        adb_path,
        ["-s", serial, "install", "-r", str(apk_path)],
        timeout=300
    )

    print(install.stdout)
    print(install.stderr)

    if install.returncode != 0:
        raise RuntimeError("Falha ao instalar APK.")

    print("Abrindo app...")

    run_adb(
        adb_path,
        ["-s", serial, "shell", "monkey", "-p", PACKAGE_NAME, "1"],
        timeout=30
    )

    print("Deploy finalizado com sucesso.")


# =========================
# MAIN
# =========================

if __name__ == "__main__":
    adb_path = get_adb_path()

    zip_path = download_from_drive()

    extracted_folder = extract_zip(zip_path)

    apk_path = find_apk(extracted_folder)

    device_serial = find_device(adb_path)

    deploy_to_device(adb_path, device_serial, apk_path)