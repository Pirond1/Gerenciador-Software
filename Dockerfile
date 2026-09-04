FROM python:3.12-slim

# git e' dependencia de execucao, nao so de build: o app le o estado do
# repositorio e faz o backup automatico.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x entrypoint.sh

ENV EM_PRODUCAO=1 \
    PORTA=8000 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["./entrypoint.sh"]