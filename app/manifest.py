import hashlib,json,time
from datetime import datetime,timezone
from app.config import RULE_VERSION
from app import node_policy
def digest(raw,params=None):
    return hashlib.sha256(raw+json.dumps(params or {},sort_keys=True,ensure_ascii=False).encode()+RULE_VERSION.encode()).hexdigest()
def manifest(raw,params,started):
    return {"input_sha256":hashlib.sha256(raw).hexdigest(),"rule_version":RULE_VERSION,
            "node_registry_fingerprint":node_policy.registry_fingerprint(),
            "node_cutover_at":node_policy.DEFAULT_CUTOVER,
            "node_policy":(params or {}).get("node_policy","event_time"),
            "parameters":params,"generated_at":datetime.now(timezone.utc).isoformat(),
            "elapsed_ms":round((time.monotonic()-started)*1000,2)}
