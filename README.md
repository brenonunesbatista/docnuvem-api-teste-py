# docnuvem-api-teste

TUI de tela cheia, em Python + [Textual](https://github.com/textualize/textual),
para testar manualmente a API REST do DocNuvem (importação de arquivos,
modelos, assinatura eletrônica e consulta de documentos). Pensada para
depurar integrações de clientes: reproduzir bugs relatados, conferir
comportamento de parâmetros e validar o fluxo de importação + assinatura.

Visual estilo Turbo Pascal / Norton Commander (DOS, fim dos anos 80): fundo
azul, bordas duplas, barra de menu no topo, barra de teclas de função no
rodapé.

## Instalação

Requer Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

Ou, com [uv](https://github.com/astral-sh/uv):

```bash
uv sync
```

## Configuração

A API usa um Bearer token fixo por instância (não há endpoint de login).
Copie o arquivo de exemplo e preencha os tokens de cada cliente/instância:

```bash
cp config.example.json config.json
```

```json
{
  "perfis": {
    "sancris": {
      "instancia": "sancris",
      "baseUrl": "http://docnuvem-1325424578.sa-east-1.elb.amazonaws.com:8083",
      "token": "COLE_AQUI"
    },
    "dotteponto": {
      "instancia": "dotteponto",
      "baseUrl": "http://docnuvem-1325424578.sa-east-1.elb.amazonaws.com:8083",
      "token": "COLE_AQUI"
    }
  },
  "perfilPadrao": "sancris"
}
```

`config.json` fica fora do git (veja `.gitignore`) — nunca versione tokens.

Por padrão o programa procura `config.json` na pasta atual (o mesmo esquema
da versão anterior em Node.js). Para usar outro caminho, defina a variável
de ambiente `DOCNUVEM_TESTER_CONFIG`:

```bash
DOCNUVEM_TESTER_CONFIG=/caminho/para/config.json docnuvem-tester
```

Troque o perfil ativo a qualquer momento pelo menu **Perfil**, sem reiniciar
o programa. O perfil ativo aparece sempre na tela principal.

## Uso

```bash
docnuvem-tester
```

ou

```bash
python -m docnuvem_tester
```

### Modo debug

Console de logs do Textual (em outro terminal, antes de rodar o app):

```bash
textual console
```

Rodar com hot-reload de CSS e o console conectado:

```bash
textual run --dev -m docnuvem_tester
```

## Mapa de teclado

| Tecla                | Ação                                              |
| --------------------- | -------------------------------------------------- |
| `Tab` / `Shift+Tab`   | move o foco entre campos                          |
| Setas / `Enter`       | navega e confirma em listas e menus               |
| Clique do mouse       | funciona em qualquer botão, item de lista ou tabela |
| `F1`                  | ajuda                                             |
| `L`                   | log de requisições/respostas                      |
| `F10`                 | sair (com confirmação)                            |
| `F5` (num formulário) | executa a chamada à API                           |
| `Esc`                 | volta para a tela anterior                        |
| `C` (num resultado)   | copia o valor em destaque (link, URL, ID) para a área de transferência |

## Endpoints cobertos

1. `POST /importar`
2. `POST /enviarParaEnvioInteligente`
3. `POST /api/documento/from-template`
4. `GET /api/modelos`
5. `POST /api/assinatura`
6. `DELETE /api/assinatura/{assinaturaId}`
7. `GET /api/documentos`
8. `GET /api/documento/{documentoId}/status`
9. `GET /api/documento/{documentoId}/download`

Mais a opção **Fluxo Completo**, que encadeia `/importar` → `/api/assinatura`
com o `documentoId` já preenchido no segundo formulário.

Toda chamada fica registrada no log (tecla `L`), com método, URL completa
(incluindo os query params efetivamente enviados), timestamp, status HTTP e
o header `Authorization` sempre mascarado (`Bearer ***...últimos4`).

## Estrutura

```
src/docnuvem_tester/
  app.py                  App Textual, bindings globais (F1/L/F10)
  config.py                carrega/valida config.json e perfis
  client.py                 wrapper httpx.AsyncClient + log de chamadas
  models.py                  schemas pydantic de request/response
  formatting.py                helpers de formatação (json, valor-ou-traço)
  screens/                      uma tela por endpoint + banner, menu, log etc.
  widgets/                       menu bar, listas dinâmicas, diálogos, resultado
  styles/app.tcss                  paleta e layout fixos (DOS azul)
```

## Notas de implementação

- Toda chamada HTTP roda em `httpx.AsyncClient` dentro de workers do
  Textual — nunca bloqueia a interface.
- Navegação usa exclusivamente a pilha de telas do Textual
  (`push_screen`/`dismiss`, `ModalScreen`), sem troca manual de widget raiz.
- Nenhum valor é formatado com padding manual: tabelas usam `DataTable`,
  texto usa `Static`/`TextArea`; campos nulos da API viram `—` antes de
  serem exibidos.
- Copiar para a área de transferência usa `app.copy_to_clipboard` (OSC 52)
  — sempre o valor original, nunca uma versão truncada.
