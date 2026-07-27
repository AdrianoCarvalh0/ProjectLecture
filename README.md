# ProjectLecture

Leitor comunitário de documentos com Django, API REST interna, MySQL, Celery/Redis e interface baseada no SB Admin. Transforma texto, PDF, DOCX, EPUB ou TXT em áudio neural local, reproduz no navegador e salva automaticamente o ponto de leitura.

## O que já funciona

- cadastro, login e isolamento da biblioteca por usuário;
- criação por texto colado ou upload de PDF, DOCX, EPUB e TXT;
- extração de texto com PyMuPDF, python-docx e EbookLib;
- geração progressiva e assíncrona em pequenos blocos com Celery;
- TTS neural local com Kokoro-82M, sem chave externa ou cobrança por caractere;
- três narradores brasileiros com avatares fictícios: Lia, Caio e Ravi;
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

No primeiro uso de uma voz neural, o Kokoro baixa seus arquivos para o volume persistente `model_cache`. Essa primeira amostra pode demorar; as próximas reutilizam o modelo e o cache.

O ambiente padrão instala o PyTorch para CPU, reduzindo bastante a imagem. Em um host com NVIDIA Container Toolkit configurado, o override instala a variante CUDA:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build
```

Para criar um administrador:

```bash
docker compose exec web python manage.py createsuperuser
```

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
        Celery ── preparação acadêmica ── serviço neural Kokoro ── WAVs curtos
                                            |
                                      modelos abertos locais
```

O TTS fica atrás de uma interface em `reader/services/tts.py`. O Kokoro roda num container FastAPI separado, portanto modelos mais pesados como Chatterbox podem ser adicionados como outro provedor sem alterar biblioteca, fila, player ou progresso.

O navegador recebe os trechos conforme ficam prontos e pré-carrega o seguinte.
Nas vozes Kokoro, os tempos por palavra vêm das durações fonéticas previstas pelo
próprio modelo, incluindo as pausas de pontuação. Vozes sem essa informação usam
uma estimativa ponderada como fallback.

## Avaliar qualidade e desempenho

Para gerar o mesmo trecho acadêmico com todas as vozes e medir o fator de tempo real:

```bash
docker compose exec web python manage.py benchmark_voices
```

As amostras ficam em `media/benchmarks/`. Para avaliação de qualidade, compare naturalidade, inteligibilidade de siglas e referências, fadiga após alguns minutos e consistência de ritmo. O tempo de síntese não mede qualidade, mas ajuda a decidir entre CPU e GPU.

## Modelos e licenças

- [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M): Apache 2.0, com vozes brasileiras `pf_dora`, `pm_alex` e `pm_santa`.
- `espeak-ng`: fallback livre e leve para máquinas que não comportam o serviço neural.
- os avatares Lia, Caio e Ravi foram gerados especialmente para este projeto e não representam pessoas reais.

## Limites desta versão

- A primeira síntese neural precisa baixar o modelo e é mais lenta.
- A qualidade de PDF depende da camada de texto original; periódicos escaneados exigirão OCR.
- O limite padrão é 20 MB e 100.000 caracteres por documento.
- Bootstrap e Font Awesome são carregados por CDN no navegador.

O SB Admin original é um template gratuito e licenciado sob MIT pela Start Bootstrap. Esta interface adapta seus padrões de navegação e painel ao ProjectLecture.
