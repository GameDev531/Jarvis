"""Voz na nuvem — LMNT. O plano C da cadeia.

A cadeia de voz tinha dois degraus, e o segundo era um penhasco:

    elevenlabs  ->  10.000 caracteres/mês, e acabou
    piper       ->  local, ilimitado, mas soa bem pior

No dia 20 do mês a cota da ElevenLabs acaba e a voz cai de uma vez. A LMNT
entra no meio: mais 15.000 caracteres grátis por mês, de outra conta, com
outra contagem. A queda vira um degrau.

    elevenlabs  ->  lmnt  ->  piper

## Por que a API, e não o site

O site tem login, e automatizar login de site é frágil e proibido pela maioria
dos termos de uso. Não precisa: a LMNT tem API pública com chave própria
(`X-API-Key`), retirada da página da conta. É a mesma coisa que já fazemos com
a ElevenLabs.

## `format: raw` a 16 kHz

Mesma decisão do arquivo da ElevenLabs, pelo mesmo motivo. O padrão da API é
MP3, que exigiria um decodificador — mais uma dependência, mais CPU, mais
latência — só para virar PCM de novo. Em `raw` a 16 kHz o áudio já chega no
formato do resto do pipeline: 16 kHz, mono, 16 bits, igual ao microfone.

## O problema de verdade: a voz não pode trocar de dono

Trocar de motor com vozes diferentes faz o James virar outra pessoa no meio da
conversa. É pior que ficar sem cota — soa como defeito, não como economia.

A solução é usar a MESMA amostra de voz nos dois motores de nuvem. ElevenLabs
e LMNT clonam; alimente as duas com o mesmo arquivo e a troca fica quase
imperceptível. `clonar_voz()` abaixo faz a parte da LMNT.

O Piper (plano D, local) não clona — ali a troca é audível de qualquer jeito, e
é o preço de continuar falando de graça e sem internet.

Sobre a amostra: use uma voz que você tenha o direito de usar. Clonar a voz de
uma pessoa real sem consentimento é vetado pelos termos da LMNT, e o resultado
prático de ser pego é a conta suspensa. É uso local, e a escolha é de quem
roda — mas o risco é esse, e é bom saber dele antes.

## O endpoint é configurável de propósito

A LMNT já mudou o caminho da API entre versões do SDK, e não deu para
confirmar o caminho atual daqui (o domínio da documentação está bloqueado
neste ambiente). O padrão abaixo é o que a documentação pública descreve; se
mudar, `voz.lmnt.endpoint` no config.yaml resolve sem tocar em código.
"""

from __future__ import annotations

import threading

from james.logs import get_logger
from james.voice.tts import TTSUnavailable

logger = get_logger("james.voice.lmnt")

ENDPOINT_PADRAO = "https://api.lmnt.com/v1/speech"
ENDPOINT_VOZES = "https://api.lmnt.com/v1/ai/voice"

# Casa com o pipeline inteiro: 16 kHz mono 16-bit, sem conversão.
SAMPLE_RATE = 16000
_FORMATO = "raw"

# Voz do catálogo público da LMNT. Troque em `voz.lmnt.voz` no config.yaml.
VOZ_PADRAO = "leah"

MODELO_PADRAO = "blizzard"

# Plano grátis da LMNT. Independente da cota da ElevenLabs — é outra conta.
CARACTERES_GRATIS_POR_MES_LMNT = 15_000

# A API aceita 5.000 por requisição, mas isso não é uma fala — é um despejo que
# comeria um terço da cota do mês numa tacada. O Speaker já divide por sentença.
MAX_CARACTERES = 800


class LmntTTS:
    """Sintetiza texto em PCM 16-bit mono, 16 kHz. Mesmo contrato do Piper."""

    nome = "lmnt"

    def __init__(
        self,
        api_key: str,
        voice_id: str = VOZ_PADRAO,
        model_id: str = MODELO_PADRAO,
        endpoint: str = ENDPOINT_PADRAO,
        timeout_s: float = 30.0,
        idioma: str = "pt",
    ) -> None:
        if not api_key:
            raise TTSUnavailable("LMNT_API_KEY ausente.")
        try:
            import httpx  # noqa: F401
        except ImportError as exc:
            raise TTSUnavailable("Voz na nuvem precisa do httpx.") from exc

        self.api_key = api_key
        self.voice_id = str(voice_id or VOZ_PADRAO)
        self.model_id = str(model_id or MODELO_PADRAO)
        self.endpoint = str(endpoint or ENDPOINT_PADRAO)
        self.timeout_s = float(timeout_s)
        self.idioma = str(idioma or "pt")
        self.sample_rate = SAMPLE_RATE
        self._lock = threading.Lock()
        self._cliente = None

    @property
    def backend(self) -> str:
        return f"lmnt/{self.model_id}"

    def prewarm(self) -> float:
        """Zero de propósito: aquecer gastaria cota.

        Mesma decisão da ElevenLabs. Uma frase de aquecimento aqui custa
        caracteres reais, e a cota do mês é justamente o recurso escasso.
        """
        return 0.0

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
                    self.endpoint,
                    headers={
                        "X-API-Key": self.api_key,
                        "content-type": "application/json",
                    },
                    json={
                        "text": limpo,
                        "voice": self.voice_id,
                        "model": self.model_id,
                        "format": _FORMATO,
                        "sample_rate": SAMPLE_RATE,
                        "language": self.idioma,
                    },
                )
            except httpx.HTTPError as exc:
                raise TTSUnavailable(f"Falha de rede na voz: {exc}") from exc

        if resposta.status_code in (401, 403):
            raise TTSUnavailable("Chave da LMNT recusada.")
        if resposta.status_code == 429:
            raise TTSUnavailable("Cota ou limite de concorrência da LMNT.")
        if resposta.status_code == 404:
            # Erro caro de diagnosticar sem esta frase: o caminho da API mudou
            # entre versões do SDK deles, e um 404 aqui parece "voz não existe".
            raise TTSUnavailable(
                f"LMNT devolveu 404 em {self.endpoint}. O caminho da API pode ter "
                "mudado — ajuste `voz.lmnt.endpoint` no config.yaml."
            )
        if resposta.status_code >= 400:
            detalhe = resposta.text[:200] if resposta.text else ""
            raise TTSUnavailable(f"LMNT devolveu {resposta.status_code}. {detalhe}")

        audio = resposta.content or b""
        if len(audio) < 2:
            raise TTSUnavailable("LMNT devolveu áudio vazio.")
        # PCM 16-bit tem número par de bytes. Ímpar significa resposta truncada,
        # e meia amostra vira um estalo alto no alto-falante.
        if len(audio) % 2:
            audio = audio[:-1]
        return audio


# ------------------------------------------------------------------ clonagem


def clonar_voz(
    api_key: str,
    amostra,   # caminho (str/Path) ou os bytes do áudio
    nome: str = "james",
    endpoint: str = ENDPOINT_VOZES,
    timeout_s: float = 120.0,
) -> str:
    """Cria uma voz a partir de uma amostra e devolve o `voice id`.

    Roda UMA VEZ, fora do caminho de voz — o id resultante vai para
    `voz.lmnt.voz` no config.yaml e nunca mais se toca nisto.

    Por que existe: sem uma voz igual nos dois motores de nuvem, o James troca
    de timbre quando a cota da ElevenLabs acaba. Alimentar os dois com a mesma
    amostra é o que torna a queda inaudível.

    `amostra` é o caminho de um arquivo de áudio ou os bytes dele. Use material
    que você tenha o direito de usar: clonar a voz de outra pessoa sem
    consentimento viola os termos da LMNT, e o custo prático é a conta suspensa.

    O upload demora — daí o tempo limite generoso.
    """
    import httpx

    if not api_key:
        raise TTSUnavailable("LMNT_API_KEY ausente.")

    # `bytes` é o conteúdo; qualquer outra coisa é caminho (str ou Path).
    if isinstance(amostra, (bytes, bytearray)):
        dados = bytes(amostra)
        arquivo = "amostra.mp3"
    else:
        from pathlib import Path

        caminho = Path(amostra)
        if not caminho.exists():
            raise TTSUnavailable(f"Amostra não encontrada: {caminho}")
        dados = caminho.read_bytes()
        arquivo = caminho.name

    if len(dados) < 1024:
        raise TTSUnavailable("Amostra vazia ou pequena demais para clonar.")

    try:
        resposta = httpx.post(
            endpoint,
            headers={"X-API-Key": api_key},
            data={"name": str(nome), "type": "instant"},
            files={"files": (arquivo, dados)},
            timeout=timeout_s,
        )
    except httpx.HTTPError as exc:
        raise TTSUnavailable(f"Falha de rede ao clonar: {exc}") from exc

    if resposta.status_code in (401, 403):
        raise TTSUnavailable("Chave da LMNT recusada.")
    if resposta.status_code >= 400:
        raise TTSUnavailable(
            f"LMNT devolveu {resposta.status_code} ao clonar: {resposta.text[:200]}"
        )

    try:
        corpo = resposta.json()
    except ValueError as exc:
        raise TTSUnavailable("Resposta da LMNT não é JSON.") from exc

    # O formato da resposta varia entre versões da API deles; procurar em vez
    # de assumir evita quebrar por uma chave renomeada.
    for chave in ("id", "voice_id", "voice"):
        valor = corpo.get(chave) if isinstance(corpo, dict) else None
        if isinstance(valor, str) and valor:
            return valor
        if isinstance(valor, dict) and isinstance(valor.get("id"), str):
            return valor["id"]
    raise TTSUnavailable(f"Não achei o id da voz na resposta: {str(corpo)[:200]}")
