"""
Camada HTTP.

Aqui nao ha logica de disco nem regra de negocio: as rotas leem pelo
repositorio, montam o que a tela precisa e delegam a renderizacao ao Jinja.

Sobre o "login": o cookie de perfil escolhe uma visao, nao protege nada.
Quem tem o repositorio tem todos os dados. Isso e proposital e esta
registrado como tal -- nao trate como controle de acesso.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pydantic import BaseModel

from app.models import Membro, Perfil
from app.repositorio import NAO_ATRIBUIDAS, ErroRepositorio, Repositorio

APP_DIR = Path(__file__).resolve().parent
RAIZ = APP_DIR.parent

app = FastAPI(title="Gerenciador de Software")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
repo = Repositorio(RAIZ)

SEM_DONO = "#9AA1AC"
ORDEM_PRIORIDADE = {"alta": 0, "media": 1, "baixa": 2}
COOKIE = "perfil"
UM_ANO = 60 * 60 * 24 * 365


# ---------------------------------------------------------------------------
# Perfil ativo
# ---------------------------------------------------------------------------


def atual(request: Request) -> Optional[Membro]:
    """Membro escolhido na tela de entrada, ou None."""
    membro_id = request.cookies.get(COOKIE)
    return repo.equipe().por_id(membro_id) if membro_id else None


def pode_editar(membro: Optional[Membro]) -> bool:
    return membro is not None and membro.perfil != Perfil.PROFESSOR


def _ou_nulo(valor: Optional[str]) -> Optional[str]:
    """Select vazio no HTML chega como string vazia; no modelo isso e None."""
    return valor or None


def _para_entrada() -> RedirectResponse:
    return RedirectResponse("/entrar", status_code=303)


@app.get("/entrar")
def form_entrar(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="entrar.html",
        context={"projeto": repo.projeto(), "membros": repo.equipe().membros},
    )


@app.post("/entrar")
def entrar(membro: str = Form(...)):
    resposta = RedirectResponse("/", status_code=303)
    resposta.set_cookie(COOKIE, membro, max_age=UM_ANO, samesite="lax")
    return resposta


@app.get("/sair")
def sair():
    resposta = _para_entrada()
    resposta.delete_cookie(COOKIE)
    return resposta


# ---------------------------------------------------------------------------
# Board
# ---------------------------------------------------------------------------


def _cartoes_por_coluna(coluna_id: str, tarefas, membros) -> list[dict]:
    """Monta os cartoes de uma coluna, ja ordenados.

    Ordem: prioridade primeiro, mais recente depois. Nao existe campo de
    posicao manual -- ordenar por dado real evita reescrever todos os
    arquivos da coluna a cada arrastada.
    """
    selecionadas = [(chave, t) for chave, t in tarefas if t.status == coluna_id]
    selecionadas.sort(
        key=lambda par: (
            ORDEM_PRIORIDADE.get(par[1].prioridade.value, 9),
            -par[1].atualizado_em.timestamp(),
        )
    )

    cartoes = []
    for chave, tarefa in selecionadas:
        membro = membros.get(chave)
        cartoes.append(
            {
                "tarefa": tarefa,
                "chave": chave,
                "responsavel": membro.nome if membro else "Sem responsavel",
                "cor": membro.cor if membro else SEM_DONO,
            }
        )
    return cartoes


@app.get("/")
def ver_board(request: Request, ver: str = ""):
    eu = atual(request)
    if eu is None:
        return _para_entrada()

    try:
        projeto = repo.projeto()
        equipe = repo.equipe()
        tarefas = repo.todas_tarefas()
    except ErroRepositorio as e:
        return templates.TemplateResponse(
            request=request,
            name="erro.html",
            context={"mensagem": str(e)},
            status_code=500,
        )

    # Membro comum abre no proprio trabalho; quem coordena e quem avalia
    # abrem no board inteiro. Os dois podem trocar pelo filtro.
    padrao = "meus" if eu.perfil == Perfil.MEMBRO else "todos"
    escopo = ver or padrao
    if escopo == "meus":
        tarefas = [(c, t) for c, t in tarefas if c == eu.id]

    membros = {m.id: m for m in equipe.membros}
    colunas = [
        {"dados": c, "cartoes": _cartoes_por_coluna(c.id, tarefas, membros)}
        for c in projeto.colunas
    ]

    entrega = projeto.proxima_entrega
    dias = (entrega.prazo - date.today()).days if entrega else None

    return templates.TemplateResponse(
        request=request,
        name="board.html",
        context={
            "projeto": projeto,
            "colunas": colunas,
            "eu": eu,
            "editavel": pode_editar(eu),
            "escopo": escopo,
            "total": len(tarefas),
            "entrega": entrega,
            "dias": dias,
            # Integridade quebrada nao impede o uso do board, mas fica
            # visivel no topo -- o time precisa saber antes de commitar.
            "problemas": repo.verificar_integridade(),
        },
    )



# ---------------------------------------------------------------------------
# Equipe e stack
# ---------------------------------------------------------------------------


@app.get("/equipe")
def ver_equipe(request: Request):
    eu = atual(request)
    if eu is None:
        return _para_entrada()

    projeto = repo.projeto()
    equipe = repo.equipe()
    tarefas = repo.todas_tarefas()

    fichas = []
    for membro in equipe.membros:
        if membro.perfil == Perfil.PROFESSOR:
            continue
        minhas = [t for chave, t in tarefas if chave == membro.id]
        fichas.append(
            {
                "membro": membro,
                "total": len(minhas),
                "distribuicao": [
                    {"nome": c.nome, "qtd": sum(1 for t in minhas if t.status == c.id)}
                    for c in projeto.colunas
                ],
            }
        )

    return templates.TemplateResponse(
        request=request,
        name="equipe.html",
        context={
            "projeto": projeto,
            "eu": eu,
            "fichas": fichas,
            "docente": next(
                (m for m in equipe.membros if m.perfil == Perfil.PROFESSOR), None
            ),
            "sem_dono": sum(1 for c, _ in tarefas if c == NAO_ATRIBUIDAS),
        },
    )


@app.get("/stack")
def ver_stack(request: Request):
    eu = atual(request)
    if eu is None:
        return _para_entrada()

    return templates.TemplateResponse(
        request=request,
        name="stack.html",
        context={
            "projeto": repo.projeto(),
            "eu": eu,
            "stack": repo.stack(),
            "membros": {m.id: m for m in repo.equipe().membros},
        },
    )

# ---------------------------------------------------------------------------
# CRUD de tarefas
# ---------------------------------------------------------------------------


def contexto_formulario(request: Request, eu: Membro, tarefa=None, chave: str = "") -> dict:
    projeto = repo.projeto()
    return {
        "request": request,
        "projeto": projeto,
        "eu": eu,
        "tarefa": tarefa,
        "responsavel_atual": chave if chave != NAO_ATRIBUIDAS else "",
        "membros": [m for m in repo.equipe().membros if m.perfil != Perfil.PROFESSOR],
        "colunas": projeto.colunas,
        "entregas": projeto.entregas,
        "prioridades": ["alta", "media", "baixa"],
    }


@app.get("/tarefa/nova")
def form_nova(request: Request):
    eu = atual(request)
    if not pode_editar(eu):
        return _para_entrada()
    return templates.TemplateResponse(
        request=request,
        name="tarefa_form.html",
        context=contexto_formulario(request, eu),
    )


@app.post("/tarefa/nova")
def criar_tarefa(
    request: Request,
    titulo: str = Form(...),
    descricao: str = Form(""),
    responsavel: str = Form(""),
    status: str = Form(...),
    prioridade: str = Form("media"),
    entrega: str = Form(""),
):
    eu = atual(request)
    if not pode_editar(eu):
        return _para_entrada()

    repo.criar_tarefa(
        titulo=titulo,
        por=eu.id,
        responsavel=_ou_nulo(responsavel),
        descricao=descricao,
        status=status,
        prioridade=prioridade,
        entrega=_ou_nulo(entrega),
    )
    return RedirectResponse("/", status_code=303)


@app.get("/tarefa/{tarefa_id}/editar")
def form_editar(request: Request, tarefa_id: str):
    eu = atual(request)
    if not pode_editar(eu):
        return _para_entrada()

    try:
        chave, board, i = repo._localizar(tarefa_id)
    except ErroRepositorio as e:
        return templates.TemplateResponse(
            request=request, name="erro.html",
            context={"mensagem": str(e)}, status_code=404,
        )

    return templates.TemplateResponse(
        request=request,
        name="tarefa_form.html",
        context=contexto_formulario(request, eu, board.tarefas[i], chave),
    )


@app.post("/tarefa/{tarefa_id}/editar")
def salvar_edicao(
    request: Request,
    tarefa_id: str,
    titulo: str = Form(...),
    descricao: str = Form(""),
    responsavel: str = Form(""),
    status: str = Form(...),
    prioridade: str = Form("media"),
    entrega: str = Form(""),
):
    eu = atual(request)
    if not pode_editar(eu):
        return _para_entrada()

    # Campos que o formulario nao conhece -- criado_em, requisitos -- ficam
    # intactos, porque nao sao tocados aqui.
    repo.atualizar_tarefa(
        tarefa_id,
        por=eu.id,
        titulo=titulo,
        descricao=descricao,
        status=status,
        prioridade=prioridade,
        entrega=_ou_nulo(entrega),
    )

    # Trocar de dono e mudar a tarefa de arquivo: operacao separada.
    destino = _ou_nulo(responsavel) or NAO_ATRIBUIDAS
    origem, _, _ = repo._localizar(tarefa_id)
    if origem != destino:
        repo.realocar_tarefa(tarefa_id, _ou_nulo(responsavel), por=eu.id)

    return RedirectResponse("/", status_code=303)


@app.post("/tarefa/{tarefa_id}/excluir")
def excluir(request: Request, tarefa_id: str):
    eu = atual(request)
    if not pode_editar(eu):
        return _para_entrada()
    repo.excluir_tarefa(tarefa_id)
    return RedirectResponse("/", status_code=303)


# ---------------------------------------------------------------------------
# API do arrastar
# ---------------------------------------------------------------------------


class Movimento(BaseModel):
    status: str


@app.post("/api/tarefa/{tarefa_id}/mover")
def api_mover(request: Request, tarefa_id: str, movimento: Movimento):
    """Chamada pelo board ao soltar um cartao em outra coluna.

    Devolve JSON em vez de redirect: quem chama e o fetch do navegador, e
    ele precisa saber se falhou para devolver o cartao ao lugar.
    """
    eu = atual(request)
    if not pode_editar(eu):
        return JSONResponse({"detalhe": "sem permissao para mover"}, status_code=403)

    try:
        tarefa = repo.mover_tarefa(tarefa_id, movimento.status, por=eu.id)
    except ErroRepositorio as e:
        return JSONResponse({"detalhe": str(e)}, status_code=400)

    return {"id": tarefa.id, "status": tarefa.status}