# Avaliação: trocar o runtime da página?

Item 4 da Etapa 4 do [ROADMAP](ROADMAP.md): avaliar trocar o `dc-runtime.js` copiado do artifact por
HTML e JavaScript simples. **Conclusão: não trocar agora.** Antes, resolver a licença e criar testes
de navegador. Abaixo, os números e o raciocínio.

## Como a página funciona hoje

| Peça | Tamanho |
| --- | --- |
| `webapp/index.html` | 2.108 linhas, 215 KB |
| marcação (`<x-dc>`) | 166 `<sc-if>`, 59 `<sc-for>`, 1.164 expressões `{{ }}` |
| lógica (`class Component extends DCLogic`) | a partir da linha ~1170; `renderVals()` devolve um objeto grande com os valores de todas as telas |
| `webapp/dc-runtime.js` | 188 KB, minificado, com o React dentro (licença MIT, aviso no topo) |

O runtime é do formato `.dc.html` do tipo **Design** do Claude: ele lê a marcação `<x-dc>`, resolve os
`{{ }}`, `sc-if` e `sc-for` e redesenha com React quando o estado muda. Funciona sem internet e sem
etapa de build.

## O que pesa a favor de trocar

1. **Licença do runtime em si não verificada** (só o React, dentro dele, é MIT). Isso passou a importar
   com a distribuição: o `.exe` e o pacote `pipx` **levam o runtime junto**. Para uso interno da equipe é
   um risco pequeno; para entregar a terceiros, precisa de uma resposta antes.
2. **Opaco e impossível de editar**: é um arquivo minificado gerado. Um defeito do runtime só se
   resolve trocando o arquivo inteiro pelo do artifact.
3. **Peso**: 188 KB para renderizar uma página local. Não é problema na prática.

## O que pesa contra trocar agora

1. **Custo alto.** São 1.164 expressões e 225 blocos condicionais ou repetidos para reescrever, e todo o
   `renderVals()` para reorganizar. Em estimativa grosseira, vários dias de trabalho.
2. **Não há rede de segurança.** Os testes cobrem servidor, proxy, contrato e coerência
   página↔rotas, mas **nenhum teste abre a página num navegador**. Uma troca de runtime só se valida
   olhando tela por tela, e regressões passariam sem alarme.
3. **Funciona e está estável.** O runtime não deu nenhum problema nas quatro etapas; os bugs de tela
   encontrados eram da página (CSS, colisão de nomes), não do runtime.
4. A troca **não entrega função nova** ao usuário.

## Caminhos possíveis

| Opção | Esforço | Observação |
| --- | --- | --- |
| **A. Manter** (recomendada agora) | nenhum | Resolver a licença e manter o arquivo fixado (SHA-256 em `THIRD_PARTY.md`). |
| B. Preact + `htm`, sem build | alto | Dois arquivos pequenos e abertos (~15 KB). A marcação vira *template literals*; a lógica quase não muda. |
| C. JavaScript puro, sem biblioteca | muito alto | Zero dependência, mas reimplementa a renderização e o controle de estado à mão. |

## Recomendação, em ordem

1. **Resolver a licença** do runtime com quem criou o artifact (a equipe do Design no Claude) ou
   confirmar os termos do tipo de artifact. É isso que decide se a troca é obrigatória.
2. **Criar testes de navegador** (por exemplo, Playwright com a API falsa dos testes): abrir cada
   tela, preencher e conferir o resultado. Vale mesmo sem trocar o runtime, porque protege qualquer
   mudança futura na página.
3. **Só então**, se a licença exigir ou o runtime der problema, migrar para a opção B, tela por tela,
   mantendo os testes de navegador verdes.

Se a licença for confirmada e não houver nenhum plano de distribuir fora da equipe, **não há razão para
trocar**.
