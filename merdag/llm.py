from __future__ import annotations

import os


def codex_model() -> str:
    return os.getenv("MERDAG_CODEX_MODEL", "meta-llama/Llama-4-Scout-17B-16E-Instruct")


def fast_model() -> str:
    return os.getenv("MERDAG_FAST_MODEL", "meta-llama/Llama-4-Scout-17B-16E-Instruct")


def call_llm(tier: str, system_prompt: str, user_prompt: str) -> dict[str, int | str]:
    """Call the W&B Inference API (OpenAI-compatible) for a merdag execution step."""

    import openai

    model = codex_model() if tier in {"codex", "human"} else fast_model()
    client = openai.OpenAI(
        api_key=os.environ["WANDB_API_KEY"],
        base_url="https://api.wandb.ai/v1",
    )
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=1024,
    )
    usage = response.usage
    message = response.choices[0].message.content or ""
    return {
        "response": message,
        "model": model,
        "tokens_in": usage.prompt_tokens if usage else 0,
        "tokens_out": usage.completion_tokens if usage else 0,
    }
