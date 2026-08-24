"""
Ponto de entrada. Rode com:  python app.py

Sobe apenas em 127.0.0.1 de proposito: a ferramenta e local, cada um roda
a sua sobre o proprio clone do repositorio.
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.rotas:app", host="127.0.0.1", port=8000, reload=True)