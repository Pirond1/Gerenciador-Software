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
    """Classificacao do modelo de ERS do professor.
 
    As funcoes do produto (secao 2.2) sao divididas em tres grupos, e o
    prefixo do id acompanha o grupo: RF_B01, RF_F01, RF_S01. RNF nao tem
    secao propria no modelo -- entra em 2.4 -- mas modelamos igual para
    poder rastrear.
    """
 
    BASICA = "basica"            # RF_B — CRUD
    FUNDAMENTAL = "fundamental"  # RF_F — transacoes de negocio
    SAIDA = "saida"              # RF_S — consultas e relatorios
    NAO_FUNCIONAL = "nao_funcional"  # RNF
 
 
# Prefixo do id para cada tipo. Fonte unica: usado na validacao cruzada
# e na geracao do proximo id.
PREFIXO_REQUISITO = {
    TipoRequisito.BASICA: "RF_B",
    TipoRequisito.FUNDAMENTAL: "RF_F",
    TipoRequisito.SAIDA: "RF_S",
    TipoRequisito.NAO_FUNCIONAL: "RNF",
}


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
    senha_hash: str = ""

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
    """Uma funcao do produto, no formato da secao 2.2 do modelo.
 
    Os campos de lista servem aos tres tipos com nomes diferentes no
    documento: em RF_B e RF_F, `entradas` sao os itens de informacao
    obrigatorios; em RF_S, sao os filtros de consulta.
    """
 
    id: str = Field(pattern=r"^(RF_[BFS]|RNF)\d{2}$")
    tipo: TipoRequisito
    titulo: str = Field(min_length=1)
    descricao: str = ""
    entradas: list[str] = Field(default_factory=list)
    opcionais: list[str] = Field(default_factory=list)
    saidas: list[str] = Field(default_factory=list)
    regras: list[str] = Field(default_factory=list)
    criterios_aceite: list[str] = Field(default_factory=list)
    prioridade: Prioridade = Prioridade.MEDIA
    origem: str = ""
    status: StatusRequisito = StatusRequisito.PROPOSTO
 
    @model_validator(mode="after")
    def _prefixo_combina_com_tipo(self):
        """RF_B01 nao pode estar marcado como fundamental.
 
        Sem esta checagem, o id e o tipo divergem em silencio e o
        documento exportado sai com o requisito na secao errada.
        """
        esperado = PREFIXO_REQUISITO[self.tipo]
        if not self.id.startswith(esperado):
            raise ValueError(
                f"requisito {self.id}: tipo '{self.tipo.value}' exige "
                f"prefixo '{esperado}'"
            )
        return self
 
    @property
    def numero(self) -> int:
        return int(self.id[-2:])
 
 
class PassoFluxo(Base):
    """Um passo numerado de fluxo de caso de uso."""
 
    ator: str = ""          # quem executa: usuario, sistema...
    acao: str = Field(min_length=1)
 
 
class FluxoAlternativo(Base):
    nome: str = Field(min_length=1)
    passos: list[PassoFluxo] = Field(default_factory=list)
 
 
class CasoDeUso(Base):
    """Especificacao no formato da secao 3.2 do modelo."""
 
    id: str = Field(pattern=r"^UC\d{2}$")
    nome: str = Field(min_length=1)
    ator_principal: str = ""
    requisitos: list[str] = Field(default_factory=list)  # referencias cruzadas
    pre_condicao: str = ""
    pos_condicao: str = ""
    fluxo_principal: list[PassoFluxo] = Field(default_factory=list)
    fluxos_alternativos: list[FluxoAlternativo] = Field(default_factory=list)
 
    @property
    def numero(self) -> int:
        return int(self.id[2:])
        

class Revisao(Base):
    """Uma linha do historico de revisoes da ERS."""

    versao: str = Field(min_length=1)
    data: date
    descricao: str = ""
    autor: str = ""


class Documento(Base):
    """Secoes em prosa da ERS.

    As funcoes do produto (2.2) NAO estao aqui: elas sao os Requisitos, e
    duplicar o conteudo em dois lugares e o caminho mais curto para o
    documento divergir de si mesmo.

    Todos os campos aceitam Markdown e nascem vazios: a ERS e preenchida aos
    poucos, e um arquivo ausente nao pode quebrar o app.
    """

    objetivo: str = ""                  # 1.1
    escopo: str = ""                    # 1.2
    visao_geral: str = ""               # 1.5
    perspectiva: str = ""               # 2.1
    caracteristicas_usuario: str = ""   # 2.3 — texto que acompanha a tabela de atores
    restricoes: str = ""                # 2.4
    requisitos_adiados: str = ""        # 2.5
    viabilidade: str = ""               # 2.6
    referencias: list[str] = Field(default_factory=list)   # 1.4
    revisoes: list[Revisao] = Field(default_factory=list)


class Ator(Base):
    """Ator do sistema.

    Serve a duas secoes do modelo ao mesmo tempo: a tabela de
    caracteristicas do usuario (2.3) e o diagrama de casos de uso (3.1).
    Uma fonte, duas saidas -- em vez de duas listas que divergem.
    """

    id: str = Field(pattern=r"^[a-z0-9_]+$")
    nome: str = Field(min_length=1)
    descricao: str = ""
    frequencia_uso: str = ""    # diaria, semanal, eventual
    nivel_instrucao: str = ""
    proficiencia: str = ""      # basica, intermediaria, avancada


class Atores(Base):
    atores: list[Ator] = Field(default_factory=list)

    @field_validator("atores")
    @classmethod
    def _ids_unicos(cls, v: list[Ator]) -> list[Ator]:
        ids = [a.id for a in v]
        duplicados = {i for i in ids if ids.count(i) > 1}
        if duplicados:
            raise ValueError(f"ids de ator repetidos: {sorted(duplicados)}")
        return v


class Termo(Base):
    """Entrada de 1.3 — Definicoes, siglas e abreviacoes."""

    termo: str = Field(min_length=1)
    definicao: str = ""


class Glossario(Base):
    termos: list[Termo] = Field(default_factory=list)

    @field_validator("termos")
    @classmethod
    def _termos_unicos(cls, v: list[Termo]) -> list[Termo]:
        chaves = [t.termo.lower() for t in v]
        duplicados = {c for c in chaves if chaves.count(c) > 1}
        if duplicados:
            raise ValueError(f"termos repetidos no glossario: {sorted(duplicados)}")
        return v