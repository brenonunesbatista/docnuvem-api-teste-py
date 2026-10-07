# Código de terceiros

## `dc-runtime.js`

- **O que é:** o runtime que renderiza páginas no formato `.dc.html` (componentes com `<x-dc>`),
  copiado do artifact "Docnuvem Testes API", criado no tipo **Design** do Claude. Já traz o
  React (licença MIT, aviso no topo do arquivo).
- **Versão:** a do artifact em 2026-10-07 (contrato 0.2.47).
- **SHA-256:** `3f160299989dc595cf434dac7b7f3e6fd20ed015a2d99e075c3eac09dffe0f10`
- **Não edite o arquivo.** Ele é minificado e gerado.
- **Licença do runtime em si:** não verificada. Confirme os termos antes de distribuir o projeto
  fora da equipe.

### Como atualizar

1. Abra o artifact e baixe `artifact-type/dc-runtime.js`.
2. Substitua o arquivo, atualize a versão e o SHA-256 acima.
3. Rode `pytest` e abra a página para conferir que tudo continua funcionando.

O arquivo é servido localmente (`/dc-runtime.js`), então a página não precisa de internet,
só das fontes do Google.
