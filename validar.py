"""
Valida todos os JSONs de dados/ contra os schemas de app/models.py.

Uso:
    python validar.py

Sai com codigo 0 se tudo estiver valido, 1 se houver qualquer problema.
Esse codigo de saida e o que permite usar o mesmo script no CI depois.
"""

import json
import sys
from pathlib import Path

from pydantic import ValidationError

from app.models import BoardMembro, Equipe, Projeto, Stack
from app.repositorio import Repositorio

repo = Repositorio(Path("."))

RAIZ = Path(__file__).parent
DADOS = RAIZ / "dados"


def carregar(caminho: Path, modelo):
    """Le um JSON e valida contra o modelo. Devolve (objeto, erro_formatado)."""
    if not caminho.exists():
        return None, "arquivo nao encontrado"

    try:
        with caminho.open(encoding="utf-8") as f:
            bruto = json.load(f)
    except json.JSONDecodeError as e:
        return None, f"JSON malformado na linha {e.lineno}, coluna {e.colno}: {e.msg}"

    try:
        return modelo.model_validate(bruto), None
    except ValidationError as e:
        linhas = []
        for erro in e.errors():
            campo = " -> ".join(str(p) for p in erro["loc"]) or "(raiz)"
            linhas.append(f"      campo '{campo}': {erro['msg']}")
        return None, "falha de validacao:\n" + "\n".join(linhas)


def main() -> int:
    print("Integridade:", repo.verificar_integridade())
    print("Proximo ID:", repo.proximo_id())
    print("Boards faltando:", repo.arquivos_do_board_faltando())

    problemas = 0

    alvos = [
        (DADOS / "equipe.json", Equipe),
        (DADOS / "projeto.json", Projeto),
        (DADOS / "stack.json", Stack),
    ]
    alvos += [(p, BoardMembro) for p in sorted((DADOS / "board").glob("*.json"))]

    for caminho, modelo in alvos:
        relativo = caminho.relative_to(RAIZ)
        obj, erro = carregar(caminho, modelo)
        if erro:
            print(f"  [ERRO] {relativo}\n{erro}")
            problemas += 1
        else:
            extra = ""
            if isinstance(obj, BoardMembro):
                extra = f" ({len(obj.tarefas)} tarefa(s))"
            elif isinstance(obj, Equipe):
                extra = f" ({len(obj.membros)} membro(s))"
            print(f"  [ok]   {relativo}{extra}")

    print()
    if problemas:
        print(f"{problemas} arquivo(s) com problema.")
        return 1

    print("Todos os arquivos validos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())