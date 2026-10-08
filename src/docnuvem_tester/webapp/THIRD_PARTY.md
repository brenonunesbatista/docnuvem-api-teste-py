# Código de terceiros

## `dc-runtime.js`

- **O que é:** o runtime que renderiza páginas no formato `.dc.html` (componentes com `<x-dc>`),
  copiado do artifact "Docnuvem Testes API", criado no tipo **Design** do Claude. Já traz o
  React (licença MIT, aviso no topo do arquivo).
- **Versão:** a do artifact em 2026-10-07 (contrato 0.2.47).
- **SHA-256:** `3f160299989dc595cf434dac7b7f3e6fd20ed015a2d99e075c3eac09dffe0f10`
- **Não edite o arquivo.** Ele é minificado e gerado.

### Situação da licença do runtime em si: **não confirmada**

Verificado em 2026-10-08:

- O arquivo só traz avisos de licença do **React** (MIT, Facebook). Não há nenhum aviso, nome ou
  termo próprio do runtime (nenhuma menção a Anthropic ou a uma licença).
- Uma busca pública não achou documentação sobre a licença do `dc-runtime.js` nem sobre reuso do
  que o tipo Design exporta.
- As Condições da Anthropic atribuem ao usuário os direitos sobre o que o Claude gera, mas com ressalvas
  ("se houver", "na medida permitida em lei"), e não tratam desse arquivo em particular. Isso não substitui
  uma confirmação.

**Consequência prática:** para uso interno da equipe, o risco é pequeno. **Antes de entregar o
`.exe` ou o pacote a terceiros**, obtenha uma resposta por escrito.

### Pergunta pronta para enviar (suporte da Anthropic ou responsável jurídico)

> Criei uma ferramenta interna usando o tipo "Design" do Claude (artifacts). A página publicada carrega
> um arquivo `dc-runtime.js` (cerca de 188 KB, com o React MIT embutido). Copiei esse arquivo e passei a
> distribuí-lo junto com uma ferramenta que escrevi (executável e pacote Python), sem alterá-lo.
> 1) Os termos permitem copiar e redistribuir o `dc-runtime.js` assim? 2) Se sim, há aviso de
> licença/atribuição que devo incluir? 3) Se não, existe uma versão do runtime com licença que permita
> redistribuição?

### Como atualizar

1. Abra o artifact e baixe `artifact-type/dc-runtime.js`.
2. Substitua o arquivo, atualize a versão e o SHA-256 acima.
3. Rode `pytest` e abra a página para conferir que tudo continua funcionando.

O arquivo é servido localmente (`/dc-runtime.js`), então a página não precisa de internet,
só das fontes do Google.
