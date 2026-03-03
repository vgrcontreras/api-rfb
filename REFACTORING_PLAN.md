# Plano de Refatoração: api-rfb para nova estrutura Nextcloud da Receita Federal

## Contexto

Em janeiro de 2026, a Receita Federal migrou o portal de dados públicos do CNPJ de um
diretório HTTP simples para uma instância **Nextcloud** (plataforma de file sharing).

| | Antes | Depois |
|---|---|---|
| URL | `https://.../dados_abertos_cnpj/2025-02/` | `https://.../index.php/s/gn672Ad4CF8N6TK?dir=/Dados/Cadastros/CNPJ/2026-02` |
| Listagem | HTML puro → BeautifulSoup + regex | Nextcloud WebDAV PROPFIND (API padrão) |
| Download | `base_url + filename` | `/public.php/dav/files/{TOKEN}{PATH}/{filename}` (WebDAV GET) |

O objetivo é trocar a camada de descoberta/download dos arquivos `.zip` sem alterar nada
na extração, transformação e carga no PostgreSQL.

---

## Fase 0 — Verificação dos endpoints (CONCLUÍDA)

Verificação realizada via `src/explore_nextcloud.py` (script `requests` puro).
Ver documentação detalhada em `EXPLORE_NEXTCLOUD.md`.

### Resultados

| Endpoint | Método | Status | Resultado |
|---|---|---|---|
| `/public.php/dav/files/{TOKEN}{PATH}/` | `PROPFIND` + `Depth: 1` | **207** | 37 arquivos `.zip` listados ✓ |
| `/public.php/dav/files/{TOKEN}{PATH}/{file}` | `GET` streaming | **206** | ZIP válido (`PK\x03\x04`) ✓ |
| `/index.php/s/{TOKEN}/download?path=...&files=...` | `GET` | 200 / HTML | **Descartado** — retorna HTML vazio |

---

## Arquivos a Modificar

| Arquivo | O que muda |
|---|---|
| `src/main.py` | Core: remover scraping HTML, adicionar WebDAV + download Nextcloud |
| `.env_template` | Adicionar 3 novas variáveis Nextcloud |
| `.env` | Adicionar as mesmas 3 variáveis com valores reais |
| `pyproject.toml` | Remover dependências não usadas |

---

## Passo 1 — Dependências (`pyproject.toml`)

### Remover
```
beautifulsoup4, bs4, lxml, wget, soupsieve
```
São usadas **exclusivamente** pelo bloco de scraping HTML que será deletado.

### Manter
Todos os outros: `requests`, `sqlalchemy`, `psycopg2-binary`, `pandas`, etc.

### Nenhuma dependência nova necessária
O XML retornado pela WebDAV API é parseado com `xml.etree.ElementTree` (stdlib do Python).

Após editar: `poetry lock --no-update && poetry install`

---

## Passo 2 — Variáveis de Ambiente (`.env_template` e `.env`)

Acrescentar ao final dos dois arquivos:
```dotenv
# Nextcloud — Portal Receita Federal
NEXTCLOUD_BASE_URL=https://arquivos.receitafederal.gov.br
NEXTCLOUD_SHARE_TOKEN=gn672Ad4CF8N6TK
NEXTCLOUD_DATA_PATH=/Dados/Cadastros/CNPJ
```

> O `NEXTCLOUD_SHARE_TOKEN` pode mudar quando a RF publicar dados de um novo período.
> Ficando em `.env`, fica fácil de atualizar sem alterar o código.

---

## Passo 3 — Imports (`src/main.py` linhas 1–21)

### Remover
```python
import bs4 as bs        # linha 6  — scraping HTML
import ftplib           # linha 7  — não usado em lugar algum
import gzip             # linha 8  — não usado em lugar algum
import urllib.request   # linha 16 — usada só no bloco de scraping
import wget             # linha 17 — só em bloco comentado
# e o segundo `import requests` duplicado na linha 19
```

### Adicionar
```python
import argparse
import xml.etree.ElementTree as ET
from datetime import date
from urllib.parse import unquote
```

> `urlencode` removido: a URL de download é um path WebDAV simples, sem query string.

---

## Passo 4 — Entrada do Período de Referência (substituir linhas 86–91)

Trocar o `input()` de URL completa por dois parâmetros estruturados (ano + mês),
com suporte a CLI (`--year 2026 --month 02`) e fallback interativo com default
para o mês atual.

```python
parser = argparse.ArgumentParser()
parser.add_argument('--year',  type=str, default=None)
parser.add_argument('--month', type=str, default=None)
args, _ = parser.parse_known_args()

_today = date.today()
if args.year and args.month:
    reference_year  = args.year.strip()
    reference_month = args.month.strip().zfill(2)
else:
    reference_year  = input(f"Ano de referência (padrão: {_today.year}): ").strip() or str(_today.year)
    reference_month = input(f"Mês de referência (padrão: {str(_today.month).zfill(2)}): ").strip().zfill(2) \
                      or str(_today.month).zfill(2)

# Validações básicas
if not re.fullmatch(r'\d{4}', reference_year) or not (1 <= int(reference_month) <= 12):
    print("Erro: período inválido.")
    sys.exit(1)

period_path = f"{getEnv('NEXTCLOUD_DATA_PATH')}/{reference_year}-{reference_month}"
```

---

## Passo 5 — Sessão HTTP compartilhada (refatorar de dentro do loop)

Hoje a sessão `http` com retry é criada **dentro** do loop de download (recriada por arquivo).
Extrair para antes da listagem de arquivos, adicionando `"PROPFIND"` nos `allowed_methods`:

```python
_retry = Retry(
    total=5, backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["HEAD", "GET", "OPTIONS", "PROPFIND"],
)
_adapter = HTTPAdapter(max_retries=_retry)
http = requests.Session()
http.mount("https://", _adapter)
http.mount("http://",  _adapter)
```

---

## Passo 6 — Listagem de arquivos via WebDAV (substituir linhas 112–139)

Nova função `list_nextcloud_zip_files()` usando **WebDAV PROPFIND** (confirmado na Fase 0):

```
PROPFIND {BASE_URL}/public.php/dav/files/{TOKEN}{PERIOD_PATH}/
Headers: Depth: 1
Espera: HTTP 207 → XML com <d:response> por arquivo
```

Extrair nome do arquivo do `<d:href>`, filtrar `.zip`, aplicar URL-decode (`unquote`).

Se o PROPFIND não retornar 207 → `RuntimeError` com mensagem descritiva apontando o
`NEXTCLOUD_SHARE_TOKEN` e `NEXTCLOUD_DATA_PATH` no `.env` como prováveis causas.

> Estratégias de fallback (OCS API, scraping HTML) foram removidas do plano: o PROPFIND
> foi confirmado funcional e adicionar fallbacks aumentaria a complexidade sem necessidade.

---

## Passo 7 — Download com URL Nextcloud (substituir linhas 160–204)

> **Verificado na Fase 0**: o endpoint `/index.php/s/{TOKEN}/download?path=...&files=...`
> retorna HTML vazio (200 sem conteúdo). O download correto é feito via **WebDAV GET**,
> o mesmo endpoint base usado no PROPFIND.

Nova URL de download (WebDAV GET):
```
{BASE_URL}/public.php/dav/files/{TOKEN}{PERIOD_PATH}/{FILENAME}
```

Exemplo real confirmado:
```
GET https://arquivos.receitafederal.gov.br/public.php/dav/files/gn672Ad4CF8N6TK/Dados/Cadastros/CNPJ/2026-02/Cnaes.zip
→ HTTP 206, magic bytes PK\x03\x04 ✓
```

Melhorias em relação ao código atual:
- `stream=True` com escrita em chunks de 1 MB → não carrega o arquivo inteiro em RAM
- Chama `check_diff()` antes de baixar → idempotente (reruns não redownload)
- Remove arquivo parcial em caso de erro → evita ZIPs corrompidos na extração
- Remove o `requests.head(base_url)` que era feito **dentro** do loop por arquivo

---

## Passo 8 — O que NÃO muda

- `check_diff()` — funciona com qualquer URL
- `makedirs()`, `to_sql()`, `getEnv()` — inalterados
- Carregamento do `.env` — inalterado (só adicionamos vars)
- Loop de extração de ZIPs (linhas 217–227)
- Todo o bloco de carga no PostgreSQL (linhas 230–919)
- Criação de índices

---

## Verificação End-to-End

1. **Endpoints já confirmados** via `src/explore_nextcloud.py` (Fase 0 concluída):
   PROPFIND → HTTP 207 (37 arquivos), WebDAV GET → HTTP 206 + ZIP válido.

2. **Teste de listagem**:
   ```bash
   cd /home/vgrcontreras/repos/msl-brasil/api-rfb
   python src/main.py --year 2026 --month 02
   # Deve imprimir a lista de arquivos .zip e parar (ou seguir com download)
   ```

3. **Teste de download de um único arquivo** (editar temp para só baixar o primeiro):
   Verificar que o arquivo aparece em `OUTPUT_FILES_PATH` com o tamanho correto.

4. **Teste de extração**: Verificar que o ZIP extrai para `EXTRACTED_FILES_PATH`.

5. **Teste de carga (opcional)**: Rodar o script completo com `DB_NAME` apontando para
   um banco de teste, verificar que as tabelas `rfb.*` são populadas corretamente.
