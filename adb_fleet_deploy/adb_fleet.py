import ipaddress
import platform
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ADB_PORT = 5555
MAX_WORKERS = 50
TIMEOUT_SECONDS = 2

def get_adb_path():
    system = platform.system()

    if system == "Windows":
        adb_path = Path(str(Path.home() / 'platform-tools' / 'adb.exe'))

    elif system == "Darwin":
        adb_path = Path("./tools/macos/adb")

    else:
        adb_path = Path("./tools/linux/adb")

    if not adb_path.exists():
        raise FileNotFoundError(
            f"ADB não encontrado em: {adb_path}"
        )

    return str(adb_path)


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        # Não precisa realmente acessar a internet,
        # apenas força o socket a descobrir a interface local
        s.connect(("1.1.1.1", 80))
        return s.getsockname()[0]

    finally:
        s.close()


def generate_subnet_ips(local_ip):
    network = ipaddress.ip_network(
        f"{local_ip}/24",
        strict=False
    )

    return [str(ip) for ip in network.hosts()]


def try_connect_adb(adb_path, ip):
    serial = f"{ip}:{ADB_PORT}"

    try:
        # limpa conexões antigas
        subprocess.run(
            [adb_path, "disconnect", serial],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS
        )

        # tenta conectar
        subprocess.run(
            [adb_path, "connect", serial],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS
        )

        # valida se realmente virou um device adb
        state = subprocess.run(
            [adb_path, "-s", serial, "get-state"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS
        )

        if state.stdout.strip().lower() == "device":
            return serial

    except Exception:
        pass

    return None


def find_device():
    adb_path = get_adb_path()

    #depois trocar
    local_ip = get_local_ip()

    print(f"IP local detectado: {local_ip}")

    ips = generate_subnet_ips(local_ip)

    print(f"Escaneando {len(ips)} IPs na rede...")

    found_devices = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                try_connect_adb,
                adb_path,
                ip
            ): ip for ip in ips
        }

        for future in as_completed(futures):
            result = future.result()

            if result:
                print(f"[DISPOSITIVO ENCONTRADO] {result}")

                found_devices.append(result)

    return found_devices


if __name__ == "__main__":

    devices = find_device()

    print("\nResultado final:")

    if devices:

        for device in devices:
            print(f"✅ Dispositivo encontrado: {device}")

    else:
        print("❌ Nenhum dispositivo encontrado na rede.")
        
        
    