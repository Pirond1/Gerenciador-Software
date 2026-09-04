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

import threading

from pydantic import BaseModel, ValidationError

from app.models import (
    BoardMembro, CasoDeUso, Equipe, ItemStack, Projeto, Requisito,
    Stack, Tarefa, TipoRequisito, PREFIXO_REQUISITO,
    Atores, Documento, Glossario, Ator, Termo
)

NAO_ATRIBUIDAS = "nao_atribuidas"


class ErroRepositorio(Exception):
    """Falha ao ler ou gravar dados."""


class Repositorio:
    def __init__(self, raiz: Path):
        self.raiz = Path(raiz)
        self.dados = self.raiz / "dados"
        self.board_dir = self.dados / "board"
        self.requisitos_dir = self.dados / "ers" / "requisitos"
        self.casos_dir = self.dados / "ers" / "casos-uso"
        self.ers_dir = self.dados / "ers"
        self._trava = threading.RLock()
        self.imagens_dir = self.dados / "ers" / "imagens"

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
        with self._trava:
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
        with self._trava:
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
        with self._trava:
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
        with self._trava:
            if novo_status not in self.projeto().ids_colunas:
                raise ErroRepositorio(f"coluna inexistente: {novo_status}")
            return self.atualizar_tarefa(tarefa_id, por=por, status=novo_status)

    def realocar_tarefa(self, tarefa_id: str, novo_dono: Optional[str], por: str) -> Tarefa:
        """Unica operacao que toca dois arquivos.

        Grava o destino primeiro. Se a gravacao da origem falhar depois, a
        tarefa fica duplicada -- visivel e corrigivel. Na ordem inversa, ela
        sumiria. Entre os dois modos de falhar, duplicar e o menos ruim.
        """
        with self._trava:
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
        with self._trava:
            chave, board, i = self._localizar(tarefa_id)
            tarefa = board.tarefas.pop(i)
            self.salvar_board(chave, board)
            return tarefa

    def salvar_projeto(self, projeto) -> None:
        with self._trava:
            self._gravar(self.dados / "projeto.json", projeto)

    def proximo_id_entrega(self) -> str:
        numeros = [int(e.id[1:]) for e in self.projeto().entregas]
        return f"E{max(numeros) + 1 if numeros else 1}"

    def salvar_equipe(self, equipe) -> None:
        """Usado pelas telas de administracao."""
        with self._trava:
            self._gravar(self.dados / "equipe.json", equipe)
 
    def salvar_stack(self, stack) -> None:
        with self._trava:
            self._gravar(self.dados / "stack.json", stack)


    # -----------------------------------------------------------------
    # ERS: requisitos e casos de uso
    #
    # Um arquivo por requisito, pela mesma razao do board: cada pessoa
    # escreve os seus, e arquivos separados nao conflitam no merge.
    # -----------------------------------------------------------------
 
    def requisitos(self) -> list[Requisito]:
        """Todos os requisitos, ordenados por tipo e numero."""
        if not self.requisitos_dir.exists():
            return []
        itens = [
            self._ler(caminho, Requisito)
            for caminho in sorted(self.requisitos_dir.glob("*.json"))
        ]
        ordem = list(PREFIXO_REQUISITO)
        itens.sort(key=lambda r: (ordem.index(r.tipo), r.numero))
        return itens
 
    def requisito(self, requisito_id: str) -> Requisito:
        return self._ler(self.requisitos_dir / f"{requisito_id}.json", Requisito)
 
    def salvar_requisito(self, requisito: Requisito) -> None:
        with self._trava:
            self.requisitos_dir.mkdir(parents=True, exist_ok=True)
            self._gravar(self.requisitos_dir / f"{requisito.id}.json", requisito)
 
    def excluir_requisito(self, requisito_id: str) -> None:
        with self._trava:
            caminho = self.requisitos_dir / f"{requisito_id}.json"
            if not caminho.exists():
                raise ErroRepositorio(f"requisito {requisito_id} nao encontrado")
            caminho.unlink()
 
    def proximo_id_requisito(self, tipo: TipoRequisito) -> str:
        """Numeracao independente por tipo: RF_B01, RF_B02, RF_F01..."""
        prefixo = PREFIXO_REQUISITO[tipo]
        numeros = [r.numero for r in self.requisitos() if r.tipo == tipo]
        return f"{prefixo}{(max(numeros) + 1 if numeros else 1):02d}"
 
    def casos_de_uso(self) -> list[CasoDeUso]:
        if not self.casos_dir.exists():
            return []
        itens = [
            self._ler(caminho, CasoDeUso)
            for caminho in sorted(self.casos_dir.glob("*.json"))
        ]
        itens.sort(key=lambda c: c.numero)
        return itens
 
    def caso_de_uso(self, caso_id: str) -> CasoDeUso:
        return self._ler(self.casos_dir / f"{caso_id}.json", CasoDeUso)
 
    def salvar_caso_de_uso(self, caso: CasoDeUso) -> None:
        with self._trava:
            self.casos_dir.mkdir(parents=True, exist_ok=True)
            self._gravar(self.casos_dir / f"{caso.id}.json", caso)
 
    def excluir_caso_de_uso(self, caso_id: str) -> None:
        with self._trava:
            caminho = self.casos_dir / f"{caso_id}.json"
            if not caminho.exists():
                raise ErroRepositorio(f"caso de uso {caso_id} nao encontrado")
            caminho.unlink()
 
    def proximo_id_caso(self) -> str:
        numeros = [c.numero for c in self.casos_de_uso()]
        return f"UC{(max(numeros) + 1 if numeros else 1):02d}"
 
    def tarefas_do_requisito(self, requisito_id: str) -> list[Tarefa]:
        """Rastreabilidade ao contrario: que tarefas atendem este requisito."""
        return [
            tarefa
            for _, tarefa in self.todas_tarefas()
            if requisito_id in tarefa.requisitos
        ]

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

        ids_requisitos = {r.id for r in self.requisitos()}

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
                for req in t.requisitos:
                    if req not in ids_requisitos:
                        problemas.append(f"{t.id}: requisito inexistente '{req}'")

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

        for caso in self.casos_de_uso():
            for req in caso.requisitos:
                if req not in ids_requisitos:
                    problemas.append(
                        f"{caso.id}: referencia cruzada para requisito "
                        f"inexistente '{req}'"
                )

        ids_atores = {a.id for a in self.atores().atores}
        for caso in self.casos_de_uso():
            if caso.ator_principal and caso.ator_principal not in ids_atores:
                problemas.append(
                    f"{caso.id}: ator principal '{caso.ator_principal}' "
                    "nao esta cadastrado"
                )

        return problemas

    def arquivos_do_board_faltando(self) -> list[str]:
        """Membros da equipe sem arquivo de board correspondente."""
        existentes = {p.stem for p in self.board_dir.glob("*.json")}
        esperados = {m.id for m in self.equipe().membros if m.perfil != "professor"}
        return sorted(esperados - existentes)

    # -----------------------------------------------------------------
    # ERS: documento, atores e glossario
    #
    # Estes tres sao arquivos unicos, nao um por item: sao pequenos e
    # quase sempre editados por uma pessoa so. O board precisou de
    # granularidade; aqui ela seria custo sem beneficio.
    # -----------------------------------------------------------------
 
    def _ler_ou_padrao(self, caminho: Path, modelo):
        """Devolve o objeto vazio quando o arquivo ainda nao existe.
 
        A ERS e preenchida ao longo do semestre: abrir a tela antes de
        existir arquivo tem que funcionar, nao dar erro.
        """
        return self._ler(caminho, modelo) if caminho.exists() else modelo()
 
    def documento(self) -> Documento:
        return self._ler_ou_padrao(self.ers_dir / "documento.json", Documento)
 
    def salvar_documento(self, documento: Documento) -> None:
        with self._trava:
            self.ers_dir.mkdir(parents=True, exist_ok=True)
            self._gravar(self.ers_dir / "documento.json", documento)
 
    def atores(self) -> Atores:
        return self._ler_ou_padrao(self.ers_dir / "atores.json", Atores)
 
    def salvar_atores(self, atores: Atores) -> None:
        with self._trava:
            self.ers_dir.mkdir(parents=True, exist_ok=True)
            atores.atores.sort(key=lambda a: a.nome.lower())
            self._gravar(self.ers_dir / "atores.json", atores)
 
    def glossario(self) -> Glossario:
        return self._ler_ou_padrao(self.ers_dir / "glossario.json", Glossario)
 
    def salvar_glossario(self, glossario: Glossario) -> None:
        with self._trava:
            self.ers_dir.mkdir(parents=True, exist_ok=True)
            # Ordem alfabetica no arquivo: e como o glossario e lido no
            # documento, e mantem o diff estavel a cada insercao.
            glossario.termos.sort(key=lambda t: t.termo.lower())
            self._gravar(self.ers_dir / "glossario.json", glossario)

    # -----------------------------------------------------------------
    # Imagens da ERS
    # -----------------------------------------------------------------
 
    TAMANHO_MAXIMO = 4 * 1024 * 1024  # 4 MB por imagem
    EXTENSOES = {"png": "png", "jpg": "jpg", "jpeg": "jpg"}
 
    def caminho_imagem(self, arquivo: str) -> Path:
        """Resolve o caminho recusando qualquer tentativa de sair da pasta.
 
        O nome vem da URL: sem esta checagem, '../../etc/senha' seria
        servido pelo app.
        """
        destino = (self.imagens_dir / arquivo).resolve()
        if not str(destino).startswith(str(self.imagens_dir.resolve())):
            raise ErroRepositorio("caminho de imagem invalido")
        return destino
 
    def salvar_imagem(self, secao: str, nome_original: str, conteudo: bytes) -> str:
        """Grava a imagem e devolve o nome do arquivo gerado.
 
        O nome e gerado por nos, nunca aproveitado do upload: nome vindo do
        navegador pode conter caminho, acento ou caractere que quebra o
        sistema de arquivos.
        """
        extensao = nome_original.rsplit(".", 1)[-1].lower() if "." in nome_original else ""
        if extensao not in self.EXTENSOES:
            raise ErroRepositorio(
                f"formato '{extensao or 'desconhecido'}' nao aceito. Use PNG ou JPG."
            )
        if len(conteudo) > self.TAMANHO_MAXIMO:
            raise ErroRepositorio(
                f"imagem de {len(conteudo) // 1024} KB excede o limite de "
                f"{self.TAMANHO_MAXIMO // 1024} KB."
            )
        if not conteudo:
            raise ErroRepositorio("arquivo vazio")
 
        with self._trava:
            self.imagens_dir.mkdir(parents=True, exist_ok=True)
            usados = {p.name for p in self.imagens_dir.glob(f"{secao}-*")}
            i = 1
            while f"{secao}-{i}.{self.EXTENSOES[extensao]}" in usados:
                i += 1
            arquivo = f"{secao}-{i}.{self.EXTENSOES[extensao]}"
 
            destino = self.imagens_dir / arquivo
            temporario = destino.with_name(destino.name + ".tmp")
            temporario.write_bytes(conteudo)
            os.replace(temporario, destino)      # mesma escrita atomica dos JSONs
            return arquivo
 
    def excluir_imagem(self, arquivo: str) -> None:
        with self._trava:
            caminho = self.caminho_imagem(arquivo)
            if caminho.exists():
                caminho.unlink()