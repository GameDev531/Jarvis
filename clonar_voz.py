#!/usr/bin/env python3
"""Cria uma voz na LMNT a partir de uma amostra de áudio. Roda uma vez só.

    python clonar_voz.py caminho/para/amostra.mp3
    python clonar_voz.py amostra.mp3 --nome jarvis

Existe por causa de um problema de consistência, não de economia: quando a
cota da ElevenLabs acaba, a LMNT assume — e com uma voz do catálogo dela o
James vira outra pessoa no meio da conversa. Soa como defeito.

Alimentar os dois motores com a MESMA amostra faz a troca ficar quase
imperceptível. Este script faz a parte da LMNT; o id que ele imprime vai para
`voz.lmnt.voz` no config.yaml.

Use material que você tenha o direito de usar. Clonar a voz de outra pessoa
sem consentimento viola os termos da LMNT — na prática, conta suspensa.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from james.config import get_secret, load_env  # noqa: E402
from james.voice.lmnt_tts import clonar_voz  # noqa: E402
from james.voice.tts import TTSUnavailable  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Clona uma voz na LMNT.")
    parser.add_argument("amostra", help="arquivo de áudio com a voz de referência")
    parser.add_argument("--nome", default="james", help="nome da voz na sua conta")
    args = parser.parse_args(argv)

    load_env()
    chave = get_secret("LMNT_API_KEY")
    if not chave:
        print(
            "LMNT_API_KEY ausente no .env. Pegue a chave na página da sua conta "
            "em https://app.lmnt.com",
            file=sys.stderr,
        )
        return 2

    caminho = Path(args.amostra)
    if not caminho.exists():
        print(f"Arquivo não encontrado: {caminho}", file=sys.stderr)
        return 2

    print(f"Enviando {caminho.name} ({caminho.stat().st_size // 1024} KB)…")
    try:
        voice_id = clonar_voz(chave, caminho, nome=args.nome)
    except TTSUnavailable as exc:
        print(f"Não deu: {exc}", file=sys.stderr)
        return 1

    print(f"\nVoz criada: {voice_id}\n")
    print("Coloque no config.yaml:\n")
    print("voz:")
    print("  lmnt:")
    print(f"    voz: {voice_id}")
    print(
        "\nPara a troca ficar inaudível, clone a MESMA amostra na ElevenLabs "
        "também e aponte `voz.elevenlabs.voice_id` para ela."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
