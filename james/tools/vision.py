"""Visão: analisar a tela e a câmera.

Estas são as ferramentas mais sensíveis do catálogo, e por isso nascem no
Nível 2 (confirmação obrigatória):

  - A tela pode ter senha, extrato bancário, conversa privada. Capturá-la e
    mandar para a nuvem é exatamente o que tornou o Recall da Microsoft tão
    criticado. O James pergunta antes, toda vez.
  - A câmera é a sua imagem. Uma foto tirada sem aviso não tem desfazer.

Os dois são configuráveis em `permissions.vision_requires_confirmation`, mas o
padrão é pedir. Se você baixar para Nível 1, saiba exatamente o que está
trocando por conveniência.

Consequência real de manter o padrão: sem whisper.cpp instalado não há
confirmação por voz, e portanto não há análise de tela nem de câmera. É o
comportamento correto — sem poder confirmar, a resposta é não.

Nada fica salvo em disco: a imagem é capturada, enviada, e o que sobra é a
descrição em texto.
"""

from __future__ import annotations

from james.logs import audit, audit_text, get_logger
from james.tools.registry import Tool, ToolRegistry, ToolResult

logger = get_logger("james.tools.vision")

_DEFAULT_SCREEN_QUESTION = (
    "Descreva o que está visível nesta tela de computador, de forma útil e "
    "objetiva. Diga qual programa parece estar em uso e o que o conteúdo mostra."
)
_DEFAULT_CAMERA_QUESTION = (
    "Descreva o que aparece nesta imagem de webcam, de forma objetiva."
)
_CAMERA_WARMUP_FRAMES = 5


def capture_camera_jpeg(device_index: int = 0, warmup: int = _CAMERA_WARMUP_FRAMES) -> bytes:
    """Tira uma única foto e libera a câmera imediatamente.

    Os primeiros quadros saem escuros porque o sensor ainda está ajustando
    exposição — daí o descarte inicial. A câmera é liberada no `finally` para
    que a luz do hardware apague mesmo se algo falhar no meio.
    """
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError(
            "Análise de câmera precisa do OpenCV: pip install opencv-python"
        ) from exc

    capture = cv2.VideoCapture(device_index)
    try:
        if not capture.isOpened():
            raise RuntimeError("Não consegui abrir a câmera. Ela está em uso?")

        frame = None
        for _ in range(max(1, warmup)):
            ok, current = capture.read()
            if ok:
                frame = current
        if frame is None:
            raise RuntimeError("A câmera não devolveu imagem.")

        ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            raise RuntimeError("Não consegui codificar a imagem da câmera.")
        return bytes(buffer)
    finally:
        capture.release()


def register(registry: ToolRegistry, config, guard) -> None:
    def ver_tela(args: dict) -> ToolResult:
        pergunta = str(args.get("pergunta", "")).strip() or _DEFAULT_SCREEN_QUESTION

        grabber = registry.screen_grabber
        if grabber is None:
            return ToolResult.failure(
                "A captura de tela não está disponível nesta execução."
            )
        client = registry.llm
        if client is None:
            return ToolResult.failure("Não tenho como analisar imagens agora.")

        try:
            imagem = grabber.grab_png()
        except RuntimeError as exc:
            logger.error("Captura de tela falhou: %s", exc)
            return ToolResult.failure("Não consegui capturar a tela.")

        # A pergunta descreve o que está na tela sem precisar da imagem:
        # "o que diz naquele e-mail do banco?" já conta demais.
        audit(
            "ver_tela",
            tamanho_kb=len(imagem) // 1024,
            **audit_text(pergunta, campo="pergunta"),
        )
        try:
            descricao = client.describe_image(imagem, "image/png", pergunta)
        except Exception as exc:  # noqa: BLE001 — falha de visão não derruba o turno
            logger.error("Análise da tela falhou: %s", exc)
            return ToolResult.failure("Não consegui analisar a tela agora.")

        if not descricao:
            return ToolResult.failure("Não consegui interpretar o que está na tela.")

        # Resultado imprevisível: precisa do segundo ciclo para o modelo
        # transformar a descrição numa resposta ao que foi perguntado. E o
        # conteúdo é externo, então passa pelo sanitizador antes do histórico.
        return ToolResult(ok=True, data=descricao, external_content=True)

    def ver_camera(args: dict) -> ToolResult:
        pergunta = str(args.get("pergunta", "")).strip() or _DEFAULT_CAMERA_QUESTION

        client = registry.llm
        if client is None:
            return ToolResult.failure("Não tenho como analisar imagens agora.")

        try:
            imagem = capture_camera_jpeg(
                device_index=int(config.get("camera.device_index", 0))
            )
        except RuntimeError as exc:
            logger.error("Captura de câmera falhou: %s", exc)
            return ToolResult.failure(str(exc))

        audit(
            "ver_camera",
            tamanho_kb=len(imagem) // 1024,
            **audit_text(pergunta, campo="pergunta"),
        )
        try:
            descricao = client.describe_image(imagem, "image/jpeg", pergunta)
        except Exception as exc:  # noqa: BLE001
            logger.error("Análise da câmera falhou: %s", exc)
            return ToolResult.failure("Não consegui analisar a imagem da câmera.")

        if not descricao:
            return ToolResult.failure("Não consegui interpretar a imagem.")
        return ToolResult(ok=True, data=descricao, external_content=True)

    registry.register(
        Tool(
            name="ver_tela",
            description=(
                "Captura a tela do computador e analisa o conteúdo. Use quando o "
                "usuário perguntar sobre o que está na tela dele."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pergunta": {
                        "type": "string",
                        "description": "O que observar na tela, se houver algo específico.",
                    }
                },
            },
            handler=ver_tela,
            # Resultado imprevisível: aqui o segundo ciclo com o modelo agrega
            # de verdade, ao contrário de "abrir o Chrome".
            fire_and_forget=False,
        )
    )
    registry.register(
        Tool(
            name="ver_camera",
            description=(
                "Tira uma foto pela webcam e analisa a imagem. Use quando o usuário "
                "pedir para você olhar algo pela câmera."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pergunta": {
                        "type": "string",
                        "description": "O que observar na imagem, se houver algo específico.",
                    }
                },
            },
            handler=ver_camera,
            fire_and_forget=False,
        )
    )
