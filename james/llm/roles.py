"""Papéis de modelo — quem faz o quê.

Divisão de trabalho do projeto:

    PERCEPTION  ouvir e transcrever            -> Gemini (multimodal, áudio)
    VISION      entender imagem (tela, câmera) -> Gemini (multimodal, imagem)
    REASONING   decidir, planejar, escrever,   -> OpenRouter (modelos de texto
                chamar ferramentas                fortes, cota independente)

Por que separar em vez de um modelo só fazer tudo:

1. São cotas independentes. O Gemini tem a dele; o OpenRouter tem a dele. Um
   turno que gasta os dois consome metade de cada, em vez de dobrar o consumo
   de um provedor só. Na prática, dobra o uso diário possível.

2. Cada um no que é melhor. O Gemini aceita áudio e imagem direto, o que os
   modelos de texto do OpenRouter não fazem. Já para raciocínio em várias
   etapas e escrita longa, o catálogo do OpenRouter tem modelos bem maiores
   que o Flash.

3. O roteador local entra no meio de graça. Como a percepção já devolve a
   transcrição em texto, comandos frequentes podem ser resolvidos localmente
   ANTES de gastar a requisição cara de raciocínio.

Cada papel tem uma cadeia de provedores, não um provedor fixo: se o preferido
falhar ou estourar cota, o seguinte assume. É o que mantém o James de pé
quando um dos dois lados cai.
"""

from __future__ import annotations

from enum import Enum


class Role(Enum):
    PERCEPTION = "perception"
    REASONING = "reasoning"
    VISION = "vision"

    @property
    def needs_audio(self) -> bool:
        return self is Role.PERCEPTION

    @property
    def needs_image(self) -> bool:
        return self is Role.VISION


# Cadeias usadas quando o config.yaml não define nada. A ordem é a preferência.
DEFAULT_CHAINS: dict[Role, tuple[str, ...]] = {
    Role.PERCEPTION: ("gemini",),
    Role.REASONING: ("openrouter", "gemini"),
    Role.VISION: ("gemini",),
}

# Instrução da etapa de percepção. Curta e literal de propósito: aqui o modelo
# não deve interpretar, opinar nem responder — só transcrever. Qualquer coisa
# além disso vira ruído para a etapa de raciocínio.
TRANSCRIPTION_INSTRUCTION = (
    "Transcreva exatamente o que foi dito no áudio, em português do Brasil. "
    "Responda APENAS com a transcrição literal, sem aspas, sem comentários, "
    "sem responder ao que foi dito. Se o áudio não contiver fala inteligível, "
    "responda exatamente: (sem fala)"
)

# Marcador devolvido pela percepção quando não há fala utilizável.
NO_SPEECH_MARKER = "(sem fala)"
