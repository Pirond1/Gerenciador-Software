# Gerenciador de Software

Ferramenta interna do grupo para acompanhar o projeto da disciplina de
**Avaliação e Qualidade de Software**: board de tarefas, organização da
equipe e stack de tecnologia.

Os dados ficam em arquivos JSON dentro de `dados/`, versionados no Git.
Não há banco nem servidor compartilhado: **cada pessoa roda o app na
própria máquina, em cima do próprio clone do repositório.** Sincronizar é
dar `git pull` e `git push` como em qualquer outro arquivo do projeto.

---

## Instalação

Precisa de **Python 3.10 ou superior**. Confira com:

```bash
python --version
```

Clone o repositório e instale as dependências:

```bash
git clone <url-do-repositorio>
cd Gerenciador-Software
pip install -r requirements.txt
```

## Rodando

```bash
python servidor.py
```

Abra <http://127.0.0.1:8000> no navegador. Na primeira vez o app pergunta
quem você é — escolha seu nome na lista.

Para parar o servidor: `Ctrl + C` no terminal.

---

## Fluxo de trabalho

O app **não** faz commit por você. O ciclo de um dia normal é:

```bash
git pull            # antes de começar, para pegar o que os outros fizeram
python servidor.py  # trabalha no board
# Ctrl + C
git add dados/
git commit -m "board: move T-014 para revisão"
git push
```

Peque no `git pull` antes de começar. É o que evita editar em cima de uma
versão velha e gerar conflito.

### Antes de commitar

```bash
python validar.py
```

Confere se todos os JSONs estão íntegros. Se acusar erro, corrija antes de
subir — senão o app quebra na máquina dos outros. O board também mostra as
inconsistências numa faixa vermelha no topo.

---

## Sobre a escolha de perfil

Não há senha. A tela inicial só define **qual visão do board você vê** e
assina as alterações que você fizer. Quem tem acesso ao repositório já tem
acesso a todos os dados — isso é uma limitação assumida do projeto, não um
descuido.

Três perfis:

| Perfil | O que vê | Pode editar |
|---|---|---|
| `admin` | board inteiro | sim |
| `membro` | abre nas próprias tarefas | sim |
| `professor` | board inteiro | não |

---

## Estrutura

```
servidor.py           ponto de entrada
validar.py            checagem dos JSONs (também usado no CI)
app/
  models.py           schemas Pydantic — o contrato dos dados
  repositorio.py      única camada que lê e grava em disco
  rotas.py            rotas HTTP
  templates/          telas (Jinja2)
  static/             CSS e JS
dados/
  equipe.json         membros, papéis e perfis
  projeto.json        colunas do board e entregas
  stack.json          tecnologias e justificativas
  board/<pessoa>.json tarefas de cada responsável
  ers/                requisitos (fase final)
```

### Por que um arquivo de board por pessoa

Cada um mexe quase sempre só no próprio arquivo, então dois membros
trabalhando ao mesmo tempo não conflitam. Quando conflitar, os campos
`atualizado_em` e `atualizado_por` de cada tarefa dizem qual versão é a
mais recente.

---

## Problemas comuns

**`ModuleNotFoundError: No module named 'app'`**
Rode a partir da raiz do projeto, não de dentro de `app/`.

**A porta 8000 já está em uso**
Outro servidor ficou aberto. Feche-o, ou troque a porta em `servidor.py`.

**Editei um JSON e o app não mudou**
Atualize a página. Os JSONs são lidos a cada requisição, mas o navegador
pode estar mostrando a versão anterior.

**O VS Code aponta erros de CSS nos templates**
Alarme falso: ele tenta validar `{{ ... }}` do Jinja como CSS. Mude o modo
de linguagem do arquivo para *Jinja HTML*.

**Conflito de merge num arquivo do board**
Abra o arquivo, compare o `atualizado_em` das duas versões da tarefa e
mantenha a mais recente. Depois rode `python validar.py`.