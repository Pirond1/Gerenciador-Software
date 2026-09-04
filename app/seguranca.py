"""
Senha de acesso e assinatura do cookie de sessão.

Sem dependência nova: tudo com `hashlib` e `hmac` da biblioteca padrão.

Duas coisas diferentes moram aqui:

- **Senha**: derivada com PBKDF2 e guardada apenas como hash em
  `equipe.json`. Como o repositório vai para o GitHub, o hash precisa
  resistir a quem o leia — por isso PBKDF2 com muitas iterações, e por
  isso as senhas são códigos aleatórios gerados pelo sistema, não
  escolhidas pelas pessoas (ninguém reutiliza um código que não escolheu).

- **Cookie**: assinado com HMAC. Antes, o cookie guardava o id do membro
  em texto puro — bastava editá-lo no navegador para virar outra pessoa.
  Num servidor público isso é o mesmo que não ter senha nenhuma.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

ITERACOES = 260_000
ALFABETO = "abcdefghijkmnpqrstuvwxyz23456789"  # sem l, o, 0, 1


# ---------------------------------------------------------------------------
# Senha
# ---------------------------------------------------------------------------


def gerar_codigo(blocos: int = 2, tamanho: int = 4) -> str:
    """Código aleatório legível, no formato k7m2-x9pq."""
    return "-".join(
        "".join(secrets.choice(ALFABETO) for _ in range(tamanho))
        for _ in range(blocos)
    )


def hash_senha(senha: str) -> str:
    """Devolve pbkdf2_sha256$<iteracoes>$<sal>$<hash>."""
    sal = secrets.token_bytes(16)
    derivado = hashlib.pbkdf2_hmac("sha256", senha.encode(), sal, ITERACOES)
    return "$".join([
        "pbkdf2_sha256", str(ITERACOES),
        base64.b64encode(sal).decode(),
        base64.b64encode(derivado).decode(),
    ])


def conferir_senha(senha: str, guardado: str) -> bool:
    """Compara em tempo constante, para não vazar informação pelo tempo."""
    try:
        algoritmo, iteracoes, sal, esperado = guardado.split("$")
        if algoritmo != "pbkdf2_sha256":
            return False
        derivado = hashlib.pbkdf2_hmac(
            "sha256", senha.encode(), base64.b64decode(sal), int(iteracoes)
        )
        return hmac.compare_digest(derivado, base64.b64decode(esperado))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Cookie assinado
# ---------------------------------------------------------------------------


def chave_secreta() -> bytes:
    """Chave de assinatura, vinda do ambiente.

    Sem a variável, gera uma chave aleatória a cada inicialização: as
    sessões caem quando o servidor reinicia, o que é inconveniente mas
    seguro. Em produção, defina SEGREDO_SESSAO.
    """
    segredo = os.environ.get("SEGREDO_SESSAO")
    return segredo.encode() if segredo else secrets.token_bytes(32)


CHAVE = chave_secreta()


def assinar(valor: str) -> str:
    assinatura = hmac.new(CHAVE, valor.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{valor}.{assinatura}"


def conferir_assinatura(assinado: str | None) -> str | None:
    """Devolve o valor se a assinatura confere; None caso contrário."""
    if not assinado or "." not in assinado:
        return None
    valor, _, assinatura = assinado.rpartition(".")
    esperada = hmac.new(CHAVE, valor.encode(), hashlib.sha256).hexdigest()[:32]
    return valor if hmac.compare_digest(assinatura, esperada) else None