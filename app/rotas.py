"""
Camada HTTP.

Aqui nao ha logica de disco nem regra de negocio: as rotas leem pelo
repositorio, montam o que a tela precisa e delegam a renderizacao ao Jinja.

Sobre o "login": o cookie de perfil escolhe uma visao, nao protege nada.
Quem tem o repositorio tem todos os dados. Isso e proposital e esta
registrado como tal -- nao trate como controle de acesso.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Optional
from datetime import date
from app.models import (Coluna, ItemStack, Membro, Papel, Perfil,
                        Produto, Revisao, Stack,
                        CasoDeUso, Entrega, Membro, Perfil, Requisito, TipoRequisito, PREFIXO_REQUISITO,
                        Ator, Atores, Documento, Glossario, Revisao, Termo, Figura)

from fastapi import FastAPI, Form, Request, File, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from urllib.parse import quote
from app import exportar, backup
import os

from pydantic import BaseModel

import markdown as md

from app import gitinfo, seguranca
from app.repositorio import NAO_ATRIBUIDAS, ErroRepositorio, Repositorio
import unicodedata

APP_DIR = Path(__file__).resolve().parent
# Em producao os dados vivem num volume separado do codigo: a imagem
# e substituida a cada deploy, o volume nao. Em desenvolvimento,
# continua sendo a raiz do projeto.
RAIZ = Path(os.environ.get("RAIZ_DADOS") or APP_DIR.parent)
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

app = FastAPI(title="Gerenciador de Software")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
repo = Repositorio(RAIZ)
backup.iniciar(RAIZ)

# Disponivel em qualquer template sem precisar passar rota por rota.
templates.env.globals["git_estado"] = lambda: gitinfo.estado(RAIZ)
 
# Cada secao em prosa: campo do modelo, numero e titulo no documento.
SECOES_PROSA = [
    ("objetivo", "1.1", "Objetivo"),
    ("escopo", "1.2", "Escopo"),
    ("visao_geral", "1.5", "Visao geral"),
    ("perspectiva", "2.1", "Perspectiva do produto"),
    ("caracteristicas_usuario", "2.3", "Caracteristicas do usuario"),
    ("restricoes", "2.4", "Restricoes, dependencias e suposicoes"),
    ("requisitos_adiados", "2.5", "Requisitos adiados"),
    ("viabilidade", "2.6", "Estudo de viabilidade"),
]


def _escapar_fora_de_codigo(texto: str) -> str:
    """Bloqueia HTML cru sem estragar o conteudo dos blocos de codigo.

    html.escape() no texto inteiro era o bug: transformava " em &quot;, e o
    markdown escapava o & de novo, imprimindo &quot; literal na tela.
    """
    partes = texto.split("```")
    for i in range(0, len(partes), 2):  # indices pares ficam fora dos blocos
        partes[i] = partes[i].replace("<", "&lt;")
    return "```".join(partes)


def render_markdown(texto: str) -> str:
    if not texto.strip():
        return ""
    bruto = md.markdown(
        _escapar_fora_de_codigo(texto),
        extensions=["fenced_code", "tables", "sane_lists"],
    )
    bruto = re.sub(r"<li>\s*\[ \]\s*", '<li class="tarefa-item">', bruto)
    bruto = re.sub(
        r"<li>\s*\[[xX]\]\s*", '<li class="tarefa-item tarefa-item--feito">', bruto
    )
    return bruto

SEM_DONO = "#9AA1AC"
ORDEM_PRIORIDADE = {"alta": 0, "media": 1, "baixa": 2}
COOKIE = "perfil"
UM_ANO = 60 * 60 * 24 * 365
SOB_HTTPS = os.environ.get("EM_PRODUCAO") == "1"


# ---------------------------------------------------------------------------
# Perfil ativo
# ---------------------------------------------------------------------------


def atual(request: Request) -> Optional[Membro]:
    """Membro da sessao, ou None.
 
    O cookie e assinado: antes bastava edita-lo no navegador para virar
    outra pessoa. Num servidor publico isso anularia a senha.
    """
    assinado = request.cookies.get(COOKIE)
    membro_id = seguranca.conferir_assinatura(assinado)
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
 
 
@app.get("/entrar/{membro_id}")
def form_senha(request: Request, membro_id: str, erro: str = ""):
    membro = repo.equipe().por_id(membro_id)
    if membro is None:
        return _para_entrada()
 
    # Quem ainda nao tem senha definida entra direto, como antes.
    if not membro.senha_hash:
        resposta = RedirectResponse("/", status_code=303)
        _gravar_sessao(resposta, membro.id)
        return resposta
 
    return templates.TemplateResponse(
        request=request,
        name="entrar_senha.html",
        context={"projeto": repo.projeto(), "membro": membro, "erro": erro},
    )
 
 
@app.post("/entrar/{membro_id}")
def entrar(membro_id: str, senha: str = Form("")):
    membro = repo.equipe().por_id(membro_id)
    if membro is None:
        return _para_entrada()
 
    if membro.senha_hash and not seguranca.conferir_senha(senha, membro.senha_hash):
        # Mensagem unica, sem dizer se o problema foi a pessoa ou a senha.
        return RedirectResponse(f"/entrar/{membro_id}?erro=1", status_code=303)
 
    resposta = RedirectResponse("/", status_code=303)
    _gravar_sessao(resposta, membro.id)
    return resposta
 
 
def _gravar_sessao(resposta, membro_id: str) -> None:
    resposta.set_cookie(
        COOKIE,
        seguranca.assinar(membro_id),
        max_age=UM_ANO,
        samesite="lax",
        httponly=True,   # o cookie nao precisa ser lido por JavaScript
        secure=SOB_HTTPS,
    )
 
 
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
        "requisitos_disponiveis": repo.requisitos(),
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
    requisitos: list[str] = Form([]),
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
        requisitos=requisitos
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
    requisitos: list[str] = Form([]),
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
        requisitos=requisitos,
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
            "backup": backup.estado(),
            "backup_minutos": backup.INTERVALO,
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

# Rotulos das secoes do documento, na ordem em que aparecem na ERS.
SECOES_REQUISITO = [
    (TipoRequisito.BASICA, "2.2.1 Funcoes basicas"),
    (TipoRequisito.FUNDAMENTAL, "2.2.2 Funcoes fundamentais"),
    (TipoRequisito.SAIDA, "2.2.3 Funcoes de saida"),
    (TipoRequisito.NAO_FUNCIONAL, "Requisitos nao funcionais"),
]
 
 
def _linhas(texto: str) -> list[str]:
    """Textarea com um item por linha vira lista, descartando linhas vazias."""
    return [linha.strip() for linha in texto.splitlines() if linha.strip()]
 
 
@app.get("/requisitos")
def ver_requisitos(request: Request):
    eu = atual(request)
    if eu is None:
        return _para_entrada()
 
    try:
        requisitos = repo.requisitos()
    except ErroRepositorio as e:
        return templates.TemplateResponse(
            request=request, name="erro.html",
            context={"mensagem": str(e)}, status_code=500)
 
    # Quantas tarefas atendem cada requisito: e a metade visivel da
    # rastreabilidade. Zero aqui significa requisito que ninguem vai construir.
    vinculos = {r.id: 0 for r in requisitos}
    for _, tarefa in repo.todas_tarefas():
        for req in tarefa.requisitos:
            if req in vinculos:
                vinculos[req] += 1
 
    secoes = [
        {"titulo": titulo, "tipo": tipo.value,
         "itens": [r for r in requisitos if r.tipo == tipo]}
        for tipo, titulo in SECOES_REQUISITO
    ]
 
    return templates.TemplateResponse(
        request=request, name="requisitos.html",
        context={
            "projeto": repo.projeto(), "eu": eu,
            "editavel": pode_editar(eu),
            "secoes": secoes, "vinculos": vinculos,
            "total": len(requisitos),
            "orfaos": sum(1 for r in requisitos if not vinculos.get(r.id)),
        })
 
 
@app.get("/requisito/novo")
def form_requisito_novo(request: Request, tipo: str = "basica"):
    eu = atual(request)
    if not pode_editar(eu):
        return RedirectResponse("/requisitos", status_code=303)
 
    try:
        tipo_enum = TipoRequisito(tipo)
    except ValueError:
        tipo_enum = TipoRequisito.BASICA
 
    return templates.TemplateResponse(
        request=request, name="requisito_form.html",
        context={
            "projeto": repo.projeto(), "eu": eu, "requisito": None,
            "tipo_novo": tipo_enum,
            "proximo": repo.proximo_id_requisito(tipo_enum),
            "secoes": SECOES_REQUISITO,
        })
 
 
@app.get("/requisito/{requisito_id}/editar")
def form_requisito_editar(request: Request, requisito_id: str):
    eu = atual(request)
    if not pode_editar(eu):
        return RedirectResponse("/requisitos", status_code=303)
 
    try:
        requisito = repo.requisito(requisito_id)
    except ErroRepositorio as e:
        return templates.TemplateResponse(
            request=request, name="erro.html",
            context={"mensagem": str(e)}, status_code=404)
 
    return templates.TemplateResponse(
        request=request, name="requisito_form.html",
        context={
            "projeto": repo.projeto(), "eu": eu, "requisito": requisito,
            "tipo_novo": requisito.tipo, "proximo": requisito.id,
            "secoes": SECOES_REQUISITO,
        })
 
 
@app.post("/requisito/salvar")
def salvar_requisito(
    request: Request,
    requisito_id: str = Form(""),
    tipo: str = Form(...),
    titulo: str = Form(...),
    descricao: str = Form(""),
    entradas: str = Form(""),
    opcionais: str = Form(""),
    saidas: str = Form(""),
    regras: str = Form(""),
    criterios_aceite: str = Form(""),
    prioridade: str = Form("media"),
    origem: str = Form(""),
    status: str = Form("proposto"),
):
    eu = atual(request)
    if not pode_editar(eu):
        return RedirectResponse("/requisitos", status_code=303)
 
    tipo_enum = TipoRequisito(tipo)
    # Na edicao o id nao muda: ele carrega o tipo no prefixo, e renomear
    # quebraria toda tarefa e caso de uso que ja aponta para ele.
    novo_id = requisito_id or repo.proximo_id_requisito(tipo_enum)
 
    requisito = Requisito(
        id=novo_id, tipo=tipo_enum, titulo=titulo, descricao=descricao,
        entradas=_linhas(entradas), opcionais=_linhas(opcionais),
        saidas=_linhas(saidas), regras=_linhas(regras),
        criterios_aceite=_linhas(criterios_aceite),
        prioridade=prioridade, origem=origem, status=status,
    )
    repo.salvar_requisito(requisito)
    return RedirectResponse(f"/requisito/{requisito.id}", status_code=303)
 
 
@app.get("/requisito/{requisito_id}")
def ver_requisito(request: Request, requisito_id: str):
    """Visivel para todos, inclusive o professor."""
    eu = atual(request)
    if eu is None:
        return _para_entrada()
 
    try:
        requisito = repo.requisito(requisito_id)
    except ErroRepositorio as e:
        return templates.TemplateResponse(
            request=request, name="erro.html",
            context={"mensagem": str(e)}, status_code=404)
 
    equipe = repo.equipe()
    tarefas = [
        {"tarefa": t, "dono": equipe.por_id(chave)}
        for chave, t in repo.todas_tarefas()
        if requisito_id in t.requisitos
    ]
 
    return templates.TemplateResponse(
        request=request, name="requisito.html",
        context={
            "projeto": repo.projeto(), "eu": eu,
            "editavel": pode_editar(eu),
            "requisito": requisito,
            "descricao_html": render_markdown(requisito.descricao),
            "tarefas": tarefas,
            "casos": [c for c in repo.casos_de_uso() if requisito_id in c.requisitos],
        })
 
 
@app.post("/requisito/{requisito_id}/excluir")
def excluir_requisito(request: Request, requisito_id: str):
    eu = atual(request)
    if not pode_editar(eu):
        return RedirectResponse("/requisitos", status_code=303)
 
    # Mesma regra da entrega: nao deixar referencia orfa para tras.
    ligadas = [t.id for _, t in repo.todas_tarefas() if requisito_id in t.requisitos]
    if ligadas:
        return templates.TemplateResponse(
            request=request, name="erro.html",
            context={"mensagem":
                     f"{requisito_id} nao pode ser excluido: "
                     f"{len(ligadas)} tarefa(s) apontam para ele "
                     f"({', '.join(ligadas[:8])}). Desvincule-as primeiro."},
            status_code=409)
 
    repo.excluir_requisito(requisito_id)
    return RedirectResponse("/requisitos", status_code=303)

 
def _slug(texto: str) -> str:
    """Nome legivel vira id estavel: 'Usuario Comum' -> 'usuario_comum'."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    limpo = "".join(c if c.isalnum() else "_" for c in sem_acento.lower())
    return "_".join(p for p in limpo.split("_") if p) or "ator"
 
 
@app.get("/ers")
def ver_ers(request: Request):
    """Panorama do documento: o que ja existe e o que falta."""
    eu = atual(request)
    if eu is None:
        return _para_entrada()
 
    try:
        doc = repo.documento()
        atores = repo.atores().atores
        glossario = repo.glossario().termos
        requisitos = repo.requisitos()
        casos = repo.casos_de_uso()
    except ErroRepositorio as e:
        return templates.TemplateResponse(
            request=request, name="erro.html",
            context={"mensagem": str(e)}, status_code=500)
 
    secoes = [
        {"campo": campo, "numero": numero, "titulo": titulo,
         "texto": getattr(doc, campo),
         "palavras": len(getattr(doc, campo).split())}
        for campo, numero, titulo in SECOES_PROSA
    ]
    preenchidas = sum(1 for s in secoes if s["palavras"])
 
    return templates.TemplateResponse(
        request=request, name="ers.html",
        context={
            "projeto": repo.projeto(), "eu": eu,
            "editavel": pode_editar(eu),
            "doc": doc, "secoes": secoes,
            "preenchidas": preenchidas, "total_secoes": len(secoes),
            "atores": atores, "glossario": glossario,
            "requisitos": requisitos, "casos": casos,
            "por_tipo": {
                tipo.value: sum(1 for r in requisitos if r.tipo == tipo)
                for tipo, _ in SECOES_REQUISITO
            },
            "imagens": sum(len(v) for v in doc.imagens.values()),
        })
 
 
@app.get("/ers/editar")
def form_ers(request: Request):
    eu = atual(request)
    if not pode_editar(eu):
        return RedirectResponse("/ers", status_code=303)
    return templates.TemplateResponse(
        request=request, name="ers_form.html",
        context={"projeto": repo.projeto(), "eu": eu,
                 "doc": repo.documento(), "secoes": SECOES_PROSA})
 
 
@app.post("/ers/salvar")
async def salvar_ers(request: Request):
    """Le os campos dinamicamente: a lista de secoes mora em SECOES_PROSA.
 
    Declarar oito parametros Form aqui obrigaria a mexer em dois lugares
    toda vez que uma secao entrasse ou saisse do documento.
    """
    eu = atual(request)
    if not pode_editar(eu):
        return RedirectResponse("/ers", status_code=303)
 
    formulario = await request.form()
    doc = repo.documento()
    for campo, _, _ in SECOES_PROSA:
        setattr(doc, campo, formulario.get(campo, ""))
    doc.referencias = _linhas(formulario.get("referencias", ""))
    repo.salvar_documento(doc)
    return RedirectResponse("/ers", status_code=303)
 
 
# ---------------------------------------------------------------------------
# Atores
# ---------------------------------------------------------------------------
 
 
@app.get("/ers/atores")
def ver_atores(request: Request):
    eu = atual(request)
    if eu is None:
        return _para_entrada()
    return templates.TemplateResponse(
        request=request, name="atores.html",
        context={"projeto": repo.projeto(), "eu": eu,
                 "editavel": pode_editar(eu),
                 "atores": repo.atores().atores,
                 "casos": repo.casos_de_uso()})
 
 
@app.post("/ers/ator/salvar")
def salvar_ator(
    request: Request,
    ator_id: str = Form(""),
    nome: str = Form(...),
    descricao: str = Form(""),
    frequencia_uso: str = Form(""),
    nivel_instrucao: str = Form(""),
    proficiencia: str = Form(""),
):
    eu = atual(request)
    if not pode_editar(eu):
        return RedirectResponse("/ers/atores", status_code=303)
 
    lista = repo.atores()
    novo = Ator(id=ator_id or _slug(nome), nome=nome, descricao=descricao,
                frequencia_uso=frequencia_uso, nivel_instrucao=nivel_instrucao,
                proficiencia=proficiencia)
    lista.atores = [a for a in lista.atores if a.id != novo.id] + [novo]
    repo.salvar_atores(lista)
    return RedirectResponse("/ers/atores", status_code=303)
 
 
@app.post("/ers/ator/excluir")
def excluir_ator(request: Request, ator_id: str = Form(...)):
    eu = atual(request)
    if not pode_editar(eu):
        return RedirectResponse("/ers/atores", status_code=303)
 
    usados = [c.id for c in repo.casos_de_uso() if c.ator_principal == ator_id]
    if usados:
        return templates.TemplateResponse(
            request=request, name="erro.html",
            context={"mensagem":
                     f"ator '{ator_id}' e ator principal de {', '.join(usados)}. "
                     "Troque o ator desses casos de uso antes de excluir."},
            status_code=409)
 
    lista = repo.atores()
    lista.atores = [a for a in lista.atores if a.id != ator_id]
    repo.salvar_atores(lista)
    return RedirectResponse("/ers/atores", status_code=303)
 
 
# ---------------------------------------------------------------------------
# Glossario
# ---------------------------------------------------------------------------
 
 
@app.get("/ers/glossario")
def ver_glossario(request: Request):
    eu = atual(request)
    if eu is None:
        return _para_entrada()
    return templates.TemplateResponse(
        request=request, name="glossario.html",
        context={"projeto": repo.projeto(), "eu": eu,
                 "editavel": pode_editar(eu),
                 "termos": repo.glossario().termos})
 
 
@app.post("/ers/termo/salvar")
def salvar_termo(
    request: Request,
    original: str = Form(""),
    termo: str = Form(...),
    definicao: str = Form(""),
):
    eu = atual(request)
    if not pode_editar(eu):
        return RedirectResponse("/ers/glossario", status_code=303)
 
    glossario = repo.glossario()
    chave = (original or termo).lower()
    glossario.termos = [t for t in glossario.termos if t.termo.lower() != chave]
    glossario.termos.append(Termo(termo=termo, definicao=definicao))
    repo.salvar_glossario(glossario)
    return RedirectResponse("/ers/glossario", status_code=303)
 
 
@app.post("/ers/termo/excluir")
def excluir_termo(request: Request, termo: str = Form(...)):
    eu = atual(request)
    if not pode_editar(eu):
        return RedirectResponse("/ers/glossario", status_code=303)
 
    glossario = repo.glossario()
    glossario.termos = [t for t in glossario.termos if t.termo.lower() != termo.lower()]
    repo.salvar_glossario(glossario)
    return RedirectResponse("/ers/glossario", status_code=303)
 
@app.get("/ers/exportar")
def exportar_ers(request: Request):
    """Gera a ERS em .docx a partir dos mesmos JSONs que alimentam as telas.
 
    Nada e gravado em disco: o arquivo e montado em memoria e enviado. Um
    .docx no repositorio ficaria desatualizado no minuto seguinte e ainda
    entraria em conflito de merge a cada geracao.
    """
    eu = atual(request)
    if eu is None:
        return _para_entrada()
 
    try:
        arquivo = exportar.gerar(repo)
    except ErroRepositorio as e:
        return templates.TemplateResponse(
            request=request, name="erro.html",
            context={"mensagem": str(e)}, status_code=500)
 
    projeto = repo.projeto()
    nome = f"ERS - {projeto.nome} - {date.today():%Y-%m-%d}.docx"
 
    return StreamingResponse(
        arquivo,
        media_type=DOCX,
        headers={
            # filename* com UTF-8 preserva acentos do nome do projeto; o
            # filename simples fica como reserva para clientes antigos.
            "Content-Disposition":
                f"attachment; filename=ERS.docx; "
                f"filename*=UTF-8''{quote(nome)}"
        },
    )

def pode_administrar(membro: Optional[Membro]) -> bool:
    """Administracao e do admin. Estes arquivos sao unicos e compartilhados:
    quanto menos gente escreve neles, menos conflito e menos acidente."""
    return membro is not None and membro.perfil == Perfil.ADMIN
 
 
def _so_admin(request: Request):
    """Devolve o membro se ele pode administrar, ou um redirect."""
    eu = atual(request)
    if eu is None:
        return None, _para_entrada()
    if not pode_administrar(eu):
        return None, RedirectResponse("/", status_code=303)
    return eu, None
 
 
@app.get("/admin")
def ver_admin(request: Request):
    eu, saida = _so_admin(request)
    if saida:
        return saida
 
    projeto = repo.projeto()
    return templates.TemplateResponse(
        request=request, name="admin.html",
        context={
            "projeto": projeto, "eu": eu,
            "membros": len(repo.equipe().membros),
            "colunas": len(projeto.colunas),
            "itens_stack": len(repo.stack().itens),
            "revisoes": len(repo.documento().revisoes),
            "sem_senha": [m.nome for m in repo.equipe().membros if not m.senha_hash],
        })
 
 
# ---------------------------------------------------------------------------
# Equipe
# ---------------------------------------------------------------------------
 
 
@app.get("/admin/equipe")
def admin_equipe(request: Request, codigo: str = "", de: str = ""):
    eu, saida = _so_admin(request)
    if saida:
        return saida
 
    contagem = {}
    for chave, _ in repo.todas_tarefas():
        contagem[chave] = contagem.get(chave, 0) + 1
 
    return templates.TemplateResponse(
        request=request, name="admin_equipe.html",
        context={
            "projeto": repo.projeto(), "eu": eu,
            "membros": repo.equipe().membros,
            "papeis": list(Papel), "perfis": list(Perfil),
            "contagem": contagem,
            # Codigo recem-gerado, mostrado uma unica vez nesta resposta.
            "codigo": codigo, "codigo_de": de,
        })
 
 
@app.post("/admin/membro/salvar")
def salvar_membro(
    request: Request,
    membro_id: str = Form(""),
    nome: str = Form(...),
    novo_id: str = Form(""),
    perfil: str = Form("membro"),
    cor: str = Form("#3B82F6"),
    github: str = Form(""),
    papeis: list[str] = Form([]),
):
    eu, saida = _so_admin(request)
    if saida:
        return saida
 
    equipe = repo.equipe()
    # O id e a chave estrangeira do sistema e o nome do arquivo do board:
    # so pode ser definido na criacao.
    identificador = membro_id or _slug(novo_id or nome)
 
    novo = Membro(
        id=identificador, nome=nome, papeis=papeis or [],
        perfil=perfil, cor=cor, github=_ou_nulo(github),
        senha_hash=next((m.senha_hash for m in equipe.membros
                         if m.id == identificador), ""),
    )
    equipe.membros = [m for m in equipe.membros if m.id != identificador] + [novo]
    repo.salvar_equipe(equipe)
 
    # Membro sem arquivo de board nao aparece no quadro e quebra a
    # verificacao de integridade.
    if novo.perfil != Perfil.PROFESSOR:
        caminho = repo.board_dir / f"{novo.id}.json"
        if not caminho.exists():
            repo.salvar_board(novo.id, BoardMembro(responsavel=novo.id, tarefas=[]))
 
    return RedirectResponse("/admin/equipe", status_code=303)
 
 
@app.post("/admin/membro/excluir")
def excluir_membro(request: Request, membro_id: str = Form(...)):
    eu, saida = _so_admin(request)
    if saida:
        return saida
 
    if membro_id == eu.id:
        return templates.TemplateResponse(
            request=request, name="erro.html",
            context={"mensagem": "Voce nao pode excluir a si mesmo."},
            status_code=409)
 
    tarefas = [t.id for chave, t in repo.todas_tarefas() if chave == membro_id]
    if tarefas:
        return templates.TemplateResponse(
            request=request, name="erro.html",
            context={"mensagem":
                     f"'{membro_id}' ainda tem {len(tarefas)} tarefa(s) "
                     f"({', '.join(tarefas[:8])}). Realoque-as antes de excluir."},
            status_code=409)
 
    equipe = repo.equipe()
    equipe.membros = [m for m in equipe.membros if m.id != membro_id]
    repo.salvar_equipe(equipe)
 
    caminho = repo.board_dir / f"{membro_id}.json"
    if caminho.exists():
        caminho.unlink()
 
    return RedirectResponse("/admin/equipe", status_code=303)
 
 
@app.post("/admin/membro/senha")
def definir_senha(request: Request, membro_id: str = Form(...),
                  remover: str = Form("")):
    eu, saida = _so_admin(request)
    if saida:
        return saida
 
    equipe = repo.equipe()
    membro = equipe.por_id(membro_id)
    if membro is None:
        return RedirectResponse("/admin/equipe", status_code=303)
 
    if remover:
        membro.senha_hash = ""
        repo.salvar_equipe(equipe)
        return RedirectResponse("/admin/equipe", status_code=303)
 
    codigo = seguranca.gerar_codigo()
    membro.senha_hash = seguranca.hash_senha(codigo)
    repo.salvar_equipe(equipe)
    # O codigo viaja na URL uma unica vez, para ser copiado. Nao fica
    # guardado em lugar nenhum: so o hash foi gravado.
    return RedirectResponse(
        f"/admin/equipe?codigo={codigo}&de={membro.id}", status_code=303)
 
 
# ---------------------------------------------------------------------------
# Colunas do board
# ---------------------------------------------------------------------------
 
 
@app.get("/admin/colunas")
def admin_colunas(request: Request):
    eu, saida = _so_admin(request)
    if saida:
        return saida
 
    projeto = repo.projeto()
    uso = {c.id: 0 for c in projeto.colunas}
    for _, tarefa in repo.todas_tarefas():
        if tarefa.status in uso:
            uso[tarefa.status] += 1
 
    return templates.TemplateResponse(
        request=request, name="admin_colunas.html",
        context={"projeto": projeto, "eu": eu,
                 "colunas": projeto.colunas, "uso": uso})
 
 
@app.post("/admin/coluna/salvar")
def salvar_coluna(request: Request, coluna_id: str = Form(""),
                  nome: str = Form(...), wip_limite: str = Form("")):
    eu, saida = _so_admin(request)
    if saida:
        return saida
 
    projeto = repo.projeto()
    # O id da coluna e para onde tarefa.status aponta: renomear quebraria
    # todas as tarefas nela. Nome muda a vontade; id, nunca.
    identificador = coluna_id or _slug(nome)
    limite = int(wip_limite) if wip_limite.strip().isdigit() else None
 
    nova = Coluna(id=identificador, nome=nome, wip_limite=limite)
    existentes = [c.id for c in projeto.colunas]
    if identificador in existentes:
        projeto.colunas = [nova if c.id == identificador else c
                           for c in projeto.colunas]
    else:
        projeto.colunas.append(nova)
 
    repo.salvar_projeto(projeto)
    return RedirectResponse("/admin/colunas", status_code=303)
 
 
@app.post("/admin/coluna/mover")
def mover_coluna(request: Request, coluna_id: str = Form(...),
                 direcao: str = Form(...)):
    """A ordem do array E a ordem das colunas na tela."""
    eu, saida = _so_admin(request)
    if saida:
        return saida
 
    projeto = repo.projeto()
    ids = [c.id for c in projeto.colunas]
    if coluna_id in ids:
        i = ids.index(coluna_id)
        j = i - 1 if direcao == "esquerda" else i + 1
        if 0 <= j < len(projeto.colunas):
            projeto.colunas[i], projeto.colunas[j] = projeto.colunas[j], projeto.colunas[i]
            repo.salvar_projeto(projeto)
 
    return RedirectResponse("/admin/colunas", status_code=303)
 
 
@app.post("/admin/coluna/excluir")
def excluir_coluna(request: Request, coluna_id: str = Form(...)):
    eu, saida = _so_admin(request)
    if saida:
        return saida
 
    projeto = repo.projeto()
    if len(projeto.colunas) <= 1:
        return templates.TemplateResponse(
            request=request, name="erro.html",
            context={"mensagem": "O board precisa de pelo menos uma coluna."},
            status_code=409)
 
    tarefas = [t.id for _, t in repo.todas_tarefas() if t.status == coluna_id]
    if tarefas:
        return templates.TemplateResponse(
            request=request, name="erro.html",
            context={"mensagem":
                     f"a coluna '{coluna_id}' tem {len(tarefas)} tarefa(s) "
                     f"({', '.join(tarefas[:8])}). Mova-as antes de excluir."},
            status_code=409)
 
    projeto.colunas = [c for c in projeto.colunas if c.id != coluna_id]
    repo.salvar_projeto(projeto)
    return RedirectResponse("/admin/colunas", status_code=303)
 
 
# ---------------------------------------------------------------------------
# Stack
# ---------------------------------------------------------------------------
 
 
@app.get("/admin/stack")
def admin_stack(request: Request):
    eu, saida = _so_admin(request)
    if saida:
        return saida
    return templates.TemplateResponse(
        request=request, name="admin_stack.html",
        context={"projeto": repo.projeto(), "eu": eu,
                 "stack": repo.stack(), "membros": repo.equipe().membros})
 
 
@app.post("/admin/stack/produto")
def salvar_produto(request: Request, nome: str = Form(...),
                   descricao: str = Form("")):
    eu, saida = _so_admin(request)
    if saida:
        return saida
 
    stack = repo.stack()
    stack.produto = Produto(nome=nome, descricao=descricao)
    repo.salvar_stack(stack)
    return RedirectResponse("/admin/stack", status_code=303)
 
 
@app.post("/admin/stack/item")
def salvar_item_stack(request: Request, original: str = Form(""),
                      camada: str = Form(...), tecnologia: str = Form(...),
                      responsavel: str = Form(""), justificativa: str = Form("")):
    eu, saida = _so_admin(request)
    if saida:
        return saida
 
    stack = repo.stack()
    chave = original or camada
    novo = ItemStack(camada=camada, tecnologia=tecnologia,
                     responsavel=_ou_nulo(responsavel),
                     justificativa=justificativa)
    if chave in [i.camada for i in stack.itens]:
        stack.itens = [novo if i.camada == chave else i for i in stack.itens]
    else:
        stack.itens.append(novo)
 
    repo.salvar_stack(stack)
    return RedirectResponse("/admin/stack", status_code=303)
 
 
@app.post("/admin/stack/excluir")
def excluir_item_stack(request: Request, camada: str = Form(...)):
    eu, saida = _so_admin(request)
    if saida:
        return saida
 
    stack = repo.stack()
    stack.itens = [i for i in stack.itens if i.camada != camada]
    repo.salvar_stack(stack)
    return RedirectResponse("/admin/stack", status_code=303)
 
 
# ---------------------------------------------------------------------------
# Historico de revisoes da ERS
# ---------------------------------------------------------------------------
 
 
@app.get("/admin/revisoes")
def admin_revisoes(request: Request):
    eu, saida = _so_admin(request)
    if saida:
        return saida
    return templates.TemplateResponse(
        request=request, name="admin_revisoes.html",
        context={"projeto": repo.projeto(), "eu": eu,
                 "revisoes": repo.documento().revisoes,
                 "hoje": date.today().isoformat()})
 
 
@app.post("/admin/revisao/salvar")
def salvar_revisao(request: Request, versao: str = Form(...),
                   data: str = Form(...), descricao: str = Form(""),
                   autor: str = Form("")):
    eu, saida = _so_admin(request)
    if saida:
        return saida
 
    doc = repo.documento()
    nova = Revisao(versao=versao, data=data, descricao=descricao,
                   autor=autor or eu.nome)
    doc.revisoes = [r for r in doc.revisoes if r.versao != versao] + [nova]
    doc.revisoes.sort(key=lambda r: r.data)
    repo.salvar_documento(doc)
    return RedirectResponse("/admin/revisoes", status_code=303)
 
 
@app.post("/admin/revisao/excluir")
def excluir_revisao(request: Request, versao: str = Form(...)):
    eu, saida = _so_admin(request)
    if saida:
        return saida
 
    doc = repo.documento()
    doc.revisoes = [r for r in doc.revisoes if r.versao != versao]
    repo.salvar_documento(doc)
    return RedirectResponse("/admin/revisoes", status_code=303)

@app.post("/sincronizar/backup")
def backup_agora(request: Request):
    """Backup manual, para quem nao quer esperar o ciclo automatico."""
    eu = atual(request)
    if not pode_editar(eu):
        return RedirectResponse("/sincronizar", status_code=303)
 
    ok, saida = backup.executar(RAIZ)
    return _tela_git(request, eu,
                     {"acao": "Backup no GitHub", "ok": ok, "saida": saida})

# As secoes do documento que recebem imagem. A ordem e a do documento.
SECOES_IMAGEM = [
    ("topologia", "Topologia de rede", "Estudo de viabilidade (2.6)"),
    ("casos_uso", "Diagrama de casos de uso", "Analise (3.1)"),
    ("atividades", "Diagrama de atividades", "Analise (3.3)"),
    ("classes", "Diagrama de classes", "Analise (3.4)"),
    ("sequencia", "Diagrama de sequencia", "Analise (3.5)"),
    ("der", "Diagrama entidade-relacionamento", "Analise (3.6)"),
    ("prototipo", "Prototipo de telas", "Analise (3.7)"),
]
 
TIPOS_IMAGEM = {"png": "image/png", "jpg": "image/jpeg"}
 
 
@app.get("/ers/imagens")
def ver_imagens(request: Request, erro: str = ""):
    eu = atual(request)
    if eu is None:
        return _para_entrada()
 
    doc = repo.documento()
    secoes = [
        {"id": chave, "titulo": titulo, "onde": onde,
         "figuras": doc.imagens.get(chave, [])}
        for chave, titulo, onde in SECOES_IMAGEM
    ]
 
    return templates.TemplateResponse(
        request=request, name="imagens.html",
        context={"projeto": repo.projeto(), "eu": eu,
                 "editavel": pode_editar(eu), "secoes": secoes, "erro": erro,
                 "total": sum(len(s["figuras"]) for s in secoes)})
 
 
@app.get("/ers/imagem/{arquivo}")
def servir_imagem(arquivo: str):
    """Serve a imagem do disco.
 
    Nao usamos StaticFiles porque a pasta de dados muda de lugar em
    producao (RAIZ_DADOS aponta para o volume), e o mount e resolvido uma
    vez so, na inicializacao.
    """
    try:
        caminho = repo.caminho_imagem(arquivo)
    except ErroRepositorio:
        return JSONResponse({"detalhe": "invalido"}, status_code=400)
 
    if not caminho.exists():
        return JSONResponse({"detalhe": "nao encontrada"}, status_code=404)
 
    tipo = TIPOS_IMAGEM.get(caminho.suffix.lstrip(".").lower(), "application/octet-stream")
    return FileResponse(caminho, media_type=tipo)
 
 
@app.post("/ers/imagem/enviar")
async def enviar_imagem(
    request: Request,
    secao: str = Form(...),
    legenda: str = Form(""),
    imagem: UploadFile = File(...),
):
    eu = atual(request)
    if not pode_editar(eu):
        return RedirectResponse("/ers/imagens", status_code=303)
 
    if secao not in {s[0] for s in SECOES_IMAGEM}:
        return RedirectResponse("/ers/imagens", status_code=303)
 
    try:
        conteudo = await imagem.read()
        arquivo = repo.salvar_imagem(secao, imagem.filename or "", conteudo)
    except ErroRepositorio as e:
        return RedirectResponse(f"/ers/imagens?erro={quote(str(e))}", status_code=303)
 
    doc = repo.documento()
    doc.imagens.setdefault(secao, []).append(
        Figura(arquivo=arquivo, legenda=legenda))
    repo.salvar_documento(doc)
    return RedirectResponse("/ers/imagens", status_code=303)
 
 
@app.post("/ers/imagem/excluir")
def excluir_imagem(request: Request, secao: str = Form(...),
                   arquivo: str = Form(...)):
    eu = atual(request)
    if not pode_editar(eu):
        return RedirectResponse("/ers/imagens", status_code=303)
 
    doc = repo.documento()
    doc.imagens[secao] = [f for f in doc.imagens.get(secao, [])
                          if f.arquivo != arquivo]
    repo.salvar_documento(doc)
    # O arquivo sai depois do JSON: se a ordem fosse inversa e a gravacao
    # falhasse, a ERS ficaria apontando para imagem inexistente.
    repo.excluir_imagem(arquivo)
    return RedirectResponse("/ers/imagens", status_code=303)
 
 
@app.post("/ers/imagem/legenda")
def alterar_legenda(request: Request, secao: str = Form(...),
                    arquivo: str = Form(...), legenda: str = Form("")):
    eu = atual(request)
    if not pode_editar(eu):
        return RedirectResponse("/ers/imagens", status_code=303)
 
    doc = repo.documento()
    for figura in doc.imagens.get(secao, []):
        if figura.arquivo == arquivo:
            figura.legenda = legenda
    repo.salvar_documento(doc)
    return RedirectResponse("/ers/imagens", status_code=303)