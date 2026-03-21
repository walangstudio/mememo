"""
LLM adapter for mememo — multi-provider, config-driven.

Provider resolution order:
  1. providers.yaml pointed to by MEMEMO_LLM_CONFIG env var
  2. providers.yaml in CWD
  3. mememo/config/providers.yaml (package default)

API keys: env var {PROVIDER}_API_KEY takes precedence over yaml api_key field.

Passthrough: when default_provider is "passthrough" (the default) or no API key
is found, complete() returns None — callers handle that by self-extracting.
"""

import logging
import os
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-4o-mini",
    "ollama": "llama3.2",
}

_PACKAGE_PROVIDERS_YAML = Path(__file__).parent.parent / "config" / "providers.yaml"


class LLMAdapter:
    def __init__(self, config_path: Path | None = None):
        self._cfg: dict | None = None
        self._override_path = config_path

    def _load(self) -> dict:
        if self._cfg is not None:
            return self._cfg

        candidates: list[Path] = []
        if self._override_path:
            candidates.append(Path(self._override_path))
        env_path = os.getenv("MEMEMO_LLM_CONFIG")
        if env_path:
            candidates.append(Path(env_path))
        candidates.append(Path.cwd() / "providers.yaml")
        candidates.append(_PACKAGE_PROVIDERS_YAML)

        for path in candidates:
            if path.exists():
                try:
                    self._cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                    logger.debug("LLM config loaded from %s", path)
                    return self._cfg
                except Exception as e:
                    logger.warning("Failed to load LLM config from %s: %s", path, e)

        self._cfg = {}
        return self._cfg

    def _provider(self) -> str:
        return self._load().get("default_provider", "passthrough")

    def _model(self, provider: str) -> str:
        return (
            self._load().get("providers", {}).get(provider, {}).get("default_model")
            or _DEFAULT_MODELS.get(provider)
            or "unknown"
        )

    def _api_key(self, provider: str) -> str | None:
        env_key = os.getenv(f"{provider.upper()}_API_KEY")
        if env_key:
            return env_key
        return self._load().get("providers", {}).get(provider, {}).get("api_key")

    def is_passthrough(self) -> bool:
        provider = self._provider()
        return provider in ("passthrough", "none", "")

    async def complete(self, system_prompt: str, user_prompt: str) -> str | None:
        """
        Call the configured LLM. Returns response text, or None for passthrough.
        Callers treat None as "no LLM available — self-extract".
        """
        provider = self._provider()
        if provider in ("passthrough", "none", ""):
            return None

        model = self._model(provider)
        logger.debug("LLM dispatch provider=%s model=%s", provider, model)

        if provider == "anthropic":
            return await self._anthropic(model, system_prompt, user_prompt)
        if provider == "ollama":
            return await self._ollama(model, system_prompt, user_prompt)

        cfg = self._load()
        provider_cfg = cfg.get("providers", {}).get(provider, {})
        if provider == "openai" or provider_cfg.get("type") == "openai_compatible":
            base_url = provider_cfg.get("base_url", "https://api.openai.com/v1")
            return await self._openai_compat(model, system_prompt, user_prompt, base_url, provider)

        logger.warning("Unknown provider '%s', falling back to passthrough", provider)
        return None

    async def _anthropic(self, model: str, system_prompt: str, user_prompt: str) -> str | None:
        api_key = self._api_key("anthropic")
        if not api_key:
            logger.debug("No Anthropic API key — passthrough")
            return None
        try:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=api_key)
            response = await client.messages.create(
                model=model,
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return response.content[0].text
        except Exception as e:
            logger.warning("Anthropic call failed: %s", e)
            return None

    async def _openai_compat(
        self, model: str, system_prompt: str, user_prompt: str, base_url: str, provider: str
    ) -> str | None:
        api_key = self._api_key(provider)
        try:
            import httpx

            headers: dict[str, str] = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": model,
                        "max_tokens": 1024,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    },
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning("OpenAI-compat call failed (%s): %s", provider, e)
            return None

    async def _ollama(self, model: str, system_prompt: str, user_prompt: str) -> str | None:
        base_url = (
            self._load().get("providers", {}).get("ollama", {}).get("base_url")
            or "http://localhost:11434"
        )
        try:
            import httpx

            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{base_url}/api/generate",
                    json={
                        "model": model,
                        "system": system_prompt,
                        "prompt": user_prompt,
                        "stream": False,
                    },
                )
                resp.raise_for_status()
                return resp.json()["response"]
        except Exception as e:
            logger.warning("Ollama call failed: %s", e)
            return None
