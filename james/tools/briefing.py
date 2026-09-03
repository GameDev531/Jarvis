"""Resumo do dia: hora, clima, estado da máquina e o que o James lembra.

A montagem do texto é determinística, de propósito. Um briefing é a mesma
estrutura todo dia — pedir ao modelo para redigi-lo custaria uma requisição por
uso sem melhorar nada de fato. O modelo entra antes, decidindo QUANDO chamar
esta ferramenta; o texto sai pronto daqui.

O clima vem do wttr.in, que é gratuito e não exige chave de API — mesma fonte
usada pelo Brahma-AI-Lite. Se ele não responder, o briefing continua sem a
parte de clima em vez de falhar inteiro.
"""

from __future__ import annotations

from james.logs import get_logger
from james.system_prompt import period_of_day
from james.tools.registry import Tool, ToolRegistry, ToolResult
from james.tools.system import _memory_gb, format_time_pt

logger = get_logger("james.tools.briefing")

_WEATHER_TIMEOUT_S = 8
_SAUDACOES = {
    "madrugada": "Boa madrugada",
    "manha": "Bom dia",
    "tarde": "Boa tarde",
    "noite": "Boa noite",
}


def fetch_weather(city: str) -> dict | None:
    """Clima atual pelo wttr.in. None quando não dá para obter."""
    if not city:
        return None
    try:
        import httpx
    except ImportError:
        logger.warning("httpx indisponível; briefing sai sem clima.")
        return None

    try:
        with httpx.Client(timeout=_WEATHER_TIMEOUT_S) as client:
            resposta = client.get(f"https://wttr.in/{city}", params={"format": "j1"})
            resposta.raise_for_status()
            dados = resposta.json()
    except Exception as exc:  # noqa: BLE001 — clima é acessório, nunca crítico
        logger.info("Não consegui obter o clima: %s", exc)
        return None

    try:
        atual = dados["current_condition"][0]
        hoje = dados["weather"][0]
        descricao = atual.get("lang_pt", [{}])[0].get("value") or atual["weatherDesc"][0]["value"]
        return {
            "descricao": str(descricao).strip().lower(),
            "temperatura": int(atual["temp_C"]),
            "sensacao": int(atual["FeelsLikeC"]),
            "minima": int(hoje["mintempC"]),
            "maxima": int(hoje["maxtempC"]),
            "chuva_pct": max(
                (int(h.get("chanceofrain", 0)) for h in hoje.get("hourly", [])), default=0
            ),
        }
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        logger.info("Resposta de clima em formato inesperado: %s", exc)
        return None


def register(registry: ToolRegistry, config, guard, memory=None) -> None:
    def briefing_do_dia(args: dict) -> ToolResult:
        from datetime import datetime

        agora = datetime.now()
        tratamento = str(config.get("persona.tratamento", "senhor"))
        periodo = period_of_day(agora.hour)

        partes = [f"{_SAUDACOES[periodo]}, {tratamento}."]
        dados: dict = {"hora": agora.strftime("%H:%M"), "data": agora.strftime("%d/%m/%Y")}

        partes.append(f"{format_time_pt(agora)}.")

        cidade = str(args.get("cidade") or config.get("briefing.cidade", "")).strip()
        clima = fetch_weather(cidade)
        if clima is not None:
            dados["clima"] = clima
            frase = (
                f"Em {cidade}, {clima['descricao']}, {clima['temperatura']} graus, "
                f"com mínima de {clima['minima']} e máxima de {clima['maxima']}"
            )
            if clima["chuva_pct"] >= 40:
                frase += f", e {clima['chuva_pct']} por cento de chance de chuva"
            partes.append(frase + ".")
        elif cidade:
            partes.append("Não consegui consultar o clima agora.")

        memoria = _memory_gb()
        if memoria is not None:
            total, livre = memoria
            dados["memoria_livre_gb"] = round(livre, 1)
            if livre < total * 0.15:
                # Só menciona a máquina quando há algo digno de nota — um
                # briefing que repete "está tudo normal" todo dia vira ruído.
                partes.append(
                    f"A memória está apertada: {livre:.1f} de {total:.1f} gigabytes livres."
                )

        if memory is not None:
            lembretes = memory.search("lembrete") + memory.search("hoje")
            vistos: list[str] = []
            for _, entrada in lembretes:
                if entrada not in vistos:
                    vistos.append(entrada)
            if vistos:
                dados["lembretes"] = vistos[:3]
                partes.append("Do que eu guardei: " + "; ".join(vistos[:3]) + ".")

        return ToolResult(ok=True, speech=" ".join(partes), data=dados)

    registry.register(
        Tool(
            name="briefing_do_dia",
            description=(
                "Monta o resumo do dia: saudação, hora, clima e o que for digno de "
                "nota na máquina. Use quando o usuário pedir um resumo, um briefing, "
                "ou perguntar como está o dia."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "cidade": {
                        "type": "string",
                        "description": "Cidade para o clima. Omita para usar a configurada.",
                        "audit_mode": "plaintext",
                    }
                },
            },
            handler=briefing_do_dia,
            fire_and_forget=True,
        )
    )
