"""
Ponto de entrada.
 
    python servidor.py
 
Em desenvolvimento sobe em 127.0.0.1 com recarga automática. Em produção
(EM_PRODUCAO=1) escuta em todas as interfaces, sem recarga.
 
UM worker apenas, sempre. A trava de escrita do repositório é de thread:
com vários processos, cada um teria a sua trava e duas gravações
simultâneas voltariam a se sobrescrever. Como a carga é de seis pessoas,
um worker sobra.
"""
 
import os
 
import uvicorn
 
if __name__ == "__main__":
    producao = os.environ.get("EM_PRODUCAO") == "1"
 
    uvicorn.run(
        "app.rotas:app",
        host="0.0.0.0" if producao else "127.0.0.1",
        port=int(os.environ.get("PORTA", "8000")),
        reload=not producao,
        workers=1,
        # Atrás de proxy reverso, para o app enxergar o IP e o esquema reais.
        proxy_headers=producao,
        forwarded_allow_ips="*" if producao else None,
    )
 