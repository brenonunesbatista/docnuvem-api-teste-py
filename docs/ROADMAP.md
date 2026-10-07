# Roadmap

Etapas em ordem de prioridade. Cada etapa só começa quando a anterior estiver fechada.
Legenda: ✅ feito · 🔜 próximo · ⬜ planejado.

## Etapa 1 — Base sólida ✅

- ✅ Só a interface web (a TUI foi aposentada); dependência única: `httpx`.
- ✅ Testes automáticos sem rede (configuração, proxy, segurança, status, histórico, contrato).
- ✅ `ruff`, `mypy`, `pytest` e CI no GitHub.
- ✅ `docnuvem-spec`: avisa quando a API ganha ou perde endpoints e campos.
- ✅ Status da API (online, lenta, token recusado, erro, fora do ar) no topo e na Início.
- ✅ Histórico de chamadas em disco, com exportação JSON/CSV.
- ✅ Tema escuro por padrão, menu retrátil, barras de rolagem refinadas.
- ✅ README enxuto e `.gitignore` mínimo.

## Etapa 2 — Menos trabalho no suporte ✅

- ✅ **Diagnóstico da instância.** Roda só leituras e mostra o que falta: token válido, modelos
   cadastrados (e quais são geráveis por API), pastas, tipos de solicitação de escola.
   *Pronto quando:* uma tela mostra um check por item, com a causa provável de cada falha.
- ✅ **Relato de bug pronto.** Botão que copia um texto com cURL mascarado, requisição, resposta,
   perfil e horário, para colar no ticket.
   *Pronto quando:* funciona a partir do resultado e do histórico.
- ✅ **Perfil protegido.** Marcar um perfil como "produção": bloqueia ou pede confirmação em
   chamadas que escrevem ou enviam e-mail.
   *Pronto quando:* a marca fica no `config.json` e vale para toda chamada que altera dados.
- ✅ **Baixar pasta** (a pedido de um cliente): download em lote de uma pasta, com destino escolhido.
- ✅ **Gerenciador de perfis** na tela (criar, editar, remover, padrão), gravando no `config.json`.
- ✅ **Favoritos persistentes** no histórico: sobrevivem a reiniciar, à poda e a "Limpar".

## Etapa 3 — Rotina de testes ✅

1. ✅ **Teste de fumaça** (versão fixa do "roteiros salvos"). Importar, assinar, ver status, link e
   cancelar com checagens, num clique, com relatório. *Fica para depois:* roteiros que o usuário monta e salva.
2. ✅ **Comparar instâncias.** Lê dois perfis e destaca a diferença (modelos, pastas, diagnóstico).
3. ✅ **Lote para escolas.** CSV de alunos → solicitação em massa, com ensaio sem enviar, confirmação,
   intervalo entre chamadas e relatório de sucessos e erros; depois, consulta em lote de pendências.

## Etapa 4 — Conforto e distribuição ✅

1. ✅ **Vigia de assinaturas pendentes:** avisa quando mudam de status ou estão perto de expirar.
2. ✅ **Distribuição:** `.exe` (PyInstaller, testado) e `pipx`, para o suporte rodar sem instalar
   Python. *Falta testar* o workflow do GitHub que gera o `.exe`.
3. ✅ **Arquivos de teste:** gerador de PDFs válidos de vários tamanhos e números de páginas.
4. ✅ **Runtime da página:** avaliado em [runtime-da-pagina.md](runtime-da-pagina.md). Conclusão: não
   trocar agora; antes, confirmar a licença e criar testes de navegador.

## Próximos passos sugeridos

- **Licença do `dc-runtime.js`** (bloqueia entregar o `.exe` a terceiros).
- **Testes de navegador** (Playwright) para proteger a página.
- Roteiros de teste que o usuário monta e salva (hoje só há o teste de fumaça fixo).

## Decisões em aberto

- Onde guardar tokens se o projeto voltar para uma pasta sincronizada (OneDrive): `%APPDATA%` ou
  o chaveiro do sistema. Hoje o `config.json` fica na pasta do projeto, que não está sincronizada.
