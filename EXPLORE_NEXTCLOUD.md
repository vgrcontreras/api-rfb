# explore_nextcloud.py — Documentação Técnica

Script de verificação dos endpoints Nextcloud da Receita Federal, criado durante a
**Fase 0** da refatoração do `main.py`. Seu objetivo é confirmar que os endpoints de
listagem e download funcionam corretamente antes de integrar a lógica ao código de produção.

---

## Contexto

Em janeiro de 2026 a Receita Federal migrou o portal de dados públicos do CNPJ para
uma instância **Nextcloud**. Dois endpoints foram identificados e validados por este script:

| Operação | Protocolo | Endpoint |
|---|---|---|
| Listagem de arquivos | WebDAV `PROPFIND` | `/public.php/dav/files/{TOKEN}{PATH}/` |
| Download de arquivo | WebDAV `GET` | `/public.php/dav/files/{TOKEN}{PATH}/{filename}` |

---

## Configuração

```python
BASE_URL    = "https://arquivos.receitafederal.gov.br"
SHARE_TOKEN = "gn672Ad4CF8N6TK"
DATA_PATH   = "/Dados/Cadastros/CNPJ"
DOWNLOAD_DIR = "./downloads"
```

- **`BASE_URL`** — domínio do portal da Receita Federal.
- **`SHARE_TOKEN`** — token público do compartilhamento Nextcloud. Pode mudar a cada novo
  período publicado pela RF.
- **`DATA_PATH`** — caminho base dentro do compartilhamento onde ficam as pastas de período
  (`/2026-02`, `/2025-11`, etc.).
- **`DOWNLOAD_DIR`** — diretório local onde o arquivo de teste será salvo.

### Parâmetros CLI

```bash
python src/explore_nextcloud.py --year 2026 --month 02
```

`--year` e `--month` são combinados para formar o `PERIOD_PATH`:

```
PERIOD_PATH = /Dados/Cadastros/CNPJ/2026-02
```

Se omitidos, os valores padrão são `2026` e `02`.

---

## Sessão HTTP com Retry

```python
_retry = Retry(
    total=5, backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["HEAD", "GET", "OPTIONS", "PROPFIND"],
)
http = requests.Session()
http.mount("https://", HTTPAdapter(max_retries=_retry))
http.mount("http://",  HTTPAdapter(max_retries=_retry))
```

Uma única sessão `requests.Session` é criada antes de qualquer chamada e reutilizada em
todo o script. O comportamento de retry foi configurado para:

- Tentar até **5 vezes** em caso de falha.
- Aguardar um tempo crescente entre tentativas (`backoff_factor=1` → 1 s, 2 s, 4 s...).
- Repetir automaticamente para os status de erro mais comuns em servidores sobrecarregados
  (429 Too Many Requests, 5xx Server Error).
- Reconhecer o método `PROPFIND` como um método válido para retry — necessário porque
  `urllib3`, por padrão, só faz retry em métodos idempotentes conhecidos (`GET`, `HEAD`, etc.).

---

## Função 1 — `list_zip_files()`

### O que faz

Lista todos os arquivos `.zip` disponíveis no período informado consultando o servidor
via protocolo **WebDAV**.

### Como funciona

**1. Monta a URL e emite o PROPFIND**

```python
url = f"{BASE_URL}/public.php/dav/files/{SHARE_TOKEN}{PERIOD_PATH}/"
resp = http.request("PROPFIND", url, headers={"Depth": "1"}, timeout=30)
```

O método `PROPFIND` é o verbo HTTP padrão do protocolo WebDAV para listar o conteúdo de
um diretório. O header `Depth: 1` instrui o servidor a retornar os metadados do diretório
em si **e** de seus filhos diretos (um nível de profundidade). Sem esse header o servidor
retornaria apenas os metadados do diretório raiz.

**2. Verifica o status HTTP**

O servidor deve responder com **HTTP 207 Multi-Status** — o código padrão do WebDAV para
respostas que contêm múltiplos resultados. Qualquer outro código levanta um `RuntimeError`.

**3. Parseia o XML de resposta**

A resposta 207 contém um documento XML onde cada arquivo ou diretório é representado por
um elemento `<d:response>`. O nome do recurso fica no elemento filho `<d:href>`.

```xml
<d:multistatus>
  <d:response>
    <d:href>/public.php/dav/files/TOKEN/.../Cnaes.zip</d:href>
    ...
  </d:response>
  ...
</d:multistatus>
```

O parser extrai o `href` de cada `<d:response>`, pega somente o último segmento do caminho
(`split("/")[-1]`), aplica URL-decode (`unquote`) para lidar com caracteres especiais e
filtra apenas os nomes que terminam em `.zip`.

**Retorno:** lista de strings com os nomes dos arquivos, ex.:
```
['Cnaes.zip', 'Empresas0.zip', 'Empresas1.zip', ..., 'Socios9.zip']
```

---

## Função 2 — `download_file(filename)`

### O que faz

Baixa um único arquivo `.zip` do servidor via **WebDAV GET** e salva em disco,
exibindo progresso em tempo real e verificando a integridade pelo magic number.

### Como funciona

**1. Monta a URL de download**

```python
url = f"{BASE_URL}/public.php/dav/files/{SHARE_TOKEN}{PERIOD_PATH}/{filename}"
```

O mesmo caminho base do `PROPFIND` é usado, apenas com o nome do arquivo concatenado ao
final. Isso é o comportamento padrão do WebDAV: `PROPFIND` lista, `GET` baixa.

**2. Inicia o download em modo streaming**

```python
resp = http.get(url, stream=True, timeout=60)
```

`stream=True` faz com que o `requests` **não carregue o arquivo inteiro na memória**.
O corpo da resposta é disponibilizado como um iterador que produz chunks sob demanda.
Isso é essencial para arquivos grandes (os `.zip` da RF chegam a centenas de MB).

O servidor responde com **HTTP 206 Partial Content** (confirma suporte a range requests,
útil para retomar downloads interrompidos).

**3. Escreve em chunks de 1 MB**

```python
chunk_sz = 1 * 1024 * 1024  # 1 MB
for chunk in resp.iter_content(chunk_sz):
    f.write(chunk)
    downloaded += len(chunk)
    print(f"\r    Baixado : {downloaded / 1024 / 1024:.1f} MB", end="", flush=True)
```

Cada iteração do loop recebe até 1 MB da resposta e grava imediatamente no arquivo de
destino. O `\r` no print sobrescreve a linha anterior no terminal, criando uma barra de
progresso simples sem bibliotecas externas.

**4. Limpeza em caso de erro**

```python
except Exception:
    if os.path.exists(dest):
        os.remove(dest)
    raise
```

Se qualquer exceção ocorrer durante o download (timeout, conexão interrompida, erro de
disco), o arquivo parcial é removido. Isso evita que ZIPs corrompidos sejam tratados como
válidos em execuções posteriores.

**5. Verificação de integridade (magic bytes)**

```python
with open(dest, "rb") as f:
    magic = f.read(4)
if magic == b"PK\x03\x04":
    print("ZIP válido ✓")
```

Todo arquivo ZIP começa com a assinatura `PK\x03\x04` (os bytes `50 4B 03 04` em
hexadecimal, onde "PK" são as iniciais de Phil Katz, criador do formato). Ler os primeiros
4 bytes do arquivo salvo e compará-los com essa assinatura é uma forma rápida e confiável
de confirmar que o download produziu um ZIP válido — e não uma página de erro HTML salva
com extensão `.zip`.

---

## Fluxo de Execução

```
argparse → define PERIOD_PATH
    │
    ▼
list_zip_files()
    │  PROPFIND → HTTP 207 → XML → filtra .zip → retorna lista
    │
    ▼
download_file(files[0])        ← só o primeiro arquivo (teste)
    │  GET stream → HTTP 206 → chunks 1 MB → salva em disco
    │  verifica magic bytes PK\x03\x04
    ▼
  fim
```

---

## Resultados Obtidos na Fase 0

```
[1] PROPFIND → HTTP 207 — 37 arquivos .zip encontrados
[2] GET WebDAV → HTTP 206 — Cnaes.zip baixado, magic bytes PK\x03\x04 ✓
```

Endpoint `/index.php/s/{TOKEN}/download?path=...&files=...` foi descartado:
retorna `text/html` com `Content-Length: 0`.
