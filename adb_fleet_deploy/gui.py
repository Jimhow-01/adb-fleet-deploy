import io
import ipaddress
import json
import os
import platform
import queue
import re
import socket
import subprocess
import tempfile
import threading
import time
import tkinter as tk
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tkinter import messagebox
from tkinter.scrolledtext import ScrolledText

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

# Para teste na sua rede atual, mantive fixo.
# Para produção, altere para None para detectar automaticamente a subnet local.
# Para produção, deixe como None para detectar automaticamente a subnet local.
# Para debug na sua rede, defina FORCED_LOCAL_IP = "x.x.x.x" (sub-rede local)
FORCED_LOCAL_IP = os.environ.get("FORCED_LOCAL_IP") or None

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
# LOG HELPER
# =========================

def default_log(message):
    print(message)


# =========================
# ADB
# =========================

def get_base_dir():
    import sys

    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)

    return Path(__file__).resolve().parent

def get_adb_path(log=default_log):
    base_dir = get_base_dir()
    system = platform.system()

    if system == "Windows":
        adb_path = base_dir / "tools" / "windows" / "adb.exe"

    elif system == "Darwin":
        adb_path = base_dir / "tools" / "macos" / "adb"

    else:
        adb_path = base_dir / "tools" / "linux" / "adb"

    if not adb_path.exists():
        raise FileNotFoundError(f"ADB embutido não encontrado em: {adb_path}")

    if system in ("Darwin", "Linux"):
        try:
            os.chmod(adb_path, 0o755)
        except Exception as chmod_error:
            log(f"Aviso: não foi possível aplicar chmod no ADB: {chmod_error}")

    return str(adb_path)

def run_adb(adb_path, args, timeout=30):
    creationflags = 0

    if platform.system() == "Windows":
        creationflags = subprocess.CREATE_NO_WINDOW

    return subprocess.run(
        [adb_path] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=creationflags
    )

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        s.connect(("1.1.1.1", 80))
        return s.getsockname()[0]
    finally:
        s.close()

#local_ip = None

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


def find_device(adb_path, log=default_log):
    local_ip = FORCED_LOCAL_IP or get_local_ip()
    #local_ip = None
    
    log(f"IP/rede usada para scan: {local_ip}")

    ips = generate_subnet_ips(local_ip)

    log(f"Escaneando {len(ips)} IPs na rede...")

    found_devices = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(try_connect_adb, adb_path, ip): ip
            for ip in ips
        }

        for future in as_completed(futures):
            result = future.result()

            if result:
                log(f"[DISPOSITIVO ENCONTRADO] {result}")
                found_devices.append(result)

    if not found_devices:
        raise RuntimeError("Nenhum dispositivo encontrado na rede.")

    return found_devices[0]


def wait_for_device(adb_path, serial, timeout_seconds=120, log=default_log):
    log(f"Aguardando device: {serial}")

    start = time.time()

    while time.time() - start < timeout_seconds:
        try:
            state = run_adb(
                adb_path,
                ["-s", serial, "get-state"],
                timeout=5
            )

            if state.stdout.strip().lower() == "device":
                log("Device pronto.")
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


def get_creds(log=default_log):
    creds = None

    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log("Atualizando token do Google Drive...")
            creds.refresh(Request())
        else:
            log("Autenticação Google necessária. O navegador será aberto.")
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


def download_from_drive(log=default_log):
    log("Iniciando download da build...")

    creds = get_creds(log=log)
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
        last_percent = -1

        while not done:
            status, done = downloader.next_chunk()
            percent = int(status.progress() * 100)

            if percent != last_percent:
                log(f"Download: {percent}%")
                last_percent = percent

    log(f"Build salva em: {ZIP_PATH}")

    return ZIP_PATH


# =========================
# BUILD / APK
# =========================

def clear_extract_dir():
    if not EXTRACT_DIR.exists():
        return

    for item in sorted(EXTRACT_DIR.rglob("*"), reverse=True):
        if item.is_file() or item.is_symlink():
            item.unlink()
        elif item.is_dir():
            try:
                item.rmdir()
            except OSError:
                pass


def extract_zip(zip_path, log=default_log):
    clear_extract_dir()
    EXTRACT_DIR.mkdir(exist_ok=True)

    log("Extraindo ZIP...")

    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(EXTRACT_DIR)

    log(f"Extraído em: {EXTRACT_DIR}")

    return EXTRACT_DIR


def find_apk(folder, log=default_log):
    apks = list(folder.rglob("*.apk"))

    if not apks:
        raise FileNotFoundError("Nenhum APK encontrado dentro do ZIP.")

    if len(apks) > 1:
        log("Mais de um APK encontrado. Usando o primeiro:")
        for apk in apks:
            log(f"- {apk}")

    apk_path = apks[0]

    log(f"APK encontrado: {apk_path}")

    return apk_path


# =========================
# DEPLOY
# =========================

def reconnect_until_ready(adb_path, serial, timeout_seconds=180, log=default_log):
    ip = serial.split(":")[0]
    start = time.time()

    log(f"Tentando reconectar em {serial}...")

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
                log("Device reconectado e pronto.")
                return True

        except Exception:
            pass

        time.sleep(3)

    raise TimeoutError(f"O dispositivo não voltou após {timeout_seconds}s")


def deploy_to_device(adb_path, serial, apk_path, log=default_log):
    log("Iniciando deploy...")

    run_adb(adb_path, ["kill-server"], timeout=10)

    ip = serial.split(":")[0]

    run_adb(adb_path, ["connect", f"{ip}:{ADB_PORT}"], timeout=10)

    wait_for_device(adb_path, serial, timeout_seconds=60, log=log)

    log("Reiniciando o dispositivo...")
    run_adb(adb_path, ["-s", serial, "reboot"], timeout=20)

    reconnect_until_ready(adb_path, serial, timeout_seconds=180, log=log)

    log("Desinstalando versão antiga...")

    uninstall = run_adb(
        adb_path,
        ["-s", serial, "uninstall", PACKAGE_NAME],
        timeout=60
    )

    if uninstall.stdout.strip():
        log(uninstall.stdout.strip())

    if uninstall.stderr.strip():
        log(uninstall.stderr.strip())

    if uninstall.returncode != 0:
        log("Aviso: falha ao desinstalar ou app não estava instalado.")

    log("Instalando APK...")

    install = run_adb(
        adb_path,
        ["-s", serial, "install", "-r", str(apk_path)],
        timeout=300
    )

    if install.stdout.strip():
        log(install.stdout.strip())

    if install.stderr.strip():
        log(install.stderr.strip())

    if install.returncode != 0:
        raise RuntimeError("Falha ao instalar APK.")

    log("Abrindo app...")

    launch = run_adb(
        adb_path,
        ["-s", serial, "shell", "monkey", "-p", PACKAGE_NAME, "1"],
        timeout=30
    )

    if launch.stdout.strip():
        log(launch.stdout.strip())

    if launch.stderr.strip():
        log(launch.stderr.strip())

    log("Deploy finalizado com sucesso.")


def run_full_deploy(log=default_log):
    adb_path = get_adb_path(log=log)
    log(f"ADB encontrado: {adb_path}")

    zip_path = download_from_drive(log=log)
    extracted_folder = extract_zip(zip_path, log=log)
    apk_path = find_apk(extracted_folder, log=log)
    device_serial = find_device(adb_path, log=log)
    deploy_to_device(adb_path, device_serial, apk_path, log=log)


# =========================
# GUI
# =========================

class DeployApp:
    def __init__(self, root):
        self.root = root
        self.root.title("ADB Fleet Deploy")
        self.root.geometry("860x540")
        self.root.minsize(700, 420)

        self.log_queue = queue.Queue()
        self.is_running = False

        self.title_label = tk.Label(
            root,
            text="ADB Fleet Deploy",
            font=("Segoe UI", 18, "bold")
        )
        self.title_label.pack(pady=(14, 4))

        self.subtitle_label = tk.Label(
            root,
            text="Baixa a build do Drive, encontra o dispositivo na rede e faz deploy via ADB.",
            font=("Segoe UI", 10)
        )
        self.subtitle_label.pack(pady=(0, 10))

        self.run_button = tk.Button(
            root,
            text="Rodar Build",
            height=2,
            font=("Segoe UI", 11, "bold"),
            command=self.start_deploy
        )
        self.run_button.pack(fill="x", padx=14, pady=(0, 10))

        self.log_box = ScrolledText(
            root,
            state="disabled",
            font=("Consolas", 10),
            wrap="word"
        )
        self.log_box.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        self.root.after(100, self.process_log_queue)

    def log(self, message):
        timestamp = time.strftime("%H:%M:%S")
        self.log_queue.put(f"[{timestamp}] {message}")

    def process_log_queue(self):
        while not self.log_queue.empty():
            message = self.log_queue.get()

            self.log_box.config(state="normal")
            self.log_box.insert("end", message + "\n")
            self.log_box.see("end")
            self.log_box.config(state="disabled")

        self.root.after(100, self.process_log_queue)

    def start_deploy(self):
        if self.is_running:
            return

        self.is_running = True
        self.run_button.config(state="disabled", text="Rodando...")
        self.log("Processo iniciado.")

        thread = threading.Thread(target=self.run_deploy_thread, daemon=True)
        thread.start()

    def run_deploy_thread(self):
        try:
            run_full_deploy(log=self.log)
            self.log("Processo concluído com sucesso.")
            self.root.after(0, lambda: messagebox.showinfo("Sucesso", "Deploy finalizado com sucesso."))

        except Exception as error:
            error_message = str(error)
            self.log(f"ERRO: {error_message}")
            self.root.after(
                0,
                lambda msg=error_message: messagebox.showerror("Erro", msg)
            )

        finally:
            self.is_running = False
            self.root.after(0, lambda: self.run_button.config(state="normal", text="Rodar Build"))


if __name__ == "__main__":
    root = tk.Tk()
    app = DeployApp(root)
    root.mainloop()
