# ponta a ponta

Vazia por enquanto, e de propósito: um teste ponta a ponta do James precisa de
microfone, alto-falante, uma chave de API de verdade e uma janela Qt. Nada
disso existe numa CI, e simular tudo produziria um teste que passa sem provar
nada.

O que mora aqui, quando existir:

- o turno completo, do áudio à fala, com provedor de mentira mas com o
  orquestrador de verdade;
- a partida dos dois processos (`wake_listener` e `orchestrator`) e o IPC entre
  eles;
- o modo navegador ligando, abrindo uma página local e desligando.

Todo arquivo aqui carrega `@pytest.mark.e2e` — a CI unitária os ignora.
