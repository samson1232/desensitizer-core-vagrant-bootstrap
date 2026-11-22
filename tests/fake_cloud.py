"""A transport that never touches the network. Used by every offline test."""
import json


class FakeTransport:
    """Records each call; answers with a fixed reply, a callable, or raw bytes."""

    def __init__(self, reply="ok", status=200, headers=None, raw=None):
        self.reply = reply
        self.status = status
        self.headers = headers or {}
        self.raw = raw
        self.calls = []

    def __call__(self, call, timeout=None):
        self.calls.append({"call": call, "timeout": timeout})
        if self.raw is not None:
            return self.status, self.headers, self.raw
        text = self.reply(call) if callable(self.reply) else self.reply
        body = json.dumps({"choices": [{"message": {"role": "assistant", "content": text}}]}).encode("utf-8")
        return self.status, self.headers, body

    # -- helpers used by the tests --------------------------------------
    @property
    def last_prompt(self):
        return self.calls[-1]["call"]["json"]["messages"][-1]["content"]

    @property
    def last_body(self):
        return self.calls[-1]["call"]["json"]


class EchoTransport(FakeTransport):
    """Replies with the prompt it received, plus a short acknowledgement."""

    def __init__(self, suffix="\n\nAcknowledged."):
        super().__init__(reply=None)
        self.suffix = suffix

    def __call__(self, call, timeout=None):
        prompt = call["json"]["messages"][-1]["content"]
        self.reply = prompt + self.suffix
        return super().__call__(call, timeout)
