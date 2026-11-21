---
title: artigo2md
subtitle: Seus PDFs viram texto limpo, sem sair do seu computador
project: artigo2md
lang: pt-BR
generated: 2026-11-20
vocabulary: PDF, Markdown
---

<!-- slide: cover -->
# artigo2md
## Seus PDFs viram texto limpo, sem sair do seu computador
Artigos científicos, livros e diretrizes convertidos em texto estruturado, na máquina de quem usa e sem custo por página.
> Nada é enviado para a internet.
@source docs/briefing.md

<!-- slide: manifesto -->
## Por que este projeto existe
Quem converte artigos científicos conhece o problema: a conversão termina, mas a tabela que o texto cita não está lá. Um único conversor não acerta todo tipo de documento.

O artigo2md parte de uma convicção simples: cada PDF merece ser **examinado antes, encaminhado ao conversor mais adequado e conferido depois**. Se o resultado ficou ruim, o próximo conversor é tentado e o melhor resultado é o que fica.
> Nada é enviado para a internet e não há custo por página.
@source docs/briefing.md

<!-- slide: briefing -->
## O ponto de partida
### Visão
Um conversor de PDFs (artigos científicos, livros, diretrizes e capítulos) para Markdown, um formato de texto simples e estruturado, que roda no computador de quem usa.
### Usuários
Pesquisadores e estudantes, profissionais de saúde e desenvolvedores: gente que precisa ler, anotar, consultar ou preparar muitos documentos.
### Escopo
- Exame do documento antes da conversão
- Escolha automática do conversor, com nota de qualidade e nova tentativa
- Uso pelo navegador ou por comandos, sempre na própria máquina
### Restrições
Processamento sempre local, componentes obrigatórios de licença aberta e permissiva, projeto comunitário sem prazo fixo e sem orçamento.
### Prioridades
Qualidade e robustez em primeiro lugar.
@source docs/briefing.md

<!-- slide: constitution -->
## No que acreditamos
### I. Processamento 100% Local
Nenhum documento de quem usa é enviado a serviços externos.
### II. Dependências Obrigatórias com Licença Permissiva
O projeto segue livre para qualquer uso: nada obrigatório carrega licença restritiva.
### III. Mensagens ao Usuário em Português
Quem usa é atendido na própria língua.
### IV. Testes Antes do Commit
O commit é o registro oficial de uma mudança no projeto: nenhuma é registrada sem que a verificação automática passe.
### V. Veracidade de Dados, Zero Fabricação (NON-NEGOTIABLE)
Nenhum documento do projeto contém dado inventado. Sem fonte rastreável, o trabalho para e uma pessoa decide.
@metric Princípios | principles
@metric Versão | constitution-version
@source docs/constitution.md

<!-- slide: chapter -->
## Em construção
O produto já existia e funcionava. Agora cada peça foi documentada a partir do que ele realmente faz, passou por perguntas e decisões e, onde havia lacuna, ganhou um plano.

<!-- slide: spec key=command-line-interface -->
## Converter pastas inteiras com um comando
Quem tem muitos documentos não precisa abrir um por um: aponta a pasta e recebe tudo convertido.
### O que é
Um comando único para converter arquivos ou pastas inteiras, abrir a página de uso no navegador e conferir se a instalação está saudável. Documentos já convertidos são pulados, o que permite repetir a operação sem retrabalho.
### Como foi pensada
O ponto frágil estava nos documentos de mesmo nome guardados em pastas diferentes: um deles ficava de fora. A escolha foi fazer a saída repetir a organização de pastas da entrada, um resultado que se mantém estável a cada nova execução.
### Como foi enriquecida
As perguntas de refinamento definiram a prioridade, confirmaram que pular um documento já convertido não é erro e fixaram a meta: cada comportamento com a sua verificação automática.
### Como foi implementada
A capacidade já existe no produto. A melhoria para documentos de mesmo nome e as verificações automáticas estão planejadas e em andamento, ainda sem tarefas concluídas.
@metric Tarefas entregues | tasks
@metric Dúvidas esclarecidas | clarify-questions
@tag Linha de comando
@source docs/specs/command-line-interface/spec.md
@source docs/specs/command-line-interface/research.md

<!-- slide: spec key=conversion-engines -->
## Vários conversores, um só compromisso
Cada conversor tem um talento, e todos trabalham sob a mesma regra: o documento não sai da máquina.
### O que é
O produto reúne conversores com talentos diferentes: um principal para páginas em colunas, tabelas e páginas digitalizadas, um leve para texto corrido e opcionais para rapidez e para fórmulas. Ele sabe quais estão instalados e mostra a ficha de cada um.
### Como foi pensada
A garantia de privacidade estava declarada para apenas um dos conversores. A decisão foi estendê-la a todos: recursos remotos desligados de forma explícita e coleta de dados de uso desativada sempre que o conversor oferece essa opção.
### Como foi enriquecida
As perguntas transformaram a privacidade em exigência para todos os conversores e mantiveram a política de aceitar, do conversor opcional de fórmulas, apenas a geração que trabalha localmente.
### Como foi implementada
Os conversores já funcionam no produto. O reforço de privacidade e as verificações automáticas que faltam estão planejados e em andamento, ainda sem tarefas concluídas.
@metric Tarefas entregues | tasks
@metric Dúvidas esclarecidas | clarify-questions
@tag Docling
@tag MarkItDown
@tag MinerU
@source docs/specs/conversion-engines/spec.md
@source docs/specs/conversion-engines/research.md

<!-- slide: spec key=engine-routing -->
## O conversor certo para cada documento
O produto escolhe o melhor caminho para cada PDF e não se contenta com um resultado ruim.
### O que é
Depois de examinar o documento, o produto monta a ordem de conversores a tentar, dá uma nota de qualidade ao resultado e, se a nota não basta ou algo falha, tenta o próximo. Fica o melhor resultado.
### Como foi pensada
A nota cai quando o documento cita tabelas e nenhuma aparece no resultado, quando o texto sai curto demais ou quando boa parte do conteúdo original se perde. É a resposta direta ao problema que deu origem ao projeto.
### Como foi enriquecida
As perguntas ratificaram os critérios da nota como a definição de conversão aceitável. Também fixaram o que acontece quando alguém pede um conversor que não está instalado: o produto explica como instalar, sem trocar a escolha da pessoa.
### Como foi implementada
A capacidade já existe no produto e está especificada a partir do comportamento observado. O plano para completar as verificações automáticas ainda não foi feito.
@metric Dúvidas esclarecidas | clarify-questions
@metric Estágio | stage
@source docs/specs/engine-routing/spec.md

<!-- slide: spec key=pdf-inspection -->
## Entender o documento antes de converter
Antes de qualquer conversão, o produto lê o documento e descobre do que ele precisa.
### O que é
Um exame rápido que identifica páginas com texto, páginas digitalizadas que exigem reconhecimento de caracteres, citações de tabelas e sinais de fórmulas. Esse retrato orienta a escolha do conversor.
### Como foi pensada
Arquivos danificados ou protegidos por senha produziam um aviso técnico em inglês. A decisão foi interromper cedo, com uma mensagem clara em português, antes de acionar conversores pesados. O comportamento foi observado em execução real antes da escolha.
### Como foi enriquecida
As perguntas transformaram duas lacunas em garantias: a falha clara para arquivo inválido ou protegido e a exigência de que o exame aconteça sem nenhum acesso à rede.
### Como foi implementada
O exame já existe no produto. A mensagem clara para arquivos problemáticos e as verificações que faltam estão planejadas e em andamento, ainda sem tarefas concluídas.
@metric Tarefas entregues | tasks
@metric Dúvidas esclarecidas | clarify-questions
@source docs/specs/pdf-inspection/spec.md
@source docs/specs/pdf-inspection/research.md

<!-- slide: spec key=web-interface -->
## Arrastar, acompanhar e baixar
Converter documentos fica tão simples quanto usar uma página no navegador, sem que nada saia do computador.
### O que é
Uma página local onde a pessoa envia um ou mais PDFs, acompanha a fila de conversão, lê o resultado e baixa um arquivo ou todos de uma vez. O que não interessa mais pode ser apagado.
### Como foi pensada
As conversões acontecem uma por vez, porque os conversores são pesados. Cada pedido guarda o documento original, o andamento e o resultado, e um pedido interrompido por reinício fica sinalizado em vez de sumir.
### Como foi enriquecida
As perguntas fecharam decisões de uso: a página não pede senha e atende só o próprio computador por padrão, não há limite de envio e os arquivos ficam guardados até a pessoa apagar.
### Como foi implementada
A página já existe no produto e está especificada a partir do comportamento observado, com a estrutura dos dados documentada. O plano para completar as verificações automáticas ainda não foi feito.
@metric Dúvidas esclarecidas | clarify-questions
@metric Estágio | stage
@source docs/specs/web-interface/spec.md
@source docs/specs/web-interface/data-model.md

<!-- slide: timeline -->
## A jornada
O produto veio primeiro. A documentação de cada peça chegou depois, de uma vez, para sustentar os próximos passos.
@timeline

<!-- slide: numbers -->
## O projeto em números
@metric Capacidades documentadas | specs
@metric Tarefas entregues | tasks
@metric Dúvidas esclarecidas | clarify-questions
@metric Princípios | principles

<!-- slide: closing -->
## O que vem a seguir
O horizonte é crescer em capacidades: novos formatos, novos conversores e mais recursos de qualidade. Antes disso, o compromisso assumido é fechar as lacunas já mapeadas, para que cada comportamento do produto tenha a sua verificação automática.
> Qualidade e robustez em primeiro lugar.
@source docs/briefing.md

<!-- slide: sources -->
## Fontes
@sources
