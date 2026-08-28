"""
Camada HTTP.

Aqui nao ha logica de disco nem regra de negocio: as rotas leem pelo
repositorio, montam o que a tela precisa e delegam a renderizacao ao Jinja.

Sobre o "login": o cookie de perfil escolhe uma visao, nao protege nada.
Quem tem o repositorio tem todos os dados. Isso e proposital e esta
registrado como tal -- nao trate como controle de acesso.
"""

from __future__ import annotations

import html
import re
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pydantic import BaseModel

import markdown as md

from app import gitinfo
from app.models import Entrega, Membro, Perfil
from app.repositorio import NAO_ATRIBUIDAS, ErroRepositorio, Repositorio

APP_DIR = Path(__file__).resolve().parent
RAIZ = APP_DIR.parent

app = FastAPI(title="Gerenciador de Software")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
repo = Repositorio(RAIZ)

# Disponivel em qualquer template sem precisar passar rota por rota.
templates.env.globals["git_estado"] = lambda: gitinfo.estado(RAIZ)


def render_markdown(texto: str) -> str:
    """Markdown das descricoes, com o HTML bruto neutralizado antes.

    Escapamos primeiro: o texto vem de outra pessoa via repositorio, e
    permitir HTML cru ali nao traz beneficio nenhum para o caso de uso.
    """
    if not texto.strip():
        return ""
    bruto = md.markdown(html.escape(texto), extensions=["nl2br", "tables"])
    # Checklists (- [ ] item) nao existem no markdown padrao; convertemos
    # o resultado para caixas desmarcaveis apenas visuais.
    bruto = re.sub(r"<li>\s*\[ \]\s*", '<li class="tarefa-item">', bruto)
    bruto = re.sub(
        r"<li>\s*\[[xX]\]\s*", '<li class="tarefa-item tarefa-item--feito">', bruto
    )
    return bruto

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


# ---------------------------------------------------------------------------
# Entregas
#
# Vivem dentro de projeto.json -- arquivo compartilhado, ao contrario do
# board. Por isso a edicao fica restrita ao admin: entrega e decisao de
# projeto, e limitar quem escreve nesse arquivo reduz conflito de merge.
# ---------------------------------------------------------------------------


def pode_gerir_entregas(membro: Optional[Membro]) -> bool:
    return membro is not None and membro.perfil == Perfil.ADMIN


def _situacao(entrega: Entrega) -> dict:
    """Estado derivado da data e do campo concluida.

    'concluida' e manual de proposito: prazo vencido nao significa entregue,
    e todas as tarefas prontas nao significa que foi enviada ao professor.
    """
    if entrega.concluida:
        return {"chave": "concluida", "rotulo": "concluida"}
    if entrega.prazo is None:
        return {"chave": "sem-data", "rotulo": "prazo a definir"}

    dias = (entrega.prazo - date.today()).days
    if dias < 0:
        return {"chave": "atrasada", "rotulo": f"{-dias} dia(s) em atraso"}
    if dias == 0:
        return {"chave": "hoje", "rotulo": "entrega hoje"}
    if dias == 1:
        return {"chave": "proxima", "rotulo": "falta 1 dia"}
    return {
        "chave": "proxima" if dias < 7 else "aberta",
        "rotulo": f"faltam {dias} dias",
    }


@app.get("/entregas")
def ver_entregas(request: Request):
    eu = atual(request)
    if eu is None:
        return _para_entrada()

    projeto = repo.projeto()
    tarefas = repo.todas_tarefas()
    # A ultima coluna do board e o fim do fluxo -- e o que define "pronta".
    coluna_final = projeto.colunas[-1].id

    linhas = []
    for entrega in projeto.entregas:
        ligadas = [t for _, t in tarefas if t.entrega == entrega.id]
        prontas = [t for t in ligadas if t.status == coluna_final]
        linhas.append(
            {
                "entrega": entrega,
                "situacao": _situacao(entrega),
                "total": len(ligadas),
                "prontas": len(prontas),
                "percentual": round(100 * len(prontas) / len(ligadas)) if ligadas else 0,
            }
        )

    return templates.TemplateResponse(
        request=request,
        name="entregas.html",
        context={
            "projeto": projeto,
            "eu": eu,
            "linhas": linhas,
            "gerivel": pode_gerir_entregas(eu),
            "soltas": sum(1 for _, t in tarefas if t.entrega is None),
        },
    )


@app.get("/entrega/nova")
def form_entrega_nova(request: Request):
    eu = atual(request)
    if not pode_gerir_entregas(eu):
        return RedirectResponse("/entregas", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="entrega_form.html",
        context={"projeto": repo.projeto(), "eu": eu, "entrega": None,
                 "proximo": repo.proximo_id_entrega()},
    )


@app.get("/entrega/{entrega_id}/editar")
def form_entrega_editar(request: Request, entrega_id: str):
    eu = atual(request)
    if not pode_gerir_entregas(eu):
        return RedirectResponse("/entregas", status_code=303)

    projeto = repo.projeto()
    entrega = next((e for e in projeto.entregas if e.id == entrega_id), None)
    if entrega is None:
        return templates.TemplateResponse(
            request=request, name="erro.html",
            context={"mensagem": f"entrega {entrega_id} nao encontrada"},
            status_code=404,
        )

    return templates.TemplateResponse(
        request=request,
        name="entrega_form.html",
        context={"projeto": projeto, "eu": eu, "entrega": entrega, "proximo": entrega.id},
    )


@app.post("/entrega/salvar")
def salvar_entrega(
    request: Request,
    entrega_id: str = Form(""),
    nome: str = Form(...),
    prazo: str = Form(""),
    descricao: str = Form(""),
):
    eu = atual(request)
    if not pode_gerir_entregas(eu):
        return RedirectResponse("/entregas", status_code=303)

    projeto = repo.projeto()
    existente = next((e for e in projeto.entregas if e.id == entrega_id), None)

    if existente:
        existente.nome = nome
        existente.prazo = _ou_nulo(prazo)
        existente.descricao = descricao
    else:
        projeto.entregas.append(
            Entrega(
                id=repo.proximo_id_entrega(),
                nome=nome,
                prazo=_ou_nulo(prazo),
                descricao=descricao,
            )
        )

    repo.salvar_projeto(projeto)
    return RedirectResponse("/entregas", status_code=303)


@app.post("/entrega/{entrega_id}/concluir")
def alternar_entrega(request: Request, entrega_id: str):
    eu = atual(request)
    if not pode_gerir_entregas(eu):
        return RedirectResponse("/entregas", status_code=303)

    projeto = repo.projeto()
    for entrega in projeto.entregas:
        if entrega.id == entrega_id:
            entrega.concluida = not entrega.concluida
            break
    repo.salvar_projeto(projeto)
    return RedirectResponse("/entregas", status_code=303)


@app.post("/entrega/{entrega_id}/excluir")
def excluir_entrega(request: Request, entrega_id: str):
    eu = atual(request)
    if not pode_gerir_entregas(eu):
        return RedirectResponse("/entregas", status_code=303)

    # Excluir uma entrega com tarefas ligadas deixaria essas tarefas
    # apontando para um id inexistente. Melhor barrar do que gerar orfaos.
    ligadas = [t.id for _, t in repo.todas_tarefas() if t.entrega == entrega_id]
    if ligadas:
        return templates.TemplateResponse(
            request=request,
            name="erro.html",
            context={
                "mensagem": f"{entrega_id} nao pode ser excluida: "
                f"{len(ligadas)} tarefa(s) ainda apontam para ela "
                f"({', '.join(ligadas[:8])}). Desvincule-as primeiro."
            },
            status_code=409,
        )

    projeto = repo.projeto()
    projeto.entregas = [e for e in projeto.entregas if e.id != entrega_id]
    repo.salvar_projeto(projeto)
    return RedirectResponse("/entregas", status_code=303)


# ---------------------------------------------------------------------------
# Detalhe da tarefa
# ---------------------------------------------------------------------------


@app.get("/tarefa/{tarefa_id}")
def ver_tarefa(request: Request, tarefa_id: str):
    """Visivel para todos os perfis, inclusive o professor.

    Sem esta tela, quem nao pode editar nao conseguia ler a descricao de
    nenhuma tarefa -- so o titulo do cartao.
    """
    eu = atual(request)
    if eu is None:
        return _para_entrada()

    try:
        chave, board, i = repo._localizar(tarefa_id)
    except ErroRepositorio as e:
        return templates.TemplateResponse(
            request=request, name="erro.html",
            context={"mensagem": str(e)}, status_code=404,
        )

    tarefa = board.tarefas[i]
    projeto = repo.projeto()
    equipe = repo.equipe()

    return templates.TemplateResponse(
        request=request,
        name="tarefa.html",
        context={
            "projeto": projeto,
            "eu": eu,
            "tarefa": tarefa,
            "editavel": pode_editar(eu),
            "descricao_html": render_markdown(tarefa.descricao),
            "dono": equipe.por_id(chave),
            "coluna": next(
                (c for c in projeto.colunas if c.id == tarefa.status), None
            ),
            "entrega": next(
                (e for e in projeto.entregas if e.id == tarefa.entrega), None
            ),
            "autor": equipe.por_id(tarefa.atualizado_por),
        },
    )


# ---------------------------------------------------------------------------
# Sincronizacao com o Git
# ---------------------------------------------------------------------------


def _tela_git(request: Request, eu: Membro, resultado=None, status=200):
    return templates.TemplateResponse(
        request=request,
        name="sincronizar.html",
        context={
            "projeto": repo.projeto(),
            "eu": eu,
            "git": gitinfo.estado(RAIZ),
            "resultado": resultado,
            "sugestao": gitinfo.mensagem_padrao(eu.nome),
            "problemas": repo.verificar_integridade(),
        },
        status_code=status,
    )


@app.get("/sincronizar")
def ver_sincronizar(request: Request):
    eu = atual(request)
    if eu is None:
        return _para_entrada()
    return _tela_git(request, eu)


@app.post("/sincronizar/baixar")
def git_baixar(request: Request):
    eu = atual(request)
    if not pode_editar(eu):
        return RedirectResponse("/sincronizar", status_code=303)

    ok, saida = gitinfo.baixar(RAIZ)
    return _tela_git(
        request, eu,
        {"acao": "Baixar do GitHub", "ok": ok, "saida": saida},
    )


@app.post("/sincronizar/enviar")
def git_enviar(request: Request, mensagem: str = Form("")):
    eu = atual(request)
    if not pode_editar(eu):
        return RedirectResponse("/sincronizar", status_code=303)

    # Portao de qualidade: dado inconsistente nao sobe. E mais barato
    # segurar aqui do que o colega descobrir com o app quebrado.
    problemas = repo.verificar_integridade()
    if problemas:
        return _tela_git(
            request, eu,
            {
                "acao": "Enviar ao GitHub",
                "ok": False,
                "saida": "Envio bloqueado: ha inconsistencias nos dados. "
                         "Corrija-as antes de subir.",
            },
        )

    passos = []
    ok, saida = gitinfo.commitar(RAIZ, mensagem.strip() or gitinfo.mensagem_padrao(eu.nome))
    passos.append(("Commit", ok, saida))

    if ok:
        # Baixar antes de enviar evita o push rejeitado por historico
        # desatualizado, que e a falha mais comum aqui.
        ok, saida = gitinfo.baixar(RAIZ)
        passos.append(("Baixar antes de enviar", ok, saida))

    if ok:
        ok, saida = gitinfo.enviar(RAIZ)
        passos.append(("Push", ok, saida))

    return _tela_git(
        request, eu,
        {"acao": "Enviar ao GitHub", "ok": ok, "passos": passos},
    )


@app.post("/sincronizar/verificar")
def git_verificar(request: Request):
    """Fetch manual, para quem nao quer esperar a checagem automatica."""
    eu = atual(request)
    if eu is None:
        return _para_entrada()

    ok, saida = gitinfo.verificar_agora(RAIZ)
    return _tela_git(
        request, eu,
        {"acao": "Verificar o servidor", "ok": ok, "saida": saida},
    )