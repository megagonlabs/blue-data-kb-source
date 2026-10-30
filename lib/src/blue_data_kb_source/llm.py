"""LLM calls, sent through LiteLLM to the deployment's provider (#164, #166).

Every agent (extraction, schema expansion, answer/validate/enrich, the assert and
ingest judges) talks to `AsyncClient`, which has the OpenAI client's shape —
`client.chat.completions.create(model=..., messages=..., tools=..., ...)` — and
LiteLLM answers in OpenAI's response shape whatever the provider, so the agents'
tool-calling loops are the same for every provider.

Which provider answers is the deployment's: BLUE_DATA_LLM_PROVIDER, blank meaning
`openai`, as in blue-data-ops. Every call carries it as `custom_llm_provider`, so
LiteLLM dispatches on the deployment's setting instead of inferring a route from
the model string. The family model, BLUE_DATA_LLM_MODEL, names a model of that
provider; unset, each provider has its default (blue-data's base spec:
claude-opus-4-8 / gpt-4o).
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

from blue_data_kb_source.types import KBError

PROVIDER_ENV = "BLUE_DATA_LLM_PROVIDER"
DEFAULT_PROVIDER = "openai"
DEFAULT_MODELS = {"openai": "gpt-4o", "anthropic": "claude-opus-4-8"}
# The variable each provider's key is read from. A provider not listed here is
# still served; LiteLLM reports its own missing key at call time.
API_KEY_ENVS = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}

_MISSING_LITELLM = (
    "LLM features require LiteLLM: pip install 'blue-data-kb-source[llm]'"
)


class MissingKeyError(RuntimeError):
    """The provider's API key variable is not set; `env` names it.

    A configuration error, not a KBError, like the OpenAI SDK's own missing-key
    error it replaces: callers that report KBErrors as data errors pass it on."""

    def __init__(self, env: str) -> None:
        super().__init__(f"{env} is not set; {PROVIDER_ENV}={provider()!r} needs it for LLM calls")
        self.env = env


def provider() -> str:
    """The provider this deployment's LLM calls go to."""
    return os.environ.get(PROVIDER_ENV, "").strip().lower() or DEFAULT_PROVIDER


def default_model() -> str:
    """The provider's default model, for a call that names none."""
    name = provider()
    if name not in DEFAULT_MODELS:
        raise KBError(
            f"no default model for {PROVIDER_ENV}={name!r}: set BLUE_DATA_LLM_MODEL "
            "(or a BLUE_DATA_KB_SOURCE_*_LLM_MODEL) to one of its models"
        )
    return DEFAULT_MODELS[name]


def configured_model(function: str | None = None) -> str:
    """The model configured for *function* (QUERY, EXTRACTION, ...): the function's
    variable, then the KB-wide one, then the family-wide one, then the provider's
    default. The CLI resolves the same chain from a profile."""
    names = [f"BLUE_DATA_KB_SOURCE_{function}_LLM_MODEL"] if function else []
    names += ["BLUE_DATA_KB_SOURCE_LLM_MODEL", "BLUE_DATA_LLM_MODEL"]
    for name in names:
        if os.environ.get(name):
            return os.environ[name]
    return default_model()


def api_key_env() -> str | None:
    """The variable the provider's key is read from, when kb knows it."""
    return API_KEY_ENVS.get(provider())


def missing_key() -> str | None:
    """The key variable the provider needs and the environment lacks, if any."""
    env = api_key_env()
    return env if env and not os.environ.get(env) else None


class AsyncClient:
    """An OpenAI-shaped async client whose completions LiteLLM sends to `provider()`.

    `model=None` means the provider's default."""

    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, *, model: str | None = None, **kwargs: Any) -> Any:
        # LiteLLM otherwise downloads its model price map on import; kb uses none
        # of it, and the bundled copy keeps an import off the network.
        os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
        try:
            import litellm
        except ImportError as exc:
            raise KBError(_MISSING_LITELLM) from exc
        env = missing_key()
        if env:
            raise MissingKeyError(env)
        # LiteLLM otherwise prints its help banner on every failed call.
        litellm.suppress_debug_info = True
        # Its aiohttp transport leaves SSL connections bound to the caller's event
        # loop; once `asyncio.run` closes that loop they are torn down at exit with
        # an "Event loop is closed" traceback. httpx's transport is not.
        litellm.disable_aiohttp_transport = True
        return await litellm.acompletion(
            model=model or default_model(), custom_llm_provider=provider(), **kwargs
        )

