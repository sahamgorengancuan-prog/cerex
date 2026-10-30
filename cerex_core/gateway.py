"""Opt-in OpenAI-compatible chat-completions; explicit provider, no tool execution."""
import json
import os
from urllib.parse import urlparse
from urllib.request import Request, urlopen

class GatewayError(RuntimeError):
    pass

class LLMGateway:
    def __init__(self, base_url=None, model=None, key_env="CEREX_LLM_API_KEY", live=False,
                 max_tokens=None, allow_loopback=True):
        self.base_url = (base_url or os.environ.get("CEREX_LLM_BASE_URL", "http://127.0.0.1:11434/v1")).rstrip("/")
        self.model = model or os.environ.get("CEREX_LLM_MODEL", "local-model")
        self.key_env = key_env
        self.live = live
        self.max_tokens = int(max_tokens) if max_tokens else None
        if self.max_tokens is not None and not 16 <= self.max_tokens <= 32000:
            raise GatewayError("max_tokens outside permitted range")
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "requests": 0}
        parsed = urlparse(self.base_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise GatewayError("Do not include secrets/query in LLM base URL")
        loopback = parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")
        if parsed.scheme != "https" and not (loopback and allow_loopback):
            raise GatewayError("LLM URL must use HTTPS" + (", except loopback HTTP" if allow_loopback else ""))
        if parsed.scheme not in ("https","http") or not parsed.hostname:
            raise GatewayError("Invalid LLM endpoint")

    def chat(self, system, user):
        if not self.live:
            return json.dumps({"reply": "MOCK: " + user[:250], "messages": []}, ensure_ascii=False)
        key = os.environ.get(self.key_env, "")
        if not key and urlparse(self.base_url).hostname not in ("127.0.0.1","localhost","::1"):
            raise GatewayError(f"Missing environment variable {self.key_env}")
        payload = {"model":self.model,"temperature":0.2,"messages":[{"role":"system","content":system},{"role":"user","content":user}]}
        if self.max_tokens:
            payload["max_tokens"] = self.max_tokens
        headers={"Content-Type":"application/json"}
        if key:
            headers["Authorization"] = "Bearer " + key
        request = Request(self.base_url+"/chat/completions",data=json.dumps(payload).encode("utf-8"),headers=headers,method="POST")
        try:
            with urlopen(request, timeout=75) as res:
                raw=res.read(2*1024*1024)
            data=json.loads(raw)
            content=data["choices"][0]["message"]["content"]
            usage=data.get("usage") if isinstance(data.get("usage"),dict) else {}
            self.usage["requests"]+=1
            for k in ("prompt_tokens","completion_tokens"):
                if isinstance(usage.get(k),int) and usage[k]>=0:
                    self.usage[k]+=usage[k]
            if not isinstance(content,str):
                raise TypeError("non-text model reply")
            return content
        except Exception as exc:
            # Intentionally no raw provider body, headers, or exception string to avoid key leaks
            raise GatewayError(f"LLM request failed ({type(exc).__name__})") from None


def parse_agent_reply(text):
    """LLM may answer in JSON, fenced JSON, or plain text. No commands executed."""
    value=text.strip()
    if value.startswith("```"):
        value=value[3:]
        if value[:4].lower()=="json":
            value=value[4:]
        value=value.split("```",1)[0].strip()
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
