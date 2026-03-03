"""
Fase 0 — Verificação dos endpoints Nextcloud da Receita Federal.

Endpoints confirmados:
  Listagem : PROPFIND {BASE_URL}/public.php/dav/files/{TOKEN}{PERIOD_PATH}/  → HTTP 207
  Download : GET      {BASE_URL}/public.php/dav/files/{TOKEN}{PERIOD_PATH}/{filename} → HTTP 206

Uso:
    python src/explore_nextcloud.py
    python src/explore_nextcloud.py --year 2026 --month 02
"""
import argparse
import os
import xml.etree.ElementTree as ET
from urllib.parse import unquote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------
BASE_URL    = "https://arquivos.receitafederal.gov.br"
SHARE_TOKEN = "gn672Ad4CF8N6TK"
DATA_PATH   = "/Dados/Cadastros/CNPJ"
DOWNLOAD_DIR = "./downloads"

parser = argparse.ArgumentParser()
parser.add_argument("--year",  default="2026")
parser.add_argument("--month", default="02")
args = parser.parse_args()

PERIOD_PATH = f"{DATA_PATH}/{args.year}-{args.month.zfill(2)}"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

print(f"\n{'='*60}")
print(f"  BASE_URL    : {BASE_URL}")
print(f"  TOKEN       : {SHARE_TOKEN}")
print(f"  PERIOD_PATH : {PERIOD_PATH}")
print(f"  DOWNLOAD_DIR: {DOWNLOAD_DIR}")
print(f"{'='*60}\n")

# ---------------------------------------------------------------------------
# Sessão HTTP com retry
# ---------------------------------------------------------------------------
_retry = Retry(
    total=5, backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["HEAD", "GET", "OPTIONS", "PROPFIND"],
)
http = requests.Session()
http.mount("https://", HTTPAdapter(max_retries=_retry))
http.mount("http://",  HTTPAdapter(max_retries=_retry))


# ---------------------------------------------------------------------------
# 1 — Listagem via WebDAV PROPFIND
# ---------------------------------------------------------------------------
def list_zip_files() -> list[str]:
    url = f"{BASE_URL}/public.php/dav/files/{SHARE_TOKEN}{PERIOD_PATH}/"
    print(f"[1] PROPFIND → {url}")

    resp = http.request("PROPFIND", url, headers={"Depth": "1"}, timeout=30)
    print(f"    Status : {resp.status_code}")

    if resp.status_code != 207:
        raise RuntimeError(f"PROPFIND falhou: HTTP {resp.status_code}\n{resp.text[:400]}")

    root = ET.fromstring(resp.text)
    ns   = {"d": "DAV:"}
    files = []
    for response in root.findall("d:response", ns):
        href = response.findtext("d:href", namespaces=ns) or ""
        filename = unquote(href.rstrip("/").split("/")[-1])
        if filename.lower().endswith(".zip"):
            files.append(filename)

    print(f"    Arquivos .zip encontrados ({len(files)}):")
    for f in files:
        print(f"      - {f}")
    return files


# ---------------------------------------------------------------------------
# 2 — Download de um arquivo via WebDAV GET
# ---------------------------------------------------------------------------
def download_file(filename: str) -> str:
    url      = f"{BASE_URL}/public.php/dav/files/{SHARE_TOKEN}{PERIOD_PATH}/{filename}"
    dest     = os.path.join(DOWNLOAD_DIR, filename)
    chunk_sz = 1 * 1024 * 1024  # 1 MB

    print(f"\n[2] Download → {url}")
    print(f"    Destino : {dest}")

    resp = http.get(url, stream=True, timeout=60)
    print(f"    Status  : {resp.status_code}")

    if resp.status_code not in (200, 206):
        raise RuntimeError(f"Download falhou: HTTP {resp.status_code}")

    try:
        downloaded = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_sz):
                f.write(chunk)
                downloaded += len(chunk)
                print(f"\r    Baixado : {downloaded / 1024 / 1024:.1f} MB", end="", flush=True)
        print()  # quebra de linha após o progresso
    except Exception:
        if os.path.exists(dest):
            os.remove(dest)
        raise

    size_mb = os.path.getsize(dest) / 1024 / 1024
    print(f"    Tamanho : {size_mb:.2f} MB")

    # Verificar magic bytes
    with open(dest, "rb") as f:
        magic = f.read(4)
    if magic == b"PK\x03\x04":
        print(f"    Magic   : PK\\x03\\x04 — ZIP válido ✓")
    else:
        print(f"    ATENCAO : magic bytes inesperados: {magic!r}")

    return dest


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------
files = list_zip_files()

if files:
    download_file(files[0])
else:
    print("\nNenhum arquivo encontrado — verifique TOKEN e PERIOD_PATH.")
