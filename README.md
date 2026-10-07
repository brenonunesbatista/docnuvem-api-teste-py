# docnuvem-api-teste

TUI de tela cheia, em Python + [Textual](https://github.com/textualize/textual),
para testar manualmente a API REST do DocNuvem (importação de arquivos,
modelos, assinatura eletrônica e consulta de documentos). Pensada para
depurar integrações de clientes: reproduzir bugs relatados, conferir
comportamento de parâmetros e validar o fluxo de importação + assinatura.

Visual moderno e limpo: tema escuro fixo (definido em `theme.py`, independente
do tema do terminal), cartões com bordas arredondadas, barra de menu no topo e
barra de atalhos no rodapé.

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
    "cliente1": {
      "instancia": "cliente1",
      "baseUrl": "http://docnuvem-1325424578.sa-east-1.elb.amazonaws.com:8083",
      "token": "COLE_AQUI"
    },
    "cliente2": {
      "instancia": "cliente2",
      "baseUrl": "http://docnuvem-1325424578.sa-east-1.elb.amazonaws.com:8083",
      "token": "COLE_AQUI"
    }
  },
  "perfilPadrao": "cliente1"
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

### Interface web

Além da TUI, há uma interface web moderna (menu lateral, requisição prevista,
resultado em abas Resumo/JSON/Requisição, log em gaveta, tema claro e escuro).
Ela usa os **mesmos perfis do `config.json`**:

```bash
python -m docnuvem_tester.web
```

ou, depois de `pip install -e .`, o comando `docnuvem-web`. Abre o navegador em
`http://127.0.0.1:8765/` (use `--porta` para mudar e `--nao-abrir` para não abrir
o navegador).

- **As chamadas são reais.** O aviso vermelho "API real" fica sempre no topo.
  Importar arquivo e cancelar assinatura alteram dados de verdade.
- **O token nunca vai para o navegador.** O servidor local injeta o `Authorization`
  e o parâmetro `instancia` (minúsculo); a página só recebe o token mascarado.
- **Só aceita a própria página**, em `127.0.0.1`, e só repassa os endpoints da
  ferramenta (não é um proxy aberto).
- Telas: importar, envio inteligente, modelos, criar de modelo, solicitar e cancelar
  assinatura, documentos, pastas (`GET /api/diretorios`), status, download e o
  fluxo completo. Para escolas, há também "Solicitar documentos" e "Status do
  aluno" (`/api/solicitacaoAluno/*`). Atenção: solicitar documentos **envia um
  e-mail real ao aluno**, por isso, no modo real, a tela exige marcar uma
  confirmação antes de liberar o botão.
- A página foi desenhada no artifact "Design" do Claude e usa o runtime desse
  formato (`webapp/dc-runtime.js`, servido localmente; não precisa de internet,
  só das fontes do Google). Abrindo o `index.html` direto no navegador, sem o
  servidor, o runtime não carrega: use sempre o comando acima.

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
| `F2` (num formulário) | prévia: mostra método, URL final e corpo, sem enviar (com "Copiar cURL") |
| `Esc`                 | volta para a tela anterior                        |
| `C` (num resultado)   | copia o valor em destaque (link, URL, ID) para a área de transferência |
| `F` / `R` / `C` / `S` (no log) | favoritar / reenviar / copiar cURL / só favoritos |

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

O `from-template` não tem tela própria no menu: a porta de entrada é a tela
**Modelos** — clicar num modelo da lista abre um formulário montado
dinamicamente a partir das `variaveis` daquele modelo (um campo por
variável, com o tipo de widget certo conforme `tipo`: texto, texto longo,
data, numérico, checkbox ou seleção). Campos com `aceitaPorApi: false` ou
`preenchidoPeloDestinatario: true` aparecem desabilitados e não entram no
payload. Se a API responder 400 com `variaveisFaltantes`, os campos citados
ficam com borda vermelha até o próximo envio.

Nos campos de caminho de arquivo (Importar, Envio Inteligente, Fluxo
Completo), o botão **"Selecionar arquivo..."** abre o explorador nativo do
sistema operacional (via `tkinter.filedialog`, sem travar a interface). Sem
display gráfico disponível (ex.: SSH sem X11), cai automaticamente para um
seletor dentro do terminal (`DirectoryTree` do Textual).

Toda chamada fica registrada no log (tecla `L`), com método, URL completa
(incluindo os query params efetivamente enviados), timestamp, status HTTP e
o header `Authorization` sempre mascarado (`Bearer ***...últimos4`). No log
dá para favoritar (★), reenviar a mesma chamada, copiar o cURL (token
mascarado) e filtrar só os favoritos.

Outros recursos para depurar:

- **Prévia (F2)** em qualquer formulário: mostra a requisição exata que seria
  enviada, sem enviar.
- **Resultado em abas:** "Resposta" e "Requisição" (o que foi enviado), também
  nos erros.
- **Validação inline:** campos inválidos (arquivo inexistente, extensão, CPF,
  e-mail, datas, números) ganham borda vermelha e uma mensagem abaixo do
  formulário enquanto você digita.
- **"Usar último ID":** os campos de `documentoId` têm um botão que reaproveita
  o último ID devolvido pela API na sessão.
- **Cancelar assinatura** só habilita o "Sim" depois de você digitar o ID.

## Estrutura

```
src/docnuvem_tester/
  app.py                  App Textual, bindings globais (F1/L/F10)
  config.py                carrega/valida config.json e perfis
  client.py                 wrapper httpx.AsyncClient + log de chamadas
  models.py                  schemas pydantic de request/response
  formatting.py                helpers de formatação (json, valor-ou-traço)
  web.py                         servidor local da interface web (docnuvem-web)
  webapp/index.html               a página da interface web (arquivo único)
  screens/                      uma tela por endpoint + banner, menu, log etc.
  widgets/                       menu bar, listas dinâmicas, diálogos, resultado
  theme.py + styles/app.tcss       tema escuro e layout
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
