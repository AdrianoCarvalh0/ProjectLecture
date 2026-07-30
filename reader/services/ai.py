from openai import OpenAI

from reader.models import AppConfiguration
from reader.services.runtime_config import (
    azure_openai_api_key,
    get_app_configuration,
    openai_api_key,
)


class AIConfigurationError(RuntimeError):
    pass


def _client_and_model(configuration):
    if configuration.ai_provider == AppConfiguration.AIProvider.OPENAI:
        key = openai_api_key(configuration)
        if not key:
            raise AIConfigurationError("A chave da OpenAI não foi configurada.")
        return OpenAI(api_key=key), configuration.openai_model

    if configuration.ai_provider == AppConfiguration.AIProvider.AZURE_OPENAI:
        key = azure_openai_api_key(configuration)
        endpoint = configuration.azure_openai_endpoint.rstrip("/")
        deployment = configuration.azure_openai_deployment
        if not key or not endpoint or not deployment:
            raise AIConfigurationError(
                "Endpoint, implantação e chave do Azure OpenAI são obrigatórios."
            )
        return (
            OpenAI(
                api_key=key,
                base_url=f"{endpoint}/openai/v1/",
            ),
            deployment,
        )

    raise AIConfigurationError("Os recursos de IA estão desativados.")


def _chunks(text, maximum=40_000):
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    chunks = []
    current = ""
    for paragraph in paragraphs:
        remaining = paragraph
        while len(remaining) > maximum:
            boundary = remaining.rfind(" ", 0, maximum)
            boundary = boundary if boundary > maximum // 2 else maximum
            piece, remaining = remaining[:boundary], remaining[boundary:]
            if current:
                chunks.append(current)
                current = ""
            chunks.append(piece.strip())
        candidate = f"{current}\n\n{remaining}".strip() if current else remaining.strip()
        if current and len(candidate) > maximum:
            chunks.append(current)
            current = remaining.strip()
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _response_text(client, model, instructions, text, max_output_tokens=5_000):
    response = client.responses.create(
        model=model,
        instructions=instructions,
        input=text,
        max_output_tokens=max_output_tokens,
    )
    output = (response.output_text or "").strip()
    if not output:
        raise RuntimeError("O provedor de IA não retornou texto.")
    return output


def summarize_text(text, title=""):
    configuration = get_app_configuration()
    client, model = _client_and_model(configuration)
    parts = _chunks(text)
    instructions = (
        "Produza um resumo fiel em português do Brasil. Preserve objetivo, argumentos, "
        "método, resultados, conclusões e limitações. Não invente informações. "
        "Use subtítulos curtos e listas somente quando ajudarem a leitura."
    )
    summaries = [
        _response_text(
            client,
            model,
            instructions,
            f"Título: {title}\n\nConteúdo:\n{part}",
        )
        for part in parts
    ]
    if len(summaries) == 1:
        return summaries[0], model
    combined = "\n\n---\n\n".join(summaries)
    final = _response_text(
        client,
        model,
        (
            "Consolide os resumos parciais em um único resumo estruturado em português "
            "do Brasil. Remova repetições, mantenha fatos e ressalvas e não acrescente "
            "informações ausentes."
        ),
        f"Título: {title}\n\nResumos parciais:\n{combined}",
        max_output_tokens=7_000,
    )
    return final, model


def translate_text(text, target_language):
    configuration = get_app_configuration()
    client, model = _client_and_model(configuration)
    instructions = (
        f"Traduza integralmente para {target_language}. Preserve títulos, parágrafos, "
        "citações, números, referências e terminologia técnica. Não resuma, não explique "
        "e não acrescente comentários."
    )
    translated = [
        _response_text(
            client,
            model,
            instructions,
            part,
            max_output_tokens=12_000,
        )
        for part in _chunks(text, maximum=24_000)
    ]
    return "\n\n".join(translated), model
