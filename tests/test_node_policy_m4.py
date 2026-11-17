"""路径裁决中的口径选择、回退与证据保留（m4）。"""
from app.modules.m2_nodes.rules import evaluate as map_nodes
from app.modules.m4_paths.rules import evaluate as build_paths
from app import node_policy


def _scan(sid, parcel, node, day, t="00:00:00"):
    return {"scan_id": sid, "parcel": parcel, "node": node,
            "event_time": f"2026-11-{day:02d}T{t}Z",
            "event_utc": f"2026-11-{day:02d}T{t}+00:00",
            "received_utc": "2026-11-16T00:00:00+00:00",
            "destination": "JP", "_sources": [int(sid)], "reported": "0",
            "promised_hours": "24", "declared_value": "200"}


def _pipeline_rows(rows, params=None):
    mapped = map_nodes(rows, {"parameters": params or {}})
    assert not mapped["quarantine"]
    return mapped["rows"]


def test_backlog_old_scan_keeps_legacy_path():
    # 旧扫描本月补传：CN 仍是清关，路径不被重排
    rows = _pipeline_rows([_scan("1", "A", "收件", 5), _scan("2", "A", "CN", 6),
                           _scan("3", "A", "妥投", 7)])
    out = build_paths(rows, {"parameters": {}, "quarantine": []})
    parcel = out["rows"][0]
    assert parcel["path"] == "ACCEPT → CUSTOMS → DELIVERED"
    assert parcel["node_policy_result"] == "primary"
    assert parcel["node_versions_applied"] == [node_policy.LEGACY_VERSION]


def test_new_scan_uses_current_vocabulary():
    rows = _pipeline_rows([_scan("1", "B", "收件", 12), _scan("2", "B", "CN", 12, "12:00:00"),
                           _scan("3", "B", "清关", 13), _scan("4", "B", "妥投", 14)])
    out = build_paths(rows, {"parameters": {}, "quarantine": []})
    parcel = out["rows"][0]
    assert parcel["path"] == "ACCEPT → DEPART → CUSTOMS → DELIVERED"
    assert parcel["node_versions_applied"] == [node_policy.CURRENT_VERSION]


def test_fallback_to_legacy_when_current_interpretation_regresses():
    # 切后：清关在前、CN 在后。新义（起运）倒退；回退旧义（清关）后路径有效
    rows = _pipeline_rows([_scan("1", "C", "收件", 12), _scan("2", "C", "清关", 12, "12:00:00"),
                           _scan("3", "C", "CN", 13), _scan("4", "C", "妥投", 14)])
    out = build_paths(rows, {"parameters": {}, "quarantine": []})
    parcel = out["rows"][0]
    assert parcel["node_policy_result"] == "fallback"
    assert parcel["path"] == "ACCEPT → CUSTOMS → CUSTOMS → DELIVERED"
    assert out["metrics"]["policy_fallback"] == 1
    # 证据里同时保留新义（被弃用）与旧义（被采用）
    cn = next(e for e in parcel["interpretations"] if e["raw_node"] == "CN")
    assert cn["applied_version"] == node_policy.LEGACY_VERSION
    assert cn["alternatives"] == [{"version": node_policy.CURRENT_VERSION, "canonical": "DEPART"}]


def test_straddle_parcel_keeps_both_vocabularies_with_losing_evidence():
    # 跨切换票：切前 CN（旧义清关）在切后标准起运之前。逐事件锚点解释为
    # CUSTOMS→DEPART 属物理时间上的倒退，引擎回退切前 CN 的备选解释（新义起运），
    # 但两套口径的原始依据都保留在证据里，可人工追溯
    rows = _pipeline_rows([_scan("1", "D", "收件", 8), _scan("2", "D", "CN", 9),
                           _scan("3", "D", "起运", 11), _scan("4", "D", "妥投", 12)])
    out = build_paths(rows, {"parameters": {}, "quarantine": []})
    parcel = out["rows"][0]
    assert parcel["node_policy_result"] == "fallback"
    assert parcel["node_versions_applied"] == [node_policy.LEGACY_VERSION, node_policy.CURRENT_VERSION]
    assert parcel["path"] == "ACCEPT → DEPART → DEPART → DELIVERED"
    cn = next(e for e in parcel["interpretations"] if e["raw_node"] == "CN")
    # 被采用的是切后口径解释，但旧义清关作为备选仍留痕
    assert cn["canonical"] == "DEPART"
    assert cn["alternatives"] == [{"version": node_policy.LEGACY_VERSION, "canonical": "CUSTOMS"}]


def test_unresolvable_under_both_vocabularies_quarantines_with_attempts():
    # 妥投之后又来 CN：新义起运、旧义清关都构成倒退
    rows = _pipeline_rows([_scan("1", "E", "收件", 12), _scan("2", "E", "妥投", 13),
                           _scan("3", "E", "CN", 14)])
    out = build_paths(rows, {"parameters": {}, "quarantine": []})
    bad = out["quarantine"][0]
    assert bad["reason"] == "PATH_REGRESSION"
    plans = {a["plan"]: a["path"] for a in bad["node_policy_attempts"]}
    assert plans["anchor"] == "ACCEPT → DELIVERED → DEPART"
    assert any(v == "ACCEPT → DELIVERED → CUSTOMS" for v in plans.values())
    # 有效前缀仍形成正式路径，原路径依据不丢失
    assert out["rows"][0]["path"] == "ACCEPT → DELIVERED"


def test_force_current_does_not_fallback_and_flags_conflict():
    rows = _pipeline_rows([_scan("1", "F", "收件", 12), _scan("2", "F", "清关", 12, "12:00:00"),
                           _scan("3", "F", "CN", 13), _scan("4", "F", "妥投", 14)],
                          {"node_policy": "force_current"})
    out = build_paths(rows, {"parameters": {"node_policy": "force_current"}, "quarantine": []})
    assert out["metrics"]["policy_fallback"] == 0
    assert out["metrics"]["forced_policy_failures"] == 1
    assert out["quarantine"][0]["reason"] == "PATH_REGRESSION"
    assert out["rows"][0]["node_policy_result"] == "primary"


def test_force_legacy_interprets_post_cutover_cn_as_customs():
    rows = _pipeline_rows([_scan("1", "G", "收件", 12), _scan("2", "G", "CN", 13),
                           _scan("3", "G", "妥投", 14)], {"node_policy": "force_legacy"})
    out = build_paths(rows, {"parameters": {"node_policy": "force_legacy"}, "quarantine": []})
    assert out["rows"][0]["path"] == "ACCEPT → CUSTOMS → DELIVERED"
    assert out["rows"][0]["node_versions_applied"] == [node_policy.LEGACY_VERSION]


def test_every_kept_scan_carries_full_interpretation_evidence():
    rows = _pipeline_rows([_scan("1", "H", "收件", 12), _scan("2", "H", "CN", 13),
                           _scan("3", "H", "妥投", 14)])
    out = build_paths(rows, {"parameters": {}, "quarantine": []})
    evidence = out["rows"][0]["interpretations"]
    assert len(evidence) == 3
    assert all({"scan_id", "source_lines", "raw_node", "applied_version",
                "canonical", "basis", "alternatives"} <= set(e) for e in evidence)
    assert evidence[1]["raw_node"] == "CN" and evidence[1]["canonical"] == "DEPART"
