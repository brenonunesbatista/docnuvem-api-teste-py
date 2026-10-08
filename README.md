# Docnuvem API Tester

Interface web, em Python, para testar manualmente a API REST do Docnuvem
(importação de arquivos, modelos, assinatura eletrônica, consulta de documentos e
solicitações de escola). Serve para depurar integrações de clientes: reproduzir
bugs, conferir o comportamento de parâmetros e validar fluxos de ponta a ponta.

## Instalação

Requer Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

## Configuração

A API usa um Bearer token fixo por instância. Copie o exemplo e preencha os tokens:

```bash
cp config.example.json config.json
```

```json
{
  "perfis": {
    "cliente1": { "instancia": "cliente1", "baseUrl": "http://servidor-da-api:8083", "token": "COLE_AQUI" },
    "cliente2": { "instancia": "cliente2", "baseUrl": "http://servidor-da-api:8083", "token": "COLE_AQUI" }
  },
  "perfilPadrao": "cliente1"
}
```

O `config.json` fica fora do git (`.gitignore`): nunca versione tokens. Por padrão ele é
lido da pasta atual; para usar outro caminho, defina `DOCNUVEM_TESTER_CONFIG`.

**Gerenciar pelos perfis na tela.** O item **Perfis** do menu cria, edita e remove perfis
(nome, instância, endereço, token e proteção) e escolhe o padrão. Tudo é gravado no
próprio `config.json`, que não é publicado; a cada gravação o servidor guarda uma cópia
em `config.json.bak` (também fora do git). O token nunca volta para a página: ao editar,
deixe o campo em branco para manter o atual.

**Perfil protegido (produção).** Acrescente `"protegido"` ao perfil para evitar acidentes:

- `"protegido": true` (ou `"confirmar"`): toda chamada que altera dados ou envia e-mail pede
  confirmação na tela antes de ir para a API.
- `"protegido": "bloquear"`: o perfil só faz leituras; qualquer outra chamada é recusada.

O servidor local aplica a regra, não só a página: uma chamada sem confirmação nunca chega à API.

## Uso

```bash
docnuvem-web          # ou: python -m docnuvem_tester
```

No Windows, o atalho mais simples é dar dois cliques em `iniciar-docnuvem.bat`.

Abre o navegador em `http://127.0.0.1:8765/` (se a porta estiver bloqueada, escolhe outra).
Opções: `--porta N`, `--nao-abrir`, `--sem-historico`, `--dados PASTA`.

### O que a interface oferece

- **Telas:** importar arquivo, envio inteligente, modelos, criar documento de modelo,
  solicitar e cancelar assinatura, documentos, pastas, status, download, fluxo completo
  e, para escolas, solicitar documentos e status do aluno.
- **Requisição prevista** antes de enviar (URL final, cabeçalhos, corpo, cURL) e resultado
  em abas Resumo, JSON e Requisição, com copiar em um clique.
- **Status da API** no topo e na Início: online, lenta, token recusado, com erro ou fora do ar.
  Usa só leituras e se atualiza a cada minuto.
- **Baixar pasta**: salva no computador os documentos de uma pasta (com subpastas, se quiser,
  refazendo a árvore), com filtro opcional de status e de período. Você escolhe o destino por uma janela ou digitando o caminho;
  há "só contar" antes de baixar, andamento, cancelamento e um relatório CSV. Nunca sobrescreve
  arquivos. Só lê da API, então vale até em perfil protegido.
- **Vigia de assinaturas pendentes**: em segundo plano, consulta de tempos em tempos (a partir de
  5 min) os documentos pendentes do perfil e registra quem assinou ou visualizou, documento
  concluído, expirado ou cancelado e prazo perto de acabar. Mostra o contador no menu e pode
  notificar pelo navegador. Só leituras; para quando o programa fecha.
- **PDF de teste**: nas telas de importar, o botão "Gerar PDF de teste" cria um PDF válido de
  10 KB a 40 MB e de 1 a 50 páginas, sem precisar ter um arquivo à mão.
- **Comparar instâncias**: lê dois perfis (só leituras) e mostra o que difere em modelos,
  pastas ou na situação do diagnóstico.
- **Lote para escolas**: importa um CSV de alunos (`codigoMatricula`, `nome`, `cpf`, `email`,
  opcionais `telefone`, `tipoSolicitacao`, `emailResponsavel`), valida linha a linha e então
  solicita o envio de documentos em massa (**e-mail real**, com confirmação, intervalo entre as
  chamadas e parada após falhas seguidas) ou só consulta o status. Gera relatório CSV. Perfil
  somente leitura não envia.
- **Teste de fumaça**: roteiro para um cliente novo (API e token, importar PDF de teste,
  assinar sem e-mail, status, link de download, cancelar). **Escreve** na instância e deixa o
  PDF de teste numa pasta própria, por isso pede confirmação e não roda em perfil somente leitura.
- **Diagnóstico da instância** (menu Suporte): só com leituras, confere API, token, modelos
  (e quais são geráveis por API), pastas e o módulo de escola, e explica a causa provável de
  cada falha.
- **Relato de bug**: o botão "Copiar relato de bug" (no resultado e no log) copia perfil,
  horário, requisição, cURL com o token mascarado e resposta, pronto para colar no ticket.
  Confira se há dados pessoais antes de colar.
- **Histórico de chamadas** que sobrevive a fechar o programa, com **favoritos persistentes**
  (nunca são podados nem apagados por "Limpar"), reenvio, **exportação em JSON e CSV** e limpeza.
- Tema escuro por padrão (alternável), menu lateral retrátil (`Ctrl+B`), busca global (`/` ou `Ctrl+K`),
  validação local que pode ser desligada para testar a resposta da API a dados inválidos.

### Segurança

- As chamadas são **reais**. Importar arquivo, cancelar assinatura e solicitar documentos de
  aluno (que **envia e-mail de verdade**) alteram dados; as duas últimas pedem confirmação.
- O token nunca chega ao navegador: o servidor local injeta o `Authorization` e o parâmetro
  `instancia` (minúsculo). A página só vê o token mascarado.
- O servidor só atende `127.0.0.1`, só aceita pedidos da própria página e só repassa os
  endpoints da ferramenta.

### Dados locais

O histórico fica em `~/.docnuvem-tester/historico.jsonl` (até 5 mil chamadas). Ele guarda o que
foi enviado e recebido, **incluindo dados pessoais do que você testou**, mas nunca o token.
Mude a pasta com `--dados` ou `DOCNUVEM_TESTER_DADOS`, ou desligue com `--sem-historico`.

## Distribuição

Para quem vai só usar, sem instalar Python na máquina:

- **Executável (Windows):** `packaging\gerar-exe.bat` gera `dist\docnuvem-web.exe`, um arquivo só
  (cerca de 16 MB). Coloque o `config.json` na mesma pasta do `.exe` e dê dois cliques; ele também
  acha o `config.json` ao lado do `.exe` quando aberto por um atalho. O workflow
  `Executável` do GitHub gera o mesmo arquivo (Actions > Executável > Run workflow).
- **`pipx`** (precisa de Python): `pipx install .` na pasta do projeto, ou `pipx install
  git+<endereço do repositório>`. Cria os comandos `docnuvem-web` e `docnuvem-spec`.

O executável leva junto o runtime da página; veja a questão da licença em
[webapp/THIRD_PARTY.md](src/docnuvem_tester/webapp/THIRD_PARTY.md) antes de entregar a terceiros.

## Desenvolvimento

```bash
pip install -e ".[dev]"
pytest              # testes (sem rede)
pip install -e ".[browser]" && pytest -m browser   # testes que abrem a página num navegador
ruff check . && ruff format --check .
mypy
docnuvem-spec       # compara a API atual com o que a ferramenta conhece
```

`docnuvem-spec` baixa o OpenAPI público da API e avisa de endpoints e campos novos ou removidos
(sai com código 1 se algo mudou). O CI roda isso toda semana quando a variável
`DOCNUVEM_SPEC_URL` está definida no repositório.

## Mais

- Plano de próximas etapas: [docs/ROADMAP.md](docs/ROADMAP.md)
- Avaliação do runtime da página: [docs/runtime-da-pagina.md](docs/runtime-da-pagina.md)
- A página usa um runtime de terceiros: [webapp/THIRD_PARTY.md](src/docnuvem_tester/webapp/THIRD_PARTY.md)
