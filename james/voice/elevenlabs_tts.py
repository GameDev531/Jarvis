"""Voz na nuvem — ElevenLabs.

A divisão de trabalho que torna isto viável no plano grátis:

    OpenRouter pensa, decide e escreve      ->  cota de REQUISIÇÕES
    ElevenLabs apenas lê em voz alta        ->  cota de CARACTERES
    Gemini ouve, busca e enxerga imagem     ->  cota de REQUISIÇÕES

O ponto é que a ElevenLabs nunca vê a conversa. Ela recebe a frase final e mais
nada — raciocínio, histórico, resultado de busca e descrição de imagem ficam
fora da cota de voz. É por isso que 10.000 caracteres por mês chegam a render:
o que é cobrado é só o que sai pelo alto-falante.

## Duas escolhas que dobram o que a cota rende

**`eleven_flash_v2_5`.** Custa metade dos créditos por caractere, e ainda é o
modelo de menor latência (~75 ms). Numa máquina modesta com internet lenta, os
dois lados importam. A qualidade é um pouco abaixo do multilingual v2, e essa
é a troca — consciente.

**`pcm_16000`.** A saída padrão da API é MP3, que exigiria um decodificador
(mais uma dependência, mais CPU, mais latência) só para virar PCM de novo. Em
`pcm_16000` o áudio já chega no formato exato do resto do pipeline: 16 kHz,
mono, 16 bits. É o mesmo formato do microfone, do VAD e do reprodutor.

O 44.1 kHz exigiria plano Pro. O de 16 kHz não — e é o que queremos de
qualquer forma.

## O que este arquivo NÃO faz

Não decide se pode falar. Quem controla o orçamento é `budget.py`, e quem
escolhe entre nuvem e local é `chain.py`. Aqui só se converte texto em som.
"""

from __future__ import annotations

import threading

from james.logs import get_logger
from james.voice.tts import TTSUnavailable

logger = get_logger("james.voice.elevenlabs")

_BASE = "https://api.elevenlabs.io/v1"

# Formato que casa com o pipeline inteiro: 16 kHz mono 16-bit, sem conversão.
SAMPLE_RATE = 16000
_OUTPUT_FORMAT = "pcm_16000"

# Metade dos créditos por caractere e a menor latência do catálogo.
MODELO_PADRAO = "eleven_flash_v2_5"

# Voz padrão do catálogo público da ElevenLabs. Multilíngue, funciona em pt-BR.
VOZ_PADRAO = "JBFqnCBsd6RMkjVDRZzb"

# Texto maior que isto não é uma fala, é um despejo — e custaria a cota do mês
# numa tacada. O Speaker já divide por sentença; isto é a rede de segurança.
MAX_CARACTERES = 800


class ElevenLabsTTS:
    """Sintetiza texto em PCM 16-bit mono, 16 kHz. Mesmo contrato do Piper."""

    nome = "elevenlabs"

    def __init__(
        self,
        api_key: str,
        voice_id: str = VOZ_PADRAO,
        model_id: str = MODELO_PADRAO,
        timeout_s: float = 30.0,
        estabilidade: float = 0.5,
        similaridade: float = 0.75,
        velocidade: float = 1.0,
    ) -> None:
        if not api_key:
            raise TTSUnavailable("ELEVENLABS_API_KEY ausente.")
        try:
            import httpx  # noqa: F401
        except ImportError as exc:
            raise TTSUnavailable("Voz na nuvem precisa do httpx.") from exc

        self.api_key = api_key
        self.voice_id = str(voice_id or VOZ_PADRAO)
        self.model_id = str(model_id or MODELO_PADRAO)
        self.timeout_s = float(timeout_s)
        self.sample_rate = SAMPLE_RATE
        self.voice_settings = {
            "stability": float(estabilidade),
            "similarity_boost": float(similaridade),
            "speed": float(velocidade),
        }
        self._lock = threading.Lock()
        self._cliente = None

    @property
    def backend(self) -> str:
        return f"elevenlabs/{self.model_id}"

    def _http(self):
        import httpx

        if self._cliente is None:
            self._cliente = httpx.Client(timeout=self.timeout_s)
        return self._cliente

    # ---------------------------------------------------------------- síntese

    def synthesize(self, text: str) -> bytes:
        """PCM 16-bit mono a 16 kHz. Levanta TTSUnavailable em falha."""
        import httpx

        limpo = " ".join(str(text or "").split())
        if not limpo:
            return b""
        if len(limpo) > MAX_CARACTERES:
            logger.warning(
                "Texto de %d caracteres cortado em %d antes de ir para a nuvem.",
                len(limpo), MAX_CARACTERES,
            )
            limpo = limpo[:MAX_CARACTERES].rstrip()

        with self._lock:
            try:
                resposta = self._http().post(
                    f"{_BASE}/text-to-speech/{self.voice_id}",
                    params={"output_format": _OUTPUT_FORMAT},
                    headers={
                        "xi-api-key": self.api_key,
                        "accept": "audio/pcm",
                        "content-type": "application/json",
                    },
                    json={
                        "text": limpo,
                        "model_id": self.model_id,
                        "voice_settings": self.voice_settings,
                    },
                )
            except httpx.HTTPError as exc:
                raise TTSUnavailable(f"Falha de rede na voz: {exc}") from exc

        if resposta.status_code == 401:
            raise TTSUnavailable("Chave da ElevenLabs recusada.")
        if resposta.status_code == 429:
            # Aqui é limite de concorrência ou cota do lado deles. Nos dois
            # casos a resposta certa é a mesma: cair para a voz local.
            raise TTSUnavailable("Cota ou limite de concorrência da ElevenLabs.")
        if resposta.status_code >= 400:
            detalhe = resposta.text[:200] if resposta.text else ""
            raise TTSUnavailable(f"ElevenLabs devolveu {resposta.status_code}. {detalhe}")

        audio = resposta.content or b""
        if len(audio) < 2:
            raise TTSUnavailable("ElevenLabs devolveu áudio vazio.")
        # PCM 16-bit tem número par de bytes. Ímpar significa resposta truncada,
        # e meia amostra vira um estalo alto no alto-falante.
        if len(audio) % 2:
            audio = audio[:-1]
        return audio

    def prewarm(self) -> float:
        """Não faz nada de propósito.

        O Piper aquece porque carregar o modelo custa segundos na primeira
        frase. Aqui não há modelo local para carregar — e sintetizar algo só
        para aquecer gastaria caracteres do orçamento à toa.
        """
        return 0.0

    def close(self) -> None:
        with self._lock:
            if self._cliente is not None:
                self._cliente.close()
                self._cliente = None
