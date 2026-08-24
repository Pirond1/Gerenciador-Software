"""
Camada de acesso a disco.

Regra inegociavel do projeto: nenhum json.dump acontece fora deste arquivo.
E o que garante que todo JSON gravado tenha sempre a mesma formatacao e a
mesma ordenacao -- sem isso, o Git enxerga arquivo inteiro modificado e
todo merge vira conflito.

Nao ha cache: os arquivos mudam por fora (git pull, edicao manual), entao
ler do disco a cada chamada e o comportamento correto, nao um desperdicio.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ValidationError

from app.models import BoardMembro, Equipe, ItemStack, Projeto, Stack, Tarefa

NAO_ATRIBUIDAS = "nao_atribuidas"


class ErroRepositorio(Exception):
    """Falha ao ler ou gravar dados."""


class Repositorio:
    def __init__(self, raiz: Path):
        self.raiz = Path(raiz)
        self.dados = self.raiz / "dados"
        self.board_dir = self.dados / "board"

    # -----------------------------------------------------------------
    # Primitivas de disco -- o unico ponto do sistema que toca arquivo
    # -----------------------------------------------------------------

    def _ler(self, caminho: Path, modelo: type[BaseModel]) -> BaseModel:
        if not caminho.exists():
            raise ErroRepositorio(f"arquivo nao encontrado: {caminho}")

        try:
            with caminho.open(encoding="utf-8") as f:
                bruto = json.load(f)
        except json.JSONDecodeError as e:
            raise ErroRepositorio(
                f"{caminho.name}: JSON malformado (linha {e.lineno}, coluna {e.colno})"
            ) from e

        try:
            return modelo.model_validate(bruto)
        except ValidationError as e:
            raise ErroRepositorio(f"{caminho.name}: {e}") from e

    def _gravar(self, caminho: Path, modelo: BaseModel) -> None:
        """Escrita atomica e canonica.

        Atomica: grava em .tmp e so entao substitui. Se o processo morrer no
        meio, o arquivo original continua intacto -- em vez de virar um JSON
        pela metade que voce commitaria sem perceber.

        Canonica: mesma indentacao, acentos literais e quebra de linha LF
        sempre, independentemente de quem gravou ou de qual sistema.
        """
        texto = json.dumps(
            modelo.model_dump(mode="json"),
            ensure_ascii=False,  # sem isso o repositorio enche de \u00e7
            indent=2,
        ) + "\n"

        tmp = caminho.with_name(caminho.name + ".tmp")
        try:
            tmp.write_text(texto, encoding="utf-8", newline="\n")
            os.replace(tmp, caminho)  # atomico; os.rename falha no Windows
        except OSError as e:
            tmp.unlink(missing_ok=True)
            raise ErroRepositorio(f"falha ao gravar {caminho.name}: {e}") from e

    # -----------------------------------------------------------------
    # Leitura
    # -----------------------------------------------------------------

    def equipe(self) -> Equipe:
        return self._ler(self.dados / "equipe.json", Equipe)

    def projeto(self) -> Projeto:
        return self._ler(self.dados / "projeto.json", Projeto)

    def stack(self) -> Stack:
        return self._ler(self.dados / "stack.json", Stack)

    def board(self, chave: str) -> BoardMembro:
        """`chave` e o nome do arquivo sem extensao: um id de membro ou nao_atribuidas."""
        return self._ler(self.board_dir / f"{chave}.json", BoardMembro)

    def boards(self) -> dict[str, BoardMembro]:
        """Todos os arquivos do board, indexados pela chave."""
        return {
            caminho.stem: self._ler(caminho, BoardMembro)
            for caminho in sorted(self.board_dir.glob("*.json"))
        }

    def todas_tarefas(self) -> list[tuple[str, Tarefa]]:
        """Lista plana de (chave_do_arquivo, tarefa). Base para montar o board."""
        return [
            (chave, tarefa)
            for chave, board in self.boards().items()
            for tarefa in board.tarefas
        ]

    def _localizar(self, tarefa_id: str) -> tuple[str, BoardMembro, int]:
        for chave, board in self.boards().items():
            for i, tarefa in enumerate(board.tarefas):
                if tarefa.id == tarefa_id:
                    return chave, board, i
        raise ErroRepositorio(f"tarefa {tarefa_id} nao encontrada")

    # -----------------------------------------------------------------
    # Escrita
    # -----------------------------------------------------------------

    def salvar_board(self, chave: str, board: BoardMembro) -> None:
        """Ordena por id e grava.

        A ordenacao nao e estetica: mantendo a lista sempre ordenada, uma
        tarefa nova entra no meio do arquivo em vez de no fim, longe de onde
        o dono do arquivo esta editando. E o que faz o merge do Git passar
        limpo quando eu crio tarefa e ele mexe no board ao mesmo tempo.
        """
        board.tarefas.sort(key=lambda t: t.numero)
        self._gravar(self.board_dir / f"{chave}.json", board)

    def proximo_id(self) -> str:
        """Maior id existente + 1, varrendo todos os arquivos.

        Derivado, nunca armazenado: contador em arquivo separado dessincroniza
        entre clones e gera ids duplicados.
        """
        numeros = [t.numero for _, t in self.todas_tarefas()]
        return f"T-{(max(numeros) + 1 if numeros else 1):03d}"

    def criar_tarefa(
        self,
        titulo: str,
        por: str,
        responsavel: Optional[str] = None,
        **campos,
    ) -> Tarefa:
        agora = datetime.now().replace(microsecond=0)
        chave = responsavel or NAO_ATRIBUIDAS
        board = self.board(chave)

        projeto = self.projeto()
        tarefa = Tarefa(
            id=self.proximo_id(),
            titulo=titulo,
            status=campos.pop("status", projeto.colunas[0].id),
            criado_em=agora,
            atualizado_em=agora,
            atualizado_por=por,
            **campos,
        )
        board.tarefas.append(tarefa)
        self.salvar_board(chave, board)
        return tarefa

    def atualizar_tarefa(self, tarefa_id: str, por: str, **campos) -> Tarefa:
        chave, board, i = self._localizar(tarefa_id)
        tarefa = board.tarefas[i]

        for nome, valor in campos.items():
            setattr(tarefa, nome, valor)  # validate_assignment barra valor invalido
        tarefa.atualizado_em = datetime.now().replace(microsecond=0)
        tarefa.atualizado_por = por

        self.salvar_board(chave, board)
        return tarefa

    def mover_tarefa(self, tarefa_id: str, novo_status: str, por: str) -> Tarefa:
        """Arrastar cartao entre colunas: a operacao mais frequente do sistema."""
        if novo_status not in self.projeto().ids_colunas:
            raise ErroRepositorio(f"coluna inexistente: {novo_status}")
        return self.atualizar_tarefa(tarefa_id, por=por, status=novo_status)

    def realocar_tarefa(self, tarefa_id: str, novo_dono: Optional[str], por: str) -> Tarefa:
        """Unica operacao que toca dois arquivos.

        Grava o destino primeiro. Se a gravacao da origem falhar depois, a
        tarefa fica duplicada -- visivel e corrigivel. Na ordem inversa, ela
        sumiria. Entre os dois modos de falhar, duplicar e o menos ruim.
        """
        origem, board_origem, i = self._localizar(tarefa_id)
        destino = novo_dono or NAO_ATRIBUIDAS
        if origem == destino:
            return board_origem.tarefas[i]

        board_destino = self.board(destino)
        tarefa = board_origem.tarefas.pop(i)
        tarefa.atualizado_em = datetime.now().replace(microsecond=0)
        tarefa.atualizado_por = por
        board_destino.tarefas.append(tarefa)

        self.salvar_board(destino, board_destino)
        try:
            self.salvar_board(origem, board_origem)
        except ErroRepositorio as e:
            raise ErroRepositorio(
                f"tarefa {tarefa_id} copiada para {destino} mas nao removida de "
                f"{origem}; remova manualmente. Causa: {e}"
            ) from e
        return tarefa

    def excluir_tarefa(self, tarefa_id: str) -> Tarefa:
        chave, board, i = self._localizar(tarefa_id)
        tarefa = board.tarefas.pop(i)
        self.salvar_board(chave, board)
        return tarefa

    # -----------------------------------------------------------------
    # Integridade referencial entre arquivos
    # -----------------------------------------------------------------

    def verificar_integridade(self) -> list[str]:
        """Checagens que nenhum schema isolado consegue fazer.

        Devolve lista de problemas (vazia = tudo certo). E o equivalente
        manual das chaves estrangeiras que um banco daria de graca.
        """
        problemas: list[str] = []

        try:
            equipe = self.equipe()
            projeto = self.projeto()
            stack = self.stack()
            boards = self.boards()
        except ErroRepositorio as e:
            return [str(e)]

        ids_membros = equipe.ids
        colunas = projeto.ids_colunas
        entregas = projeto.ids_entregas
        vistos: dict[str, str] = {}

        for chave, board in boards.items():
            if chave != NAO_ATRIBUIDAS:
                if chave not in ids_membros:
                    problemas.append(f"board/{chave}.json: '{chave}' nao existe na equipe")
                if board.responsavel != chave:
                    problemas.append(
                        f"board/{chave}.json: campo responsavel e "
                        f"'{board.responsavel}', esperado '{chave}'"
                    )
            elif board.responsavel is not None:
                problemas.append(f"board/{chave}.json: responsavel deveria ser null")

            for t in board.tarefas:
                if t.id in vistos:
                    problemas.append(
                        f"tarefa {t.id} duplicada em {vistos[t.id]} e {chave}"
                    )
                vistos[t.id] = chave

                if t.status not in colunas:
                    problemas.append(f"{t.id}: coluna inexistente '{t.status}'")
                if t.entrega and t.entrega not in entregas:
                    problemas.append(f"{t.id}: entrega inexistente '{t.entrega}'")
                if t.atualizado_por not in ids_membros:
                    problemas.append(
                        f"{t.id}: atualizado_por '{t.atualizado_por}' nao esta na equipe"
                    )

        for item in stack.itens:
            if item.responsavel and item.responsavel not in ids_membros:
                problemas.append(
                    f"stack '{item.camada}': responsavel '{item.responsavel}' "
                    "nao esta na equipe"
                )

        return problemas

    def arquivos_do_board_faltando(self) -> list[str]:
        """Membros da equipe sem arquivo de board correspondente."""
        existentes = {p.stem for p in self.board_dir.glob("*.json")}
        esperados = {m.id for m in self.equipe().membros if m.perfil != "professor"}
        return sorted(esperados - existentes)