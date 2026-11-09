# adb-fleet-deploy

> Deploy de APK em massa para um parque de dispositivos Android via ADB: descoberta por varredura paralela de sub-rede (50 workers), download do build do Google Drive com OAuth2 e instalação com reconexão automática.

![CI](https://img.shields.io/github/actions/workflow/status/Jimhow-01/adb-fleet-deploy/ci.yml?branch=main&label=CI&logo=githubactions&logoColor=white) ![Tests](https://img.shields.io/badge/tests-17%20passing-brightgreen?logo=pytest&logoColor=white) ![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json) ![Python](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white) ![Platform](https://img.shields.io/badge/platform-Windows-lightgrey) ![Status](https://img.shields.io/badge/status-portfolio%20showcase-lightgrey)

![Python](https://img.shields.io/badge/Python-377641?logo=python&logoColor=white) ![Android](https://img.shields.io/badge/Android-3DDC84?logo=android&logoColor=white) ![ADB](https://img.shields.io/badge/ADB-3DDC84) ![Google Drive](https://img.shields.io/badge/Google%20Drive-0F9D58?logo=googledrive&logoColor=white) ![Tkinter](https://img.shields.io/badge/Tkinter-3776AB) ![PyInstaller](https://img.shields.io/badge/PyInstaller-E84D39)

## O problema

Atualizar dezenas de dispositivos Android um a um é trabalho manual puro. Este pipeline **descobre** os dispositivos na rede em paralelo, baixa o build do Drive e instala com reconexão automática — com GUI para quem não é dev e CLI para automação.

A descoberta é paralela; **a instalação é sequencial, num dispositivo por execução** (`find_device()` retorna `found_devices[0]` e `deploy_to_device()` é chamado uma vez). Deixar isso claro porque é a limitação real do pipeline: ele resolve o trabalho de *encontrar* os aparelhos na rede, não o de instalar em todos de uma vez.

## Arquitetura

```
adb-fleet-deploy/
├── adb_fleet_deploy/
│   ├── gui.py               GUI Tkinter multiplataforma (canonica)
│   ├── gui_windows.py       variante Windows
│   ├── deploy_pipeline.py   pipeline CLI completo
│   ├── drive_fetch.py       extrator isolado do Google Drive
│   └── adb_fleet.py         descoberta de dispositivos na sub-rede
└── packaging/adb_fleet_deploy.spec  spec PyInstaller (o adb NAO esta embutido, ver abaixo)
```

## Fluxo

```mermaid
graph LR
    A[scan sub-rede /24] --> B[adb connect :5555]
    B --> C[OAuth2 -> Drive API]
    C --> D[baixa zip do build]
    D --> E[extrai e localiza .apk]
    E --> F[reconnecta ate responder]
    F --> G[adb install]
```

## Destaques técnicos

### 📡 Descoberta paralela — `adb_fleet_deploy/adb_fleet.py`

Gera a sub-rede `/24` com `ipaddress` e varre com `ThreadPoolExecutor` de até **50 workers**, `adb connect` em cada host na porta 5555 com timeout curto por tentativa. Uma varredura sequencial de /24 leva minutos; paralela leva segundos.

NOTE: `adb_fleet.py` é um script **standalone** (tem `__main__` próprio e resolve o adb em `~/platform-tools/adb.exe` no Windows). Os fluxos de deploy da GUI e do pipeline reimplementam a varredura internamente em `find_device()` — a lógica é a mesma, mas não há um módulo comum. É a mesma dívida de cópia das variantes de GUI.

### 🔑 OAuth2 com credenciais vindas do ambiente

`InstalledAppFlow` + Drive API `readonly`. As credenciais vêm de `GOOGLE_OAUTH_CLIENT_ID`/`SECRET` e o token fica em `~/.adb_fleet_deploy/token.json`. O ID do arquivo de build é extraído do URL (`extract_drive_id()` em `gui.py`/`gui_windows.py`/`deploy_pipeline.py`; `drive_fetch.py` tem a mesma função sob o nome `extract_id()`), não hardcoded.

NOTE: "sem segredo permanente" vale para o estado em repouso, mas o fluxo materializa o `client_secret` num arquivo temporário (`NamedTemporaryFile(delete=False)`) porque a lib exige um caminho. A remoção acontece no fim do fluxo; se o OAuth lançar exceção antes, o arquivo fica em `%TEMP%`. É um defeito real do desenho, marcado no fonte.

### 🔁 Reinstalação resiliente

`reconnect_until_ready()` tenta `adb connect` até o dispositivo responder, e só então roda o install. Dispositivos Android em modo de depuração caem do ADB com frequência no meio do processo.

### 🖥️ Empacotamento — `packaging/adb_fleet_deploy.spec`

O `.spec` gera um executável único, mas **não embute o `adb`**: `binaries=[]` e `datas=[]` estão vazios no arquivo publicado (só há um comentário pedindo para colocar o platform-tools em `tools/` antes de buildar). O código resolve o adb em runtime via `sys._MEIPASS` com `chmod 0o755` quando ele estiver lá — ou seja, o empacotamento funciona, mas exige que você forneça os binários do adb no build.

## 📌 Nota sobre as variantes de GUI

`gui.py`, `gui_windows.py` e `deploy_pipeline.py` compartilham as mesmas funções por cópia, não por módulo comum — é dívida técnica que eu reconheço. **`gui.py` é a versão canônica** (multiplataforma, e com a correção de closure em `root.after` que a variante Windows recebeu depois).

## ⚠️ O `adb` não está no repositório

Os binários do Android platform-tools **não estão versionados** (são binários de terceiro). Consequência prática: `get_adb_path()` em `gui.py` procura `tools/windows/adb.exe`, `tools/macos/adb` ou `tools/linux/adb` relativo ao diretório base, e levanta `FileNotFoundError` num clone limpo.

Para rodar:

```bash
# instale o platform-tools e crie a estrutura esperada:
mkdir -p tools/windows tools/macos tools/linux
# Windows: copie adb.exe + DLLs para tools/windows/
# macOS/Linux: copie o binario adb para tools/macos/ ou tools/linux/
```

E para que o `.spec` realmente embuta no build one-file, é preciso preencher `datas=[('tools', 'tools')]` — no arquivo publicado ele está vazio, com apenas um comentário indicando onde colocar os binários antes de buildar.

`gui_windows.py` resolve o adb em `~/platform-tools/adb.exe` em vez da estrutura `tools/` — as duas variantes divergem nesse ponto de propósito histórico, e a variante multiplataforma é a que suporta o empacotamento do `.spec`.

## Testes e lint

```bash
pip install -r requirements.txt -r requirements-dev.txt
ruff check .          # lint
pytest                # 17 testes
```

O CI roda lint, `compileall` e pytest em `windows-latest` (Python 3.11 e 3.12) a cada
push — Windows porque este código usa `pywin32`, `wmi` e `winreg`, que não existem em Linux.

A configuração do ruff está em `pyproject.toml` e declara, com o motivo de cada uma, as
regras que **não** estou corrigindo agora. Prefiro isso a escolher um subconjunto de regras
que passa por acaso.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # Windows (cp .env.example .env em Linux/macOS)
```

### Como rodar

```bash
# GUI multiplataforma (canonica)
python adb_fleet_deploy/gui.py

# variante Windows
python adb_fleet_deploy/gui_windows.py

# pipeline completo sem GUI (para automacao)
python adb_fleet_deploy/deploy_pipeline.py

# so a descoberta de dispositivos na sub-rede
python adb_fleet_deploy/adb_fleet.py

# so o download do build do Drive
python adb_fleet_deploy/drive_fetch.py
```

Antes de qualquer um deles: instale o Android platform-tools e crie a estrutura
`tools/{windows,macos,linux}/` (ver a seção sobre o `adb` acima).

Build do executável:

```bash
pyinstaller packaging/adb_fleet_deploy.spec
```

### Variáveis de ambiente

| Variável | Descrição |
|---|---|
| `GOOGLE_OAUTH_CLIENT_ID` | ver `.env.example` |
| `GOOGLE_OAUTH_CLIENT_SECRET` | ver `.env.example` |
| `BUILD_ARCHIVE_URL` | ver `.env.example` |
| `FORCED_LOCAL_IP` | ver `.env.example` |

---

## ⚠️ Sobre o que isto é — e sobre titularidade

São **scripts internos de TI** escritos durante meu trabalho em um estúdio de jogos, **sanitizados** para publicação: tokens, senhas, hosts internos, IPs, nomes de projeto e dados de terceiros foram substituídos por variáveis de ambiente ou placeholders. O estúdio não é nomeado aqui de propósito.

Duas coisas que quero deixar claras em vez de deixar você descobrir:

- **Não estou reivindicando direitos sobre este código.** Parte dele foi produzida como funcionário, e direitos sobre trabalho de empregador pertencem ao empregador. Publico como demonstração de como eu resolvi os problemas — para leitura e estudo, não como software livre. Não há licença concedida (ver `LICENSE`).
- **Não são produtos prontos para produção.** São demonstrações de abordagem técnica, com limitações declaradas nas seções acima. Copie `.env.example` para `.env` e preencha antes de rodar qualquer coisa — e rode só contra infraestrutura sua.
