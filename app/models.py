"""
Schemas de dados do Gerenciador de Software.

Este modulo e o contrato do sistema: todo JSON lido de disco e validado
contra estas classes antes de circular pelo resto do codigo. Nada de dict
solto fora daqui.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Vocabularios fechados
# ---------------------------------------------------------------------------


class Papel(str, Enum):
    """Area de atuacao do membro dentro do projeto."""

    FRONT = "front"
    BACK = "back"
    MOBILE = "mobile"
    QA = "qa"
    GESTAO = "gestao"


class Perfil(str, Enum):
    """O que a pessoa pode fazer dentro do app (nao e o que ela faz no projeto)."""

    ADMIN = "admin"
    MEMBRO = "membro"
    PROFESSOR = "professor"


class Prioridade(str, Enum):
    ALTA = "alta"
    MEDIA = "media"
    BAIXA = "baixa"


class TipoRequisito(str, Enum):
    FUNCIONAL = "funcional"
    NAO_FUNCIONAL = "nao_funcional"


class StatusRequisito(str, Enum):
    PROPOSTO = "proposto"
    APROVADO = "aprovado"
    IMPLEMENTADO = "implementado"
    CANCELADO = "cancelado"


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class Base(BaseModel):
    """Configuracao comum a todos os schemas."""

    model_config = ConfigDict(
        extra="forbid",           # chave desconhecida no JSON vira erro, nao vira silencio
        str_strip_whitespace=True,
        validate_assignment=True,  # atribuir valor invalido em memoria tambem falha
    )


# ---------------------------------------------------------------------------
# equipe.json
# ---------------------------------------------------------------------------


class Membro(Base):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    nome: str = Field(min_length=1)
    papeis: list[Papel] = Field(default_factory=list)
    perfil: Perfil
    cor: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    github: Optional[str] = None

    @model_validator(mode="after")
    def _papeis_obrigatorios_para_membros(self):
        if self.perfil != Perfil.PROFESSOR and not self.papeis:
            raise ValueError(
                f"membro '{self.id}' precisa de pelo menos um papel"
            )
        return self


class Equipe(Base):
    membros: list[Membro] = Field(min_length=1)

    @field_validator("membros")
    @classmethod
    def _ids_unicos(cls, v: list[Membro]) -> list[Membro]:
        ids = [m.id for m in v]
        duplicados = {i for i in ids if ids.count(i) > 1}
        if duplicados:
            raise ValueError(f"ids de membro repetidos: {sorted(duplicados)}")
        return v

    def por_id(self, membro_id: str) -> Optional[Membro]:
        return next((m for m in self.membros if m.id == membro_id), None)

    @property
    def ids(self) -> set[str]:
        return {m.id for m in self.membros}


# ---------------------------------------------------------------------------
# projeto.json
# ---------------------------------------------------------------------------


class Coluna(Base):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    nome: str = Field(min_length=1)
    wip_limite: Optional[int] = Field(default=None, ge=1)


class Entrega(Base):
    id: str = Field(pattern=r"^E\d+$")
    nome: str = Field(min_length=1)
    prazo: Optional[date] = None
    descricao: str = ""
    concluida: bool = False


class Links(Base):
    repositorio: Optional[str] = None
    drive: Optional[str] = None


class Projeto(Base):
    nome: str = Field(min_length=1)
    disciplina: str
    professor: str = ""
    semestre: str = ""
    links: Links = Field(default_factory=Links)
    colunas: list[Coluna] = Field(min_length=1)
    entregas: list[Entrega] = Field(default_factory=list)

    @field_validator("colunas")
    @classmethod
    def _colunas_unicas(cls, v: list[Coluna]) -> list[Coluna]:
        ids = [c.id for c in v]
        duplicados = {i for i in ids if ids.count(i) > 1}
        if duplicados:
            raise ValueError(f"ids de coluna repetidos: {sorted(duplicados)}")
        return v

    @field_validator("entregas")
    @classmethod
    def _entregas_unicas(cls, v: list[Entrega]) -> list[Entrega]:
        ids = [e.id for e in v]
        duplicados = {i for i in ids if ids.count(i) > 1}
        if duplicados:
            raise ValueError(f"ids de entrega repetidos: {sorted(duplicados)}")
        return v

    @property
    def ids_colunas(self) -> set[str]:
        return {c.id for c in self.colunas}

    @property
    def ids_entregas(self) -> set[str]:
        return {e.id for e in self.entregas}

    @property
    def proxima_entrega(self) -> Optional[Entrega]:
        """Primeira entrega nao concluida que tenha prazo. Derivado, nunca armazenado."""
        pendentes = [e for e in self.entregas if not e.concluida and e.prazo is not None]
        return min(pendentes, key=lambda e: e.prazo) if pendentes else None


# ---------------------------------------------------------------------------
# board/<membro>.json
# ---------------------------------------------------------------------------


class Tarefa(Base):
    id: str = Field(pattern=r"^T-\d{3,}$")
    titulo: str = Field(min_length=1)
    descricao: str = ""
    status: str
    prioridade: Prioridade = Prioridade.MEDIA
    entrega: Optional[str] = None
    requisitos: list[str] = Field(default_factory=list)
    criado_em: datetime
    atualizado_em: datetime
    atualizado_por: str

    @property
    def numero(self) -> int:
        """Parte numerica do id, usada para ordenar a lista antes de gravar."""
        return int(self.id.split("-")[1])


class BoardMembro(Base):
    """Conteudo de um arquivo do board. `responsavel` e None em nao_atribuidas.json."""

    responsavel: Optional[str] = None
    tarefas: list[Tarefa] = Field(default_factory=list)

    @field_validator("tarefas")
    @classmethod
    def _tarefas_unicas(cls, v: list[Tarefa]) -> list[Tarefa]:
        ids = [t.id for t in v]
        duplicados = {i for i in ids if ids.count(i) > 1}
        if duplicados:
            raise ValueError(f"ids de tarefa repetidos no arquivo: {sorted(duplicados)}")
        return v


# ---------------------------------------------------------------------------
# stack.json
# ---------------------------------------------------------------------------


class Produto(Base):
    nome: str = Field(min_length=1)
    descricao: str = ""


class ItemStack(Base):
    camada: str = Field(min_length=1)
    tecnologia: str = Field(min_length=1)
    responsavel: Optional[str] = None
    justificativa: str = ""


class Stack(Base):
    produto: Produto
    itens: list[ItemStack] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# ers/requisitos/<id>.json  (estrutura pronta, uso na fase final)
# ---------------------------------------------------------------------------


class Requisito(Base):
    id: str = Field(pattern=r"^(RF|RNF)-\d{3}$")
    tipo: TipoRequisito
    titulo: str = Field(min_length=1)
    descricao: str = ""
    prioridade: Prioridade = Prioridade.MEDIA
    origem: str = ""
    criterios_aceite: list[str] = Field(default_factory=list)
    status: StatusRequisito = StatusRequisito.PROPOSTO