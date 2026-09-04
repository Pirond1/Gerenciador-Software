"""
Backup automático dos dados no GitHub.

Com o app num servidor, o Git deixa de ser meio de sincronização e passa
a ser rede de segurança: se alguém excluir uma tarefa por engano ou o
servidor pifar, o histórico do repositório é o que recupera.

Roda numa thread separada, em intervalo configurável. Só commita
`dados/`, e só quando há algo mudado.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from pathlib import Path

from app import gitinfo

INTERVALO = int(os.environ.get("BACKUP_MINUTOS", "0"))
TOKEN = os.environ.get("GITHUB_TOKEN", "")
AUTOR = os.environ.get("GIT_AUTOR", "Gerenciador")
EMAIL = os.environ.get("GIT_EMAIL", "gerenciador@local")

_estado = {"ultimo": None, "ultima_mensagem": "", "ok": None}
_trava = threading.Lock()


def estado() -> dict:
    with _trava:
        return dict(_estado)


def _com_token(raiz: Path) -> str | None:
    """Monta a URL de push com o token, sem gravá-la no repositório.

    O `git remote set-url` deixaria o token dentro de .git/config, que é
    fácil de vazar num backup ou numa cópia da pasta. Passar a URL a cada
    push mantém o segredo apenas na variável de ambiente.
    """
    if not TOKEN:
        return None
    url = gitinfo._ler(raiz, "remote", "get-url", "origin") or ""
    if url.startswith("https://github.com/"):
        return url.replace("https://", f"https://x-access-token:{TOKEN}@", 1)
    return None


def executar(raiz: Path) -> tuple[bool, str]:
    """Um ciclo de backup. Devolve (deu_certo, mensagem)."""
    if not gitinfo.estado(raiz):
        return False, "a pasta não é um repositório git"

    pendente = gitinfo._ler(raiz, "status", "--porcelain", "--", "dados") or ""
    if not pendente.strip():
        _registrar(True, "nada para salvar")
        return True, "nada para salvar"

    # Identidade só para os commits automáticos, sem alterar a config
    # global da máquina.
    gitinfo._executar(raiz, "-c", f"user.name={AUTOR}", "-c", f"user.email={EMAIL}",
                      "add", "--", "dados")
    ok, saida = gitinfo._executar(
        raiz, "-c", f"user.name={AUTOR}", "-c", f"user.email={EMAIL}",
        "commit", "-m", f"backup automático {datetime.now():%d/%m %H:%M}",
        "--", "dados")
    if not ok and "nothing to commit" not in saida:
        _registrar(False, f"falha no commit: {saida}")
        return False, saida

    url = _com_token(raiz)
    if url is None:
        _registrar(False, "sem GITHUB_TOKEN: commit local feito, push não")
        return False, "sem token configurado"

    ok, saida = gitinfo._executar(raiz, "push", url, "HEAD", timeout=60)
    # A saída do push pode conter a URL com o token: nunca registrar crua.
    limpa = saida.replace(TOKEN, "***") if TOKEN else saida
    _registrar(ok, "backup enviado" if ok else f"falha no push: {limpa}")
    return ok, limpa


def _registrar(ok: bool, mensagem: str) -> None:
    with _trava:
        _estado.update({"ultimo": datetime.now(), "ok": ok,
                        "ultima_mensagem": mensagem})


def iniciar(raiz: Path) -> None:
    """Dispara o ciclo em segundo plano, se configurado."""
    if INTERVALO <= 0:
        return

    def laco() -> None:
        while True:
            time.sleep(INTERVALO * 60)
            try:
                executar(raiz)
            except Exception as e:            # nunca derrubar a thread
                _registrar(False, f"erro inesperado: {e}")

    threading.Thread(target=laco, daemon=True).start()