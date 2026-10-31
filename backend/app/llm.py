"""Schema-constrained calls to Muse Spark through the OpenAI-compatible Model API."""
import json
import time

import openai

from app import config

_client = openai.OpenAI(base_url=config.MODEL_BASE_URL, api_key=config.MODEL_API_KEY)


class LLMError(RuntimeError):
    pass


def structured_call(system: str, user: str, name: str, schema: dict) -> dict:
    """Return the parsed JSON object. One retry on transport errors or unparseable output."""
    last: Exception | None = None
    for attempt in range(2):
        try:
            resp = _client.chat.completions.create(
                model=config.REASONING_MODEL,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}},
            )
            return json.loads(resp.choices[0].message.content)
        except (openai.APIError, json.JSONDecodeError, TypeError) as e:
            last = e
            if attempt == 0:
                time.sleep(1)
    raise LLMError(f"Muse Spark call failed after retry: {last}") from last
