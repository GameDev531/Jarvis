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


def _achar_amostra(partes):
    """Junta os pedaços de volta num caminho, se isso formar um arquivo real.

    O shell parte "Iron Man.mp3" em dois argumentos quando não há aspas.
    Testar o nome inteiro primeiro resolve o caso comum sem adivinhação: ou o
    arquivo existe com aquele nome, ou não existe e o erro é dito de forma
    útil.
    """
    inteiro = Path(" ".join(partes))
    if inteiro.exists():
        return inteiro
    # Um argumento só que não existe: devolver None dá a mensagem certa.
    if len(partes) == 1:
        return None
    # Vários pedaços e nenhum caminho válido — talvez o primeiro sozinho sirva.
    primeiro = Path(partes[0])
    return primeiro if primeiro.exists() else None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Clona uma voz na LMNT.")
    # `nargs="+"` porque nome de arquivo com espaço é a regra, não a exceção,
    # e no Windows a pessoa digita sem aspas. Sem isto, "Iron Man.mp3" chega
    # como dois argumentos e o argparse reclama de algo que não é o problema.
    parser.add_argument(
        "amostra", nargs="+", help="arquivo de áudio com a voz de referência"
    )
    parser.add_argument("--nome", default="james", help="nome da voz na sua conta")
    args = parser.parse_args(argv)

    caminho = _achar_amostra(args.amostra)
    if caminho is None:
        alvo = " ".join(args.amostra)
        print(f"Arquivo não encontrado: {alvo}", file=sys.stderr)
        print(
            "\nSe o nome tem espaços ou parênteses, ponha entre aspas simples:\n"
            f"    python clonar_voz.py '{alvo}'\n"
            "\nParêntese é sintaxe do PowerShell, então nesse caso as aspas não "
            "são opcionais.",
            file=sys.stderr,
        )
        return 2

    load_env()
    chave = get_secret("LMNT_API_KEY")
    if not chave:
        print(
            "LMNT_API_KEY ausente no .env. Pegue a chave na página da sua conta "
            "em https://app.lmnt.com",
            file=sys.stderr,
        )
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
