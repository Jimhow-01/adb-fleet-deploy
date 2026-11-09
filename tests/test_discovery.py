"""Testes da descoberta de dispositivos e do parser de URL do Drive.

`adb_fleet.py` e `deploy_pipeline.py` importam apenas stdlib, entao importam em
qualquer plataforma. O que se testa sao as duas partes puras do pipeline: a
geracao da sub-rede e a extracao do ID do arquivo.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adb_fleet_deploy"))

import adb_fleet  # noqa: E402
import deploy_pipeline  # noqa: E402
import drive_fetch  # noqa: E402


class TestGenerateSubnetIps:
    def test_generates_254_hosts_for_a_slash_24(self):
        ips = adb_fleet.generate_subnet_ips("10.0.0.5")
        assert len(ips) == 254
        assert "10.0.0.0" not in ips      # endereco de rede
        assert "10.0.0.255" not in ips    # broadcast
        assert "10.0.0.5" in ips          # a propria maquina entra na varredura

    def test_accepts_ip_outside_the_network_boundary(self):
        """strict=False: o codigo passa o IP observado e deriva a /24 dele."""
        ips = adb_fleet.generate_subnet_ips("192.168.100.200")
        assert len(ips) == 254
        assert all(ip.startswith("192.168.100.") for ip in ips)

    def test_all_entries_are_strings(self):
        assert all(isinstance(ip, str) for ip in adb_fleet.generate_subnet_ips("172.16.0.1"))


class TestExtractDriveId:
    URLS = [
        "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz12345/view?usp=drive_link",
        "https://drive.google.com/open?id=1AbCdEfGhIjKlMnOpQrStUvWxYz12345",
        "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz12345",
        "https://drive.google.com/uc?id=1AbCdEfGhIjKlMnOpQrStUvWxYz12345",
    ]

    @pytest.mark.parametrize("url", URLS)
    def test_extracts_id_from_every_supported_shape(self, url):
        assert deploy_pipeline.extract_drive_id(url) == "1AbCdEfGhIjKlMnOpQrStUvWxYz12345"

    @pytest.mark.parametrize("url", URLS)
    def test_drive_fetch_agrees_with_pipeline(self, url):
        """As duas implementacoes devem concordar (sao duplicadas por copia)."""
        assert drive_fetch.extract_id(url) == deploy_pipeline.extract_drive_id(url)

    @pytest.mark.parametrize("url", ["", "https://example.com/nada", "not a url"])
    def test_raises_when_no_id_present(self, url):
        with pytest.raises(ValueError):
            deploy_pipeline.extract_drive_id(url)


class TestFindApk:
    def test_finds_apk_in_extracted_tree(self, tmp_path):
        (tmp_path / "app-release.apk").write_bytes(b"PK")
        # find_apk espera Path (usa folder.rglob)
        assert Path(deploy_pipeline.find_apk(tmp_path)).name == "app-release.apk"

    def test_finds_apk_in_subdirectory(self, tmp_path):
        sub = tmp_path / "build"
        sub.mkdir()
        (sub / "game.apk").write_bytes(b"PK")
        assert Path(deploy_pipeline.find_apk(tmp_path)).name == "game.apk"

    def test_raises_when_no_apk_present(self, tmp_path):
        (tmp_path / "readme.txt").write_text("sem apk", encoding="utf-8")
        with pytest.raises(FileNotFoundError):
            deploy_pipeline.find_apk(tmp_path)
