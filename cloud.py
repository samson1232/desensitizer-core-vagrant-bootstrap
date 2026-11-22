"""Cloud-only completions. One path, no lanes, no rating, no fallback.

Everything here speaks the OpenAI chat-completions shape, which DeepSeek, OpenAI,
Nous, OpenRouter, Groq, Together and a dozen others all expose. There is no
provider matrix and no preference order: you name one endpoint and one model, and
that is what gets called.

Deliberate omissions, because the point of this package is to be small enough to
read in one sitting:

* no local model path — a local model is a different deployment, not a fallback;
* no rating or routing — the caller does not ask a classifier which half of the
  work goes where, because there is only one place for it to go;
* no agent, subagent, or tool loop — ``complete()`` returns text and that is all;
* no SDK — one urllib POST, so install is ``git clone`` and nothing else.
"""
import json
import os
import urllib.error
import urllib.request

DEFAULT_TIMEOUT = 120
DEFAULT_MAX_TOKENS = 1024


class CloudError(Exception):
    """The cloud call failed. Never retried silently, never faked."""

    def __init__(self, message, status=None, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def _urllib_transport(call, timeout):
    """Default transport: POST the JSON body, return (status, headers, body bytes)."""
    req = urllib.request.Request(
        call["url"],
        data=json.dumps(call["json"]).encode("utf-8"),
        method="POST",
        headers=call["headers"],
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()
    except urllib.error.URLError as exc:
        raise CloudError(f"transport failed: {exc.reason}") from None


class CloudClient:
    """One endpoint, one model, one call shape.

    ``transport`` is injectable so the whole pipeline can be tested offline with
    no key and no network — see ``tests/fake_cloud.py``.
    """

    def __init__(self, base_url, model, api_key=None, api_key_env=None,
                 timeout=DEFAULT_TIMEOUT, max_tokens=DEFAULT_MAX_TOKENS, transport=None):
        if not base_url:
            raise CloudError("base_url is required; this package has no default endpoint")
        if not model:
            raise CloudError("model is required; there is no default model")
        if api_key is None and api_key_env:
            api_key = os.environ.get(api_key_env)
        if not api_key:
            raise CloudError(f"no API key: set {api_key_env or 'api_key'} in the environment or config")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.transport = transport or _urllib_transport

    def _payload(self, prompt, system, max_tokens, temperature):
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return {
            "url": f"{self.base_url}/chat/completions",
            "headers": {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
            },
            "body": {
                "model": self.model,
                "messages": messages,
                "max_tokens": max_tokens or self.max_tokens,
                "temperature": temperature,
                "stream": False,
            },
        }

    def complete(self, prompt, system=None, max_tokens=None, temperature=0.2):
        """Return the assistant text. Raises CloudError on anything unexpected."""
        request = self._payload(prompt, system, max_tokens, temperature)
        call = {"url": request["url"], "headers": request["headers"], "json": request["body"]}
        status, headers, raw = self.transport(call, self.timeout)

        if status != 200:
            snippet = (raw or b"")[:300].decode("utf-8", "replace")
            retry_after = None
            for key in ("retry-after", "Retry-After"):
                if headers.get(key):
                    retry_after = headers[key]
            raise CloudError(f"HTTP {status}: {snippet}", status=status, retry_after=retry_after)

        try:
            data = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        except ValueError:
            raise CloudError("response was not JSON") from None
        choices = data.get("choices") or []
        if not choices:
            raise CloudError("response had no choices")
        message = choices[0].get("message") or {}
        text = message.get("content")
        if not isinstance(text, str) or not text.strip():
            raise CloudError("response had an empty message body")
        return text


def client_from_config(config, transport=None, environ=None):
    """Build a client from a config dict. Missing keys are errors, never defaults."""
    environ = os.environ if environ is None else environ
    cloud = (config or {}).get("cloud") or {}
    if not cloud:
        raise CloudError("config has no 'cloud' section")
    return CloudClient(
        base_url=cloud.get("base_url"),
        model=cloud.get("model"),
        api_key=cloud.get("api_key") or None,
        api_key_env=cloud.get("api_key_env"),
        timeout=cloud.get("timeout", DEFAULT_TIMEOUT),
        max_tokens=cloud.get("max_tokens", DEFAULT_MAX_TOKENS),
        transport=transport,
    )
