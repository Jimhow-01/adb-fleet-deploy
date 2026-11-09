"""Define o ambiente que os modulos esperam antes de serem importados.

`deploy_pipeline.py`, `gui.py` e `gui_windows.py` leem variaveis de ambiente no
escopo do modulo (KeyError em import). Os valores aqui nao sao segredos reais.
"""
import os

os.environ.setdefault("BUILD_ARCHIVE_URL", "https://drive.google.com/file/d/TESTID/view")
os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "test-client-secret")
