# ProjectLecture

Leitor comunitário de documentos com Django, API REST interna, MySQL, Celery/Redis e interface baseada no SB Admin. Transforma texto, PDF, DOCX, EPUB ou TXT em áudio neural, reproduz no navegador e salva automaticamente o ponto de leitura.

## O que já funciona

- cadastro público por e-mail, login Google opcional e isolamento da biblioteca por usuário;
- criação por texto colado ou upload de PDF, DOCX, EPUB e TXT;
- importação pontual de PDF, DOCX, EPUB, TXT e Google Docs pelo Google Drive;
- exportação EPUB compatível com o fluxo oficial Enviar para Kindle;
- extração de texto com PyMuPDF, python-docx e EbookLib;
- geração progressiva e assíncrona em pequenos blocos com Celery;
- quatro vozes neurais brasileiras do Azure Speech quando uma conta F0 é configurada;
- TTS neural local alternativo com Kokoro-82M, sem chave externa ou cobrança por caractere;
- catálogo automático: Azure configurado usa Francisca, Antonio, Thalita e Donato;
- amostras de voz sob demanda e fallback leve com `espeak-ng`;
- modo acadêmico que prepara abreviações, referências e símbolos para a fala;
- player com play/pause, avanço/retrocesso e velocidade;
- destaque palavra a palavra, clique no texto para iniciar ou pausar e troca
  transparente entre os blocos;
- retomada automática pelo caractere exato, mesmo após sair da página;
- API REST autenticada para documentos, vozes e progresso;
- painel responsivo baseado no SB Admin/Bootstrap 5;
- MySQL 8.4 e Redis em Docker.

## Executar com Docker

Pré-requisitos: Git, Docker Engine 24+ e Docker Compose v2.

```bash
git clone https://github.com/AdrianoCarvalh0/ProjectLecture.git
cd ProjectLecture
cp .env.example .env
docker compose up --build
```

Acesse `http://localhost:8000`, crie uma conta e adicione um texto curto. O worker gera o áudio em segundo plano e a página do documento atualiza automaticamente.

No primeiro `up`, o container `web` executa automaticamente:

1. todas as migrations do Django;
2. a coleta dos arquivos estáticos;
3. a criação ou atualização das quatro vozes iniciais.

Os dados de desenvolvimento ficam em volumes Docker, e não no repositório. O
arquivo `.env` também não é versionado; apenas o `.env.example` deve ir para o
Git. Antes de uma publicação externa, altere pelo menos
`DJANGO_SECRET_KEY`, `MYSQL_PASSWORD`, `MYSQL_ROOT_PASSWORD`,
`DJANGO_ALLOWED_HOSTS` e `DJANGO_CSRF_TRUSTED_ORIGINS`.

Cada conta gratuita pode manter 50 documentos e criar até 10 por dia por
padrão. Ajuste `MAX_DOCUMENTS_PER_USER` e
`MAX_DOCUMENTS_PER_USER_PER_DAY` no `.env` se necessário.

## Login Google e arquivos do Drive

No [Google Cloud Console](https://console.cloud.google.com/), use um único
projeto para:

1. configurar a tela de consentimento OAuth;
2. ativar **Google Drive API** e **Google Picker API**;
3. criar um cliente OAuth do tipo **Aplicativo da Web**;
4. criar uma chave de API para o Picker, restrita às origens do ProjectLecture
   e somente às APIs usadas.

Para desenvolvimento, cadastre a origem JavaScript
`http://localhost:8000` e a URI de redirecionamento:

```text
http://localhost:8000/contas/google/login/callback/
```

Em produção, cadastre também a origem e o callback HTTPS reais, por exemplo:

```text
https://projectlecture-0ff1c83a33.northcentralus.cloudapp.azure.com
https://projectlecture-0ff1c83a33.northcentralus.cloudapp.azure.com/contas/google/login/callback/
```

Preencha sem versionar os valores:

```env
GOOGLE_OAUTH_CLIENT_ID=cliente.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=segredo-oauth
GOOGLE_DRIVE_API_KEY=chave-restrita-do-picker
GOOGLE_CLOUD_PROJECT_NUMBER=123456789012
```

O login solicita somente perfil e e-mail. O Drive usa uma autorização separada
com o escopo `drive.file`: o usuário escolhe um arquivo no seletor oficial, o
servidor baixa esse arquivo com um token temporário e não guarda o token.
O cabeçalho `Cross-Origin-Opener-Policy` deve permanecer como
`same-origin-allow-popups` para que a janela OAuth consiga devolver o token à
página do ProjectLecture.

## Enviar uma leitura ao Kindle

Na página do documento, o botão **Kindle** baixa uma versão EPUB. Depois, envie
o arquivo pelo [Enviar para Kindle](https://www.amazon.com/sendtokindle). A
Amazon não fornece uma API pública para o ProjectLecture ler ou alterar a
posição de leitura do Kindle; portanto, nesta versão o progresso salvo no
ProjectLecture e o progresso mantido pela Amazon permanecem separados.

Sem credenciais do Azure, o catálogo usa as vozes locais Kokoro. No primeiro uso
de uma delas, o serviço baixa seus arquivos para o volume persistente
`model_cache`. Essa primeira amostra pode demorar; as próximas reutilizam o
modelo e o cache.

## Vozes Azure Speech no nível gratuito

Crie um recurso oficial Azure Speech no nível **Free F0** e acrescente ao `.env`:

```env
AZURE_SPEECH_KEY=chave-do-recurso
AZURE_SPEECH_REGION=brazilsouth
AZURE_SPEECH_ENDPOINT=https://brazilsouth.api.cognitive.microsoft.com/
AZURE_SPEECH_TIER=F0
```

Recrie os containers para instalar o SDK e atualizar o catálogo:

```bash
docker compose up -d --build
```

A chave é usada somente no servidor e nunca é enviada ao navegador. O `.env`
real é ignorado pelo Git. Com Azure ativo, os quatro narradores disponíveis são
Francisca, Antonio, Thalita e Donato; sem a chave, o sistema volta ao catálogo
Kokoro local na próxima execução de `python manage.py seed_voices`.

O Azure retorna limites de palavra junto com a síntese. O ProjectLecture liga a
pausa de vírgulas e pontos à palavra anterior, mantendo o destaque sincronizado.

O ambiente padrão instala o PyTorch para CPU, reduzindo bastante a imagem. Em um host com NVIDIA Container Toolkit configurado, o override instala a variante CUDA:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build
```

Para criar um administrador:

```bash
docker compose exec web python manage.py createsuperuser
```

## Acesso temporário pela internet com ngrok

O overlay `docker-compose.ngrok.yml` publica o container web sem expor MySQL,
Redis ou o serviço neural. Por segurança, o cadastro público é desativado no
túnel; use uma conta já criada.

1. Crie uma conta gratuita no ngrok e copie seu authtoken.
2. Salve-o somente no `.env` local:

```env
NGROK_AUTHTOKEN=seu-token
```

3. Suba o túnel e consulte a URL:

```bash
./scripts/ngrok-start.sh
```

O painel local do agente fica restrito a `http://127.0.0.1:4040`. Para encerrar:

```bash
docker compose -f docker-compose.yml -f docker-compose.ngrok.yml stop ngrok
docker compose up -d --force-recreate web
```

O ngrok é apenas uma entrada temporária de desenvolvimento. A URL gratuita pode
mudar quando o container reiniciar.

## Produção econômica no Azure for Students

O arquivo `docker-compose.prod.yml` foi dimensionado para uma VM pequena:

- Django/Gunicorn com um processo e duas threads;
- worker Celery com concorrência 1;
- MySQL e Redis limitados para pouca memória;
- Azure Speech F0, sem o container Kokoro em produção;
- Caddy como proxy reverso e HTTPS automático;
- volumes persistentes para banco, mídia, fila e certificados;
- cadastro público gratuito com limites por conta e por dia;
- backup diário de banco e mídia, com retenção padrão de sete dias.

A infraestrutura Bicep cria uma VM Ubuntu 24.04 `Standard_B1s`, disco Standard
LRS de 64 GB, IP público com DNS, regras 80/443 e SSH restrito ao IP do
administrador. O `cloud-init` instala Docker, cria 2 GB de swap e registra os
serviços de inicialização e backup.

Pré-requisitos na máquina de administração ou no Azure Cloud Shell:

- Azure CLI autenticado;
- `ssh`, `scp`, `rsync`, `curl` e `openssl`;
- assinatura chamada `Azure for Students`.

Provisionamento:

```bash
az login
export AZURE_BUDGET_EMAIL=seu-email@instituicao.br
./scripts/azure/provision.sh
./scripts/azure/create-budget.sh
./scripts/azure/deploy-app.sh
```

O provisionamento:

1. seleciona a assinatura `Azure for Students`;
2. cria `rg-projectlecture-prod` em `brazilsouth`;
3. descobre seu IP público e libera SSH somente para `/32`;
4. cria uma chave SSH exclusiva em
   `~/.ssh/projectlecture_azure`, se necessário;
5. implanta `infra/azure/main.bicep`;
6. gera `.env.prod` com segredos aleatórios e copia a chave Azure Speech sem
   imprimi-la;
7. envia o projeto para `/opt/projectlecture` e sobe a composição;
8. configura HTTPS no endereço
   `https://<nome>.brazilsouth.cloudapp.azure.com`.

Se `.env.prod` já existia antes do cadastro público, altere-o manualmente:

```bash
sed -i 's/^ALLOW_PUBLIC_REGISTRATION=.*/ALLOW_PUBLIC_REGISTRATION=1/' .env.prod
```

Acrescente as quatro variáveis Google ao mesmo arquivo para ativar os botões de
login e Drive e execute novamente `./scripts/azure/deploy-app.sh`.

Variáveis opcionais:

```bash
export AZURE_RESOURCE_GROUP=rg-projectlecture-prod
export AZURE_LOCATION=brazilsouth
export AZURE_VM_SIZE=Standard_B1s
export AZURE_ADMIN_CIDR=203.0.113.10/32
export AZURE_DNS_LABEL=projectlecture-meu-identificador
export AZURE_MONTHLY_BUDGET=8
```

Se uma região estiver temporariamente sem capacidade para `Standard_B1s`, tente
o tamanho gratuito AMD `Standard_B2ats_v2` ou selecione outra região. O script
passa `AZURE_LOCATION` explicitamente ao Bicep, mesmo quando o grupo de recursos
já existe em outra região; a localização do grupo não é alterada:

```bash
./scripts/azure/find-vm-option.sh
# Execute a combinação sugerida, por exemplo:
AZURE_VM_SIZE=Standard_B2ats_v2 ./scripts/azure/provision.sh
AZURE_LOCATION=eastus2 AZURE_VM_SIZE=Standard_B1s ./scripts/azure/provision.sh
```

O verificador executa apenas a validação ARM nas famílias gratuitas x86 e não
cria recursos. Em assinaturas estudantis ele também tenta ler a atribuição
`Allowed resource deployment regions`, pois as regiões permitidas variam por
assinatura.

Depois do primeiro deploy, crie o administrador:

```bash
ssh -i ~/.ssh/projectlecture_azure azureuser@SEU_FQDN
cd /opt/projectlecture
docker compose --env-file .env.prod -f docker-compose.prod.yml exec web \
  python manage.py createsuperuser
```

Operações úteis na VM:

```bash
cd /opt/projectlecture
docker compose --env-file .env.prod -f docker-compose.prod.yml ps
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f web worker
./scripts/prod/backup.sh
systemctl list-timers projectlecture-backup.timer
```

O nível gratuito de VMs oferecido a clientes novos vale por 12 meses. A
renovação estudantil recompõe o crédito anual, mas não deve ser tratada como uma
renovação automática da franquia promocional da VM. O IP público e qualquer uso
fora das franquias também podem consumir crédito; mantenha o orçamento mensal e
os alertas ativos.

Para aplicar migrations manualmente ou conferir seu estado:

```bash
docker compose exec web python manage.py migrate
docker compose exec web python manage.py showmigrations
```

Para acompanhar a geração:

```bash
docker compose logs -f worker
```

Para encerrar sem apagar os dados:

```bash
docker compose down
```

Os volumes `mysql_data`, `redis_data`, `media_data`, `static_data` e `model_cache` mantêm banco, fila, documentos, arquivos gerados e modelos neurais.

## API interna

A API navegável está em `http://localhost:8000/api/` e usa a sessão do Django.

Endpoints principais:

| Método | Endpoint | Uso |
| --- | --- | --- |
| `GET`, `POST` | `/api/documents/` | listar ou criar documento |
| `GET`, `DELETE` | `/api/documents/{id}/` | consultar ou excluir |
| `POST` | `/api/documents/{id}/generate/` | gerar novamente |
| `GET` | `/api/documents/{id}/stream/` | manifesto dos blocos e tempos por palavra |
| `GET`, `PATCH` | `/api/documents/{id}/progress/` | ler ou salvar posição |
| `GET` | `/api/voices/` | listar vozes disponíveis |

Exemplo de criação autenticada pelo navegador:

```json
{
  "title": "Lei de exemplo",
  "text": "Art. 1º Este é um texto de demonstração.",
  "voice": 1,
  "speed": 170,
  "reading_mode": "academic"
}
```

Uploads usam `multipart/form-data` com o campo `original_file` no lugar de `text`.

## Testes

```bash
docker compose exec web python manage.py test
docker compose exec web python manage.py makemigrations --check --dry-run
```

Para usar SQLite e tarefas síncronas fora do Docker:

```bash
DB_ENGINE=sqlite CELERY_TASK_ALWAYS_EAGER=1 python manage.py test
```

O workflow em `.github/workflows/ci.yml` repete essas verificações em cada
`push` e pull request.

## Desenvolvimento sem Docker

O Docker continua sendo o caminho recomendado porque MySQL, Redis, `espeak-ng`
e as bibliotecas de áudio já ficam configurados. Para executar somente o Django
com SQLite:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
DB_ENGINE=sqlite python manage.py migrate
DB_ENGINE=sqlite CELERY_TASK_ALWAYS_EAGER=1 python manage.py runserver
```

O serviço neural tem dependências próprias em
`neural_tts/requirements.txt`. O `Dockerfile.neural` instala o PyTorch pelo
índice adequado para CPU ou CUDA antes dessas dependências.

## Antes de enviar ao Git

```bash
docker compose config --quiet
docker compose exec web python manage.py makemigrations --check --dry-run
docker compose exec web python manage.py test
git status --short
```

Arquivos que devem permanecer fora do commit incluem `.env`, bancos SQLite,
uploads em `media/`, estáticos coletados, caches Python e modelos baixados.

## Arquitetura

```text
Navegador / API interna
          |
        Django  ───────── MySQL
          |
        Redis
          |
        Celery ── preparação acadêmica ──┬── Azure Speech F0
                                         └── Kokoro local
                                                  |
                                     WAVs curtos + tempos por palavra
```

O TTS fica atrás de uma interface em `reader/services/tts.py`. O Kokoro roda num container FastAPI separado, portanto modelos mais pesados como Chatterbox podem ser adicionados como outro provedor sem alterar biblioteca, fila, player ou progresso.

O navegador recebe os trechos conforme ficam prontos e pré-carrega o seguinte.
No Azure, os tempos por palavra vêm dos eventos de síntese do próprio serviço.
Nas vozes Kokoro, vêm das durações fonéticas previstas pelo modelo. Vozes sem
essa informação usam uma estimativa ponderada como fallback.

## Avaliar qualidade e desempenho

Para gerar o mesmo trecho acadêmico com todas as vozes e medir o fator de tempo real:

```bash
docker compose exec web python manage.py benchmark_voices
```

As amostras ficam em `media/benchmarks/`. Para avaliação de qualidade, compare naturalidade, inteligibilidade de siglas e referências, fadiga após alguns minutos e consistência de ritmo. O tempo de síntese não mede qualidade, mas ajuda a decidir entre CPU e GPU.

## Modelos e licenças

- [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M): Apache 2.0, com vozes brasileiras `pf_dora`, `pm_alex` e `pm_santa`.
- `espeak-ng`: fallback livre e leve para máquinas que não comportam o serviço neural.
- os avatares locais Lia, Caio e Ravi foram gerados especialmente para este projeto e não representam pessoas reais;
- Microsoft Azure Speech SDK: integração opcional com o recurso configurado pelo operador.

## Limites desta versão

- A primeira síntese neural precisa baixar o modelo e é mais lenta.
- O Azure Speech envia cada trecho preparado ao recurso Azure configurado e está
  sujeito aos limites mensais e de requisições do nível F0.
- A qualidade de PDF depende da camada de texto original; periódicos escaneados exigirão OCR.
- O limite padrão é 20 MB e 100.000 caracteres por documento.
- Bootstrap e Font Awesome são carregados por CDN no navegador.

O SB Admin original é um template gratuito e licenciado sob MIT pela Start Bootstrap. Esta interface adapta seus padrões de navegação e painel ao ProjectLecture.
