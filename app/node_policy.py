"""承运商节点代码口径注册表（版本化）。

同一原始代码在不同口径版本下可能指向不同标准节点：
  - legacy（旧版 v2026-10）：CN 表示清关 CUSTOMS
  - current（新版 v2026-11）：CN 改义为起运 DEPART
口径的适用锚点是扫描的事件发生时刻（event_time→UTC），不是补传/上传时刻，
因此旧扫描即使本月才补传，仍按其发生时刻命中旧口径，原路径依据不会被改写。
"""
import hashlib
import json

from app.values import instant

# 标准节点（路径推进顺序）
CANONICAL_NODES = ("ACCEPT", "DEPART", "CUSTOMS", "DELIVERED")
PRIORITY = {name: index for index, name in enumerate(CANONICAL_NODES)}

LEGACY_VERSION = "v2026-10"
CURRENT_VERSION = "v2026-11"
DEFAULT_CUTOVER = "2026-11-10T00:00:00Z"

# 中文别名在两套口径下含义一致；CN 是本次改义的冲突代码
_COMMON_ALIASES = {
    "收件": "ACCEPT", "ACCEPT": "ACCEPT",
    "起运": "DEPART", "DEPART": "DEPART",
    "清关": "CUSTOMS", "CUSTOMS": "CUSTOMS",
    "妥投": "DELIVERED", "DELIVERED": "DELIVERED",
}
VOCABULARIES = {
    LEGACY_VERSION: {**_COMMON_ALIASES, "CN": "CUSTOMS"},
    CURRENT_VERSION: {**_COMMON_ALIASES, "CN": "DEPART"},
}
# 口径生效窗口（左闭右开）：current 从切换时刻起生效，之前一律 legacy
WINDOWS = {
    LEGACY_VERSION: (None, DEFAULT_CUTOVER),
    CURRENT_VERSION: (DEFAULT_CUTOVER, None),
}
# 对外可查的口径登记
REGISTRY = {
    "cutover_at": DEFAULT_CUTOVER,
    "anchor": "event_time_utc",
    "vocabularies": [
        {
            "version": LEGACY_VERSION,
            "effective_from": None,
            "effective_to": DEFAULT_CUTOVER,
            "mapping": VOCABULARIES[LEGACY_VERSION],
        },
        {
            "version": CURRENT_VERSION,
            "effective_from": DEFAULT_CUTOVER,
            "effective_to": None,
            "mapping": VOCABULARIES[CURRENT_VERSION],
        },
    ],
}

# 允许人工指定的整批口径模式
POLICY_MODES = ("event_time", "force_legacy", "force_current")


def registry_fingerprint():
    """口径注册表指纹：写入清单，登记内容变动会改变运行指纹。"""
    body = json.dumps(REGISTRY, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(body).hexdigest()[:16]


def cutover_of(parameters):
    text = (parameters or {}).get("node_cutover") or DEFAULT_CUTOVER
    return instant(text)


def version_for_event(event_utc, parameters=None):
    """按事件发生时刻选择生效口径；无法判定时刻时回退新版。"""
    try:
        when = instant(event_utc)
    except Exception:
        return CURRENT_VERSION, "event_time_unparseable_fallback_current"
    return (LEGACY_VERSION, "event_in_legacy_window") if when < cutover_of(parameters) \
        else (CURRENT_VERSION, "event_in_current_window")


def resolve(raw_node, event_utc, parameters=None):
    """返回单条扫描在所有可用口径下的解释。

    每项含：version / canonical / basis。anchor 为按事件时刻命中的首选口径。
    原始代码任何口径都无法识别时，known=False，交由路径阶段隔离，不在此丢弃。
    """
    forced = (parameters or {}).get("node_policy") or "event_time"
    if forced not in POLICY_MODES:
        raise ValueError("NODE_POLICY_UNKNOWN")
    anchor_version, anchor_basis = version_for_event(event_utc, parameters)
    if forced == "force_legacy":
        anchor_version, anchor_basis = LEGACY_VERSION, "manual_force_legacy"
    elif forced == "force_current":
        anchor_version, anchor_basis = CURRENT_VERSION, "manual_force_current"

    interpretations = []
    for version in (LEGACY_VERSION, CURRENT_VERSION):
        canonical = VOCABULARIES[version].get(raw_node)
        if canonical is None:
            continue
        basis = anchor_basis if version == anchor_version else "alternative_vocabulary"
        interpretations.append({
            "version": version,
            "canonical": canonical,
            "basis": basis,
        })
    interpretations.sort(key=lambda item: 0 if item["version"] == anchor_version else 1)
    anchor = next((item for item in interpretations if item["version"] == anchor_version), None)
    return {
        "raw_node": raw_node,
        "known": anchor is not None,
        "anchor_version": anchor_version,
        "anchor_basis": anchor_basis,
        "interpretations": interpretations,
        "anchor": anchor,
    }
