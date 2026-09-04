#!/bin/sh
# Prepara o volume persistente antes de subir o app.
#
# O volume guarda um CLONE do repositório, não apenas a pasta dados/. Sem
# o .git junto, cada reinício voltaria o histórico ao estado da imagem e o
# push seguinte seria recusado por divergência — o backup pararia sozinho.
set -e

DESTINO="${RAIZ_DADOS:-/dados/repo}"

if [ -z "$REPO_URL" ]; then
    echo "REPO_URL não definido; usando os dados da própria imagem."
    exec python servidor.py
fi

# Token só na URL de trabalho, nunca gravado no .git/config.
URL="$REPO_URL"
if [ -n "$GITHUB_TOKEN" ]; then
    URL=$(echo "$REPO_URL" | sed "s#https://#https://x-access-token:${GITHUB_TOKEN}@#")
fi

if [ ! -d "$DESTINO/.git" ]; then
    echo "primeira execução: clonando o repositório em $DESTINO"
    mkdir -p "$DESTINO"
    git clone "$URL" "$DESTINO"
    git -C "$DESTINO" remote set-url origin "$REPO_URL"
else
    echo "volume já preparado; buscando novidades"
    # --ff-only: se o histórico divergiu, prefere falhar a criar merge.
    git -C "$DESTINO" pull --ff-only "$URL" || \
        echo "aviso: não foi possível atualizar; seguindo com o que está no volume"
fi

git -C "$DESTINO" config user.name "${GIT_AUTOR:-Gerenciador}"
git -C "$DESTINO" config user.email "${GIT_EMAIL:-gerenciador@local}"

exec python servidor.py