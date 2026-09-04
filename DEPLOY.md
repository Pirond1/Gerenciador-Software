# Publicar no Fly.io

Provisório: quando o servidor do TPost estiver de pé, o app vira mais um
contêiner lá e este passo some. O `Dockerfile` é o mesmo nos dois casos.

## Uma vez

1. Instale o CLI e faça login:

   ```
   fly auth login
   ```

2. Na raiz do projeto, crie o app sem publicar ainda:

   ```
   fly launch --no-deploy
   ```

   Aceite o `fly.toml` existente. Ajuste o nome se já estiver em uso.

3. Crie o volume — é o disco que sobrevive a cada deploy:

   ```
   fly volumes create dados --region gru --size 1
   ```

4. Cadastre os segredos (não vão para o repositório):

   ```
   fly secrets set SEGREDO_SESSAO=$(python -c "import secrets; print(secrets.token_hex(32))")
   fly secrets set GITHUB_TOKEN=ghp_seu_token_aqui
   fly secrets set REPO_URL=https://github.com/SEU_USUARIO/Gerenciador-Software.git
   ```

5. Publique:

   ```
   fly deploy
   ```

O endereço sai no fim: `https://gerenciador-software.fly.dev`.

## Depois de publicar

- Entre como admin e confira em **Administração** que ninguém está sem
  código de acesso. Enquanto houver, qualquer um entra como qualquer um.
- Em **Sincronizar**, clique em *Fazer backup agora* para validar que o
  token funciona. Não descubra isso só daqui a duas semanas.

## Atualizar o código

```
git push          # envia o código
fly deploy        # reconstrói e publica
```

Os dados não são tocados: eles vivem no volume, não na imagem.

## Como o volume funciona

Na primeira execução o contêiner clona o repositório em `/dados/repo` e o
app passa a ler e gravar ali. Nos reinícios seguintes ele apenas atualiza.
É por isso que o `.git` precisa estar no volume: sem ele, cada reinício
perderia o histórico local e o push seguinte seria recusado.

Consequência prática: **alterações feitas pela tela do app existem no
volume e no GitHub, mas não no seu clone local** até você dar `git pull`.

## Comandos úteis

```
fly logs                  # ver o que está acontecendo
fly ssh console           # entrar na máquina
fly status                # estado da máquina e do volume
```