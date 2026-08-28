"""
Leitura e sincronizacao do Git.

Escopo deliberadamente estreito: commit de `dados/`, pull apenas
fast-forward e push simples. Nada de merge automatico, rebase, checkout
ou force. Quando o Git precisar de decisao humana, o app recusa e devolve
a mensagem original -- e a pessoa resolve no editor.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

# Sem isso, um push que precise de credencial abre um prompt invisivel e o
# subprocess trava ate o timeout. Assim ele falha na hora, com mensagem.
AMBIENTE = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

# De quanto em quanto tempo consultamos o servidor por commits novos.
INTERVALO_FETCH = 180  # segundos

_trava = threading.Lock()
_buscando = False


def _executar(raiz: Path, *args: str, timeout: int = 30) -> tuple[bool, str]:
    """Roda um comando git e devolve (deu_certo, saida_combinada)."""
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=raiz,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=AMBIENTE,
        )
    except FileNotFoundError:
        return False, "git nao encontrado no sistema"
    except subprocess.TimeoutExpired:
        return False, "o comando demorou demais e foi interrompido"
    except OSError as e:
        return False, str(e)

    saida = (r.stdout + r.stderr).strip()
    return r.returncode == 0, saida


def _ler(raiz: Path, *args: str) -> str | None:
    ok, saida = _executar(raiz, *args, timeout=5)
    return saida if ok else None


# ---------------------------------------------------------------------------
# Consulta ao servidor
#
# `@{u}` compara com a copia LOCAL de origin/<ramo>, que so muda quando
# alguem roda `git fetch`. Sem fetch, o contador "para baixar" fica preso
# em zero mesmo havendo commits novos no GitHub.
# ---------------------------------------------------------------------------


def idade_do_fetch(raiz: Path) -> float | None:
    """Segundos desde o ultimo fetch, ou None se nunca houve.

    O proprio Git ja registra isso na data de modificacao do FETCH_HEAD --
    nao precisamos guardar estado nosso em lugar nenhum.
    """
    dir_git = _ler(raiz, "rev-parse", "--absolute-git-dir")
    if not dir_git:
        return None
    marca = Path(dir_git) / "FETCH_HEAD"
    if not marca.exists():
        return None
    return time.time() - marca.stat().st_mtime


def _buscar_em_segundo_plano(raiz: Path) -> None:
    """Dispara um fetch sem a pagina esperar por ele.

    Fetch e chamada de rede: bloquear o carregamento por ela deixaria toda
    a interface lenta, e travaria a tela inteira quando a internet caisse.
    O preco e que a contagem aparece no proximo carregamento, nao neste.
    """
    global _buscando
    with _trava:
        if _buscando:
            return  # ja tem um rodando; nao empilha
        _buscando = True

    def tarefa() -> None:
        global _buscando
        try:
            _executar(raiz, "fetch", "--quiet", timeout=30)
        finally:
            with _trava:
                _buscando = False

    threading.Thread(target=tarefa, daemon=True).start()


def verificar_agora(raiz: Path) -> tuple[bool, str]:
    """Fetch bloqueante, para o botao 'Verificar agora'."""
    ok, saida = _executar(raiz, "fetch", timeout=45)
    return ok, (saida or "Consulta concluida.")


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


def estado(raiz: Path) -> dict | None:
    """Resumo do repositorio, ou None se nao houver um utilizavel."""
    ramo = _ler(raiz, "rev-parse", "--abbrev-ref", "HEAD")
    if ramo is None:
        return None

    saida = _ler(raiz, "status", "--porcelain", "--", "dados") or ""
    alteracoes = [linha for linha in saida.splitlines() if linha.strip()]

    tem_upstream = _ler(raiz, "rev-parse", "--abbrev-ref", "@{u}") is not None
    frente = atras = 0
    idade = None

    if tem_upstream:
        idade = idade_do_fetch(raiz)
        if idade is None or idade > INTERVALO_FETCH:
            _buscar_em_segundo_plano(raiz)

        contagem = _ler(raiz, "rev-list", "--left-right", "--count", "@{u}...HEAD")
        if contagem and "\t" in contagem:
            try:
                a, f = contagem.split("\t")
                atras, frente = int(a), int(f)
            except ValueError:
                pass

    return {
        "ramo": ramo,
        "arquivos": [linha[3:] for linha in alteracoes],
        "alteracoes": len(alteracoes),
        "frente": frente,
        "atras": atras,
        "tem_upstream": tem_upstream,
        "idade_fetch": idade,
        "limpo": not alteracoes and not frente and not atras,
    }


# ---------------------------------------------------------------------------
# Escrita
# ---------------------------------------------------------------------------


def baixar(raiz: Path) -> tuple[bool, str]:
    """git pull --ff-only.

    O --ff-only e o que torna isso seguro: se o historico divergiu, o Git
    recusa em vez de criar um merge. Merge automatico aqui escreveria
    marcadores de conflito dentro dos JSONs, quebrando o proprio app.
    """
    ok, saida = _executar(raiz, "pull", "--ff-only", timeout=45)
    if ok:
        return True, saida or "Ja estava atualizado."
    return False, (
        "Nao foi possivel baixar sem merge. Isso acontece quando voce e o "
        "remoto tem commits diferentes, ou quando ha alteracoes locais nao "
        "commitadas nos mesmos arquivos. Resolva pelo VS Code ou pelo "
        f"terminal.\n\n{saida}"
    )


def commitar(raiz: Path, mensagem: str) -> tuple[bool, str]:
    """Adiciona e commita apenas dados/. Codigo fica de fora, de proposito."""
    ok, saida = _executar(raiz, "add", "--", "dados")
    if not ok:
        return False, saida

    ok, saida = _executar(raiz, "commit", "-m", mensagem, "--", "dados")
    if ok:
        return True, saida
    if "nothing to commit" in saida or "nada a submeter" in saida:
        return True, "Nenhuma alteracao em dados/ para commitar."
    return False, saida


def enviar(raiz: Path) -> tuple[bool, str]:
    ok, saida = _executar(raiz, "push", timeout=60)
    if ok:
        return True, saida or "Enviado."
    return False, saida


def mensagem_padrao(nome: str) -> str:
    return f"dados: atualizacao de {nome} em {datetime.now():%d/%m %H:%M}"