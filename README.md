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
- **Histórico de chamadas** que sobrevive a fechar o programa, com favoritos da sessão,
  reenvio, **exportação em JSON e CSV** e limpeza.
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

## Desenvolvimento

```bash
pip install -e ".[dev]"
pytest              # testes (sem rede)
ruff check . && ruff format --check .
mypy
docnuvem-spec       # compara a API atual com o que a ferramenta conhece
```

`docnuvem-spec` baixa o OpenAPI público da API e avisa de endpoints e campos novos ou removidos
(sai com código 1 se algo mudou). O CI roda isso toda semana quando a variável
`DOCNUVEM_SPEC_URL` está definida no repositório.

## Mais

- Plano de próximas etapas: [docs/ROADMAP.md](docs/ROADMAP.md)
- A página usa um runtime de terceiros: [webapp/THIRD_PARTY.md](src/docnuvem_tester/webapp/THIRD_PARTY.md)
