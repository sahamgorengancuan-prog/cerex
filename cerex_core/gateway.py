"""Opt-in OpenAI-compatible chat-completions; explicit provider, no tool execution."""
import json
import os
from urllib.parse import urlparse
from urllib.request import Request, urlopen

class GatewayError(RuntimeError):
    pass

class LLMGateway:
    def __init__(self, base_url=None, model=None, key_env="CEREX_LLM_API_KEY", live=False):
        self.base_url = (base_url or os.environ.get("CEREX_LLM_BASE_URL", "http://127.0.0.1:11434/v1")).rstrip("/")
        self.model = model or os.environ.get("CEREX_LLM_MODEL", "local-model")
        self.key_env = key_env
        self.live = live
        parsed = urlparse(self.base_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise GatewayError("Do not include secrets/query in LLM base URL")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")):
            raise GatewayError("LLM URL must use HTTPS, except loopback HTTP")
        if parsed.scheme not in ("https","http") or not parsed.hostname:
            raise GatewayError("Invalid LLM endpoint")

    def chat(self, system, user):
        if not self.live:
            return json.dumps({"reply": "MOCK: " + user[:250], "messages": []}, ensure_ascii=False)
        key = os.environ.get(self.key_env, "")
        if not key and urlparse(self.base_url).hostname not in ("127.0.0.1","localhost","::1"):
            raise GatewayError(f"Missing environment variable {self.key_env}")
        payload = {"model":self.model,"temperature":0.2,"messages":[{"role":"system","content":system},{"role":"user","content":user}]}
        headers={"Content-Type":"application/json"}
        if key:
            headers["Authorization"] = "Bearer " + key
        request = Request(self.base_url+"/chat/completions",data=json.dumps(payload).encode("utf-8"),headers=headers,method="POST")
        try:
            with urlopen(request, timeout=75) as res:
                raw=res.read(2*1024*1024)
            data=json.loads(raw)
            return data["choices"][0]["message"]["content"]
        except Exception as exc:
            # Intentionally no raw provider body, headers, or exception string to avoid key leaks
            raise GatewayError(f"LLM request failed ({type(exc).__name__})") from None


def parse_agent_reply(text):
    """LLM may answer in JSON, fenced JSON, or plain text. No commands executed."""
    value=text.strip()
    if value.startswith("```json"):
        value=value[7:].split("```",1)[0].strip()
    try:
        obj=json.loads(value)
        if isinstance(obj,dict):
            messages=obj.get("messages",[])
            if not isinstance(messages,list):
                messages=[]
            return {"reply":str(obj.get("reply", ""))[:16000],"messages":messages[:10]}
    except (json.JSONDecodeError,TypeError):
        pass
    return {"reply":text[:16000],"messages":[]}
