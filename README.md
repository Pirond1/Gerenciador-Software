# Gerenciador de Software

Ferramenta interna do grupo para acompanhar o projeto da disciplina de
**Avaliação e Qualidade de Software**: board de tarefas, cronograma de
entregas, organização da equipe e stack de tecnologia.

Os dados ficam em arquivos JSON dentro de `dados/`, versionados no Git.
Não há banco nem servidor compartilhado: **cada pessoa roda o app na
própria máquina, em cima do próprio clone do repositório.** Sincronizar é
dar `pull` e `push` — pela tela do app ou pelo editor, como preferir.

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

## As telas

| Tela | O que faz |
|---|---|
| **Board** | Kanban das tarefas. Arraste os cartões entre colunas; clique para ver o detalhe. |
| **Entregas** | Cronograma dos prazos, com progresso de cada uma e marcação de concluída. |
| **Equipe** | Quem é quem, papéis e distribuição da carga de trabalho. |
| **Stack** | Tecnologias por camada, com a justificativa de cada escolha. |
| **Sincronizar** | Baixar e enviar alterações sem sair do app. |

A descrição de cada tarefa aceita **Markdown**, incluindo checklists com
`- [ ]` e `- [x]`.

---

## Fluxo de trabalho

O ciclo de um dia normal:

1. Abra o app e clique no indicador do Git, no topo
2. Se houver commits para baixar, clique em **Baixar do GitHub**
3. Trabalhe no board
4. Ao terminar, volte em **Sincronizar** e clique em **Enviar ao GitHub**

Baixar antes de começar é o que evita trabalhar em cima de versão velha e
gerar conflito. O indicador no topo consulta o servidor sozinho a cada 3
minutos; para resposta imediata, use **Verificar agora**.

Quem preferir o terminal, o equivalente é:

```bash
git pull
# trabalha
git add dados/
git commit -m "board: move T-014 para revisão"
git push
```

### O que o app faz e não faz com o Git

Faz: commit de `dados/`, `pull --ff-only` e `push`.

Não faz: merge, rebase, checkout, resolução de conflito ou `--force`.
Quando o histórico diverge, o app **recusa** e pede resolução manual, em
vez de gerar um merge automático — merge automático aqui escreveria
marcadores de conflito dentro dos JSONs e quebraria o próprio app.

Alterações em **código** nunca são commitadas pelo app. Só `dados/`.

### Validação antes de subir

```bash
python validar.py
```

Confere se todos os JSONs estão íntegros. O app também faz isso sozinho:
inconsistências aparecem numa faixa vermelha no topo do board, e o botão
de enviar fica **bloqueado** enquanto elas existirem.

Um GitHub Actions roda as mesmas checagens a cada push, então JSON
quebrado é barrado antes de estragar o repositório dos outros.

---

## Sobre a escolha de perfil

Não há senha. A tela inicial só define **qual visão do board você vê** e
assina as alterações que você fizer. Quem tem acesso ao repositório já tem
acesso a todos os dados — isso é uma limitação assumida do projeto, não um
descuido.

| Perfil | O que vê | Edita tarefas | Gerencia entregas |
|---|---|---|---|
| `admin` | tudo | sim | sim |
| `membro` | abre nas próprias tarefas | sim | não |
| `professor` | tudo | não | não |

---

## Estrutura

```
servidor.py           ponto de entrada
validar.py            checagem dos JSONs (também usada no CI)
app/
  models.py           schemas Pydantic — o contrato dos dados
  repositorio.py      única camada que lê e grava em disco
  gitinfo.py          leitura e sincronização do Git
  rotas.py            rotas HTTP
  templates/          telas (Jinja2)
  static/             CSS e JS
dados/
  equipe.json         membros, papéis e perfis
  projeto.json        colunas do board e entregas
  stack.json          tecnologias e justificativas
  board/<pessoa>.json tarefas de cada responsável
  ers/                requisitos (fase final)
.github/workflows/    validação automática a cada push
```

### Por que um arquivo de board por pessoa

Cada um mexe quase sempre só no próprio arquivo, então dois membros
trabalhando ao mesmo tempo não conflitam. Quando conflitar, os campos
`atualizado_em` e `atualizado_por` de cada tarefa dizem qual versão é a
mais recente.

---

## Problemas comuns

**`ModuleNotFoundError: No module named 'app'`**
Rode a partir da raiz do projeto, não de dentro de `app/`. Confira também
se o arquivo `app/__init__.py` existe.

**A porta 8000 já está em uso**
Outro servidor ficou aberto. Feche-o, ou troque a porta em `servidor.py`.

**Editei um JSON e o app não mudou**
Atualize a página. Os JSONs são lidos a cada requisição, mas o navegador
pode estar mostrando a versão anterior.

**O indicador do Git não mostra commits para baixar**
Ele compara com a cópia local do remoto, que só atualiza no `fetch`. Use
**Verificar agora** na tela de sincronização.

**O botão de enviar está desabilitado**
Há inconsistências nos dados. Corrija-as — a faixa vermelha no board diz
quais são — e o botão volta.

**O push falhou com erro de credencial**
O app não abre prompt de senha, por segurança. Faça um `git push` pelo
terminal uma vez, para o Windows guardar a credencial.

**O VS Code aponta erros de CSS nos templates**
Alarme falso: ele tenta validar `{{ ... }}` do Jinja como CSS. Mude o modo
de linguagem do arquivo para *Jinja HTML*.

**Conflito de merge num arquivo do board**
Abra o arquivo, compare o `atualizado_em` das duas versões da tarefa e
mantenha a mais recente. Depois rode `python validar.py`.