---
name: planilhas
description: Como estruturar uma planilha que a pessoa consegue usar depois — colunas, tipos, ordenação e quando vale um gráfico.
---

# Montando uma planilha útil

Uma planilha boa não é uma tabela bonita: é uma que a pessoa consegue filtrar,
somar e estender sem refazer.

## Colunas

- Uma coluna = uma informação. Nunca "Nome e cargo" numa coluna só; separe.
- Nomes curtos e sem ambiguidade: "Valor (R$)" é melhor que "Valor", que é
  melhor que "Vl".
- A primeira coluna é o identificador da linha — o nome, a data, a categoria.
  É por ela que a pessoa vai procurar, e é ela que vira rótulo do gráfico.
- Datas em colunas próprias, não misturadas no texto. Se houver mês e ano,
  considere uma coluna para cada: permite agrupar.

## Valores

- Número é número, não texto. "1.500,00" precisa chegar como `1500`, senão a
  planilha não soma nem plota.
- Percentual: escolha um formato e mantenha em toda a coluna. Misturar `15%` e
  `0,15` na mesma coluna quebra qualquer cálculo.
- Célula vazia é melhor que "N/A" ou "-": o texto contamina a coluna e impede
  a soma.
- Não repita a unidade em cada célula. Ela vai no cabeçalho.

## Ordenação

- Ordene pelo que a pessoa vai querer ver primeiro, não pela ordem em que os
  dados apareceram.
- Séries temporais: do mais antigo para o mais novo, sempre. É o que o gráfico
  de linha espera.
- Rankings: do maior para o menor.

## Quando vale um gráfico

- **Barra**: comparar categorias entre si. Poucas categorias, valores
  parecidos em ordem de grandeza.
- **Linha**: evolução ao longo do tempo. Só faz sentido se a primeira coluna
  for temporal e estiver ordenada.
- **Pizza**: partes de um todo, e só quando as partes somam 100% e são no
  máximo cinco. Com mais que isso ninguém distingue as fatias.
- **Nenhum**: se são poucos números, a tabela já comunica. Um gráfico de duas
  barras é ruído.

## Erros comuns

- Linha de total no meio dos dados: ela é somada de novo quando a pessoa
  seleciona a coluna. Deixe fora ou bem separada.
- Cabeçalho em duas linhas: quebra filtro e ordenação.
- Células mescladas: quebra praticamente tudo. Evite.
