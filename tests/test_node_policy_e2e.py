"""口径切换样例的端到端裁决、HTTP 接口与归档证据。"""
import csv
import io
import json

from app.pipeline import calculate, load_sample
from app import store


def _cutover():
    return calculate(load_sample("cutover.csv"))


def test_sample_covers_primary_and_fallback():
    r = _cutover()
    m4 = r["stages"][3]
    results = {row["parcel"]: row["node_policy_result"] for row in m4["rows"]}
    assert results["0101"] == "primary"   # 积压旧扫描，旧口径直接成立
    assert results["0102"] == "primary"   # 切换后新扫描，新口径直接成立
    assert results["0103"] == "fallback"  # 新口径倒退，回退旧版
    assert m4["metrics"]["policy_fallback"] >= 1


def test_sample_contains_parcel_unresolvable_under_both_vocabularies():
    r = _cutover()
    bad = next(q for q in r["stages"][3]["quarantine"] if q["parcel"] == "0105")
    assert bad["reason"] == "PATH_REGRESSION"
    # 两种口径的候选路径与各自失败原因都留痕，有效前缀仍出正式路径
    paths = {a["path"] for a in bad["node_policy_attempts"]}
    assert {"ACCEPT → DELIVERED → DEPART", "ACCEPT → DELIVERED → CUSTOMS"} <= paths
    parcel = next(x for x in r["stages"][3]["rows"] if x["parcel"] == "0105")
    assert parcel["path"] == "ACCEPT → DELIVERED"
    # 隔离来源行进 m6 对平并随证据 CSV 导出
    assert r["stages"][5]["metrics"]["balanced"] is True


def test_backlog_parcel_legacy_path_unchanged_after_upgrade():
    r = _cutover()
    p = next(row for row in r["stages"][3]["rows"] if row["parcel"] == "0101")
    assert p["path"] == "ACCEPT → CUSTOMS → DELIVERED"
    assert p["node_versions_applied"] == ["v2026-10"]
    cn = next(e for e in p["interpretations"] if e["raw_node"] == "CN")
    assert cn["applied_version"] == "v2026-10" and cn["canonical"] == "CUSTOMS"


def test_pipeline_balances_and_archives_with_policy_evidence():
    r = _cutover()
    assert r["stages"][5]["metrics"]["balanced"] is True
    assert r["stages"][6]["metrics"]["ready"] is True
    archived = r["stages"][6]["rows"]
    assert all("node_policy_result" in row and "interpretations" in row for row in archived)


def test_manifest_records_registry_and_policy():
    r = _cutover()
    meta = r["manifest"]
    assert len(meta["node_registry_fingerprint"]) == 16
    assert meta["node_cutover_at"] == "2026-11-10T00:00:00Z"
    assert meta["node_policy"] == "event_time"


def test_policy_mode_changes_run_identity():
    a = calculate(load_sample("cutover.csv"))
    b = calculate(load_sample("cutover.csv"), {"node_policy": "force_current"})
    assert a["run_id"] != b["run_id"]
    c = calculate(load_sample("cutover.csv"))
    assert a["run_id"] == c["run_id"]  # 同输入同参数幂等


def test_node_policy_registry_endpoint(client):
    res = client.get("/api/node-policy")
    assert res.status_code == 200
    body = res.get_json()
    assert {v["version"] for v in body["registry"]["vocabularies"]} == {"v2026-10", "v2026-11"}
    legacy = next(v for v in body["registry"]["vocabularies"] if v["version"] == "v2026-10")
    current = next(v for v in body["registry"]["vocabularies"] if v["version"] == "v2026-11")
    assert legacy["mapping"]["CN"] == "CUSTOMS" and current["mapping"]["CN"] == "DEPART"


def test_runs_reject_unknown_policy_mode(client):
    res = client.post("/api/runs", json={"sample": "cutover.csv",
                                         "parameters": {"node_policy": "nope"}})
    assert res.status_code == 422 and res.get_json()["code"] == "BAD_NODE_POLICY"


def test_runs_accept_policy_parameter(client):
    res = client.post("/api/runs", json={"sample": "cutover.csv",
                                         "parameters": {"node_policy": "force_legacy"}})
    assert res.status_code == 200
    assert res.get_json()["manifest"]["node_policy"] == "force_legacy"


def test_archive_csv_contains_policy_lineage(client):
    p = client.post("/api/runs", json={"sample": "cutover.csv"}).get_json()
    export = client.get("/api/runs/" + p["run_id"] + "/archive").data.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(export)))
    evidence = [json.loads(r[6]) for r in rows[1:]]
    assert any("interpretations" in row for row in evidence)
    backlog = next(row for row in evidence if row.get("parcel") == "0101")
    assert backlog["node_versions_applied"] == ["v2026-10"]


def test_archived_run_remains_readable_with_current_rules(client):
    # 归档后的批次按存储载荷回放，后续规则升级不改变其已封存结论
    p = client.post("/api/runs", json={"sample": "cutover.csv"}).get_json()
    run_id = p["run_id"]
    stored = store.get(run_id)
    assert stored["stages"][3]["rows"] == p["stages"][3]["rows"]
    assert stored["manifest"]["rule_version"] == p["manifest"]["rule_version"]


def test_policy_reruns_create_separate_batches_without_overwriting(client):
    first = client.post("/api/runs", json={"sample": "cutover.csv"}).get_json()
    second = client.post("/api/runs", json={"sample": "cutover.csv",
                                            "parameters": {"node_policy": "force_current"}}).get_json()
    assert first["run_id"] != second["run_id"]
    # 原始 event_time 批次仍可原样取回，结论不被新口径批次覆盖
    original = store.get(first["run_id"])
    assert original["manifest"]["node_policy"] == "event_time"
    assert original["stages"][3]["rows"] == first["stages"][3]["rows"]
    backlog = next(r for r in original["stages"][3]["rows"] if r["parcel"] == "0101")
    assert backlog["path"] == "ACCEPT → CUSTOMS → DELIVERED"
    ids = {h["run_id"] for h in store.history()}
    assert {first["run_id"], second["run_id"]} <= ids


def test_cutover_sample_listed_and_pages_render(client):
    res = client.post("/api/runs", json={"sample": "cutover.csv"})
    assert res.status_code == 200
    for slug in ("nodes", "paths"):
        assert client.get("/" + slug).status_code == 200
