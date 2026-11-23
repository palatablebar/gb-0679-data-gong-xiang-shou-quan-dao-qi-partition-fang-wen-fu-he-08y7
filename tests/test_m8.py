import csv,io
import pytest
from app import store
from app.errors import DomainError
from app.modules.m8_dormancy.rules import evaluate,months_back,touched_months
from app.pipeline import calculate,execute,load_sample
GRANT={"id":"G-T1","principal":"analyst","resource":"orders","start":"2026-09-01T00:00:00Z","end":"2026-10-01T00:00:00Z","from_partition":"2026-09","to_partition":"2026-10","effect":"allow"}
def row(event_id,partition="2026-09",at="2026-11-15T00:00:00Z",principal="analyst",resource="orders",sources=(1,)):
    return {"event_id":event_id,"principal":principal,"resource":resource,"partition":partition,"at":at,"observed":"allowed","violation":"一致","_sources":list(sources)}
def ctx(grants,**params):
    p={"grants":grants,"as_of":"2026-12-01T00:00:00Z"};p.update(params)
    return {"parameters":p,"run_id":"test-run"}
def test_months_back_clips_month_end():
    assert months_back(__import__("datetime").datetime(2026,3,31),1).isoformat()=="2026-02-28T00:00:00"
def test_touched_months_half_open():
    from datetime import datetime
    assert touched_months(datetime(2026,11,1),datetime(2026,12,1))==["2026-11"]
    assert touched_months(datetime(2026,10,20),datetime(2026,11,20))==["2026-10","2026-11"]
    assert touched_months(datetime(2026,11,1),datetime(2026,11,1))==[]
def test_dormant_certificate_full_coverage():
    "零访问且分区覆盖齐全 → 休眠证明，可回收"
    certs=evaluate([row("E1")],ctx([GRANT]))["rows"]
    assert len(certs)==1 and certs[0]["conclusion"]=="休眠证明" and certs[0]["action"]=="可回收"
    assert certs[0]["reasons"]==["ZERO_ACCESS_OBSERVED"] and certs[0]["coverage"]=="1/1"
def test_certificate_states_window_coverage_basis():
    c=evaluate([row("E1")],ctx([GRANT]))["rows"][0]
    assert c["window_start"]=="2026-09-01T00:00:00+00:00" and c["window_end"]=="2026-10-01T00:00:00+00:00"
    assert c["relevant_partitions"]==["2026-09"] and c["covered_partitions"]==["2026-09"] and c["gap_partitions"]==[]
def test_gap_partition_not_counted_dormant():
    "观察缺口分区不得计作已休眠"
    c=evaluate([],ctx([GRANT]))["rows"][0]
    assert c["conclusion"]=="待核凭证" and c["action"]=="人工复核"
    assert "OBSERVATION_GAP" in c["reasons"] and c["gap_partitions"]==["2026-09"]
def test_in_validity_access_is_basis():
    "有效期内的访问记为原始访问依据，结论转待核"
    c=evaluate([row("E1",at="2026-09-15T00:00:00Z",sources=(7,))],ctx([GRANT]))["rows"][0]
    assert c["conclusion"]=="待核凭证" and "ACCESS_IN_VALIDITY" in c["reasons"]
    assert c["basis_count"]==1 and c["basis"][0]["event_id"]=="E1" and c["basis"][0]["_sources"]==[7]
def test_post_expiry_access_is_evidence_not_renewal():
    "到期后访问记为越权证据，不续用授权、不改变窗口"
    c=evaluate([row("E1",at="2026-11-15T00:00:00Z")],ctx([GRANT]))["rows"][0]
    assert c["conclusion"]=="休眠证明" and c["post_expiry_count"]==1
    assert c["post_expiry"][0]["event_id"]=="E1" and c["validity_end"]=="2026-10-01T00:00:00+00:00"
def test_access_at_expiry_instant_is_post_expiry():
    "到期时刻本身属于到期后（半开生效窗）"
    c=evaluate([row("E1",at="2026-10-01T00:00:00Z")],ctx([GRANT]))["rows"][0]
    assert c["basis_count"]==0 and c["post_expiry_count"]==1 and c["conclusion"]=="休眠证明"
def test_pre_window_access_still_counts():
    "观察区间只取最近一个月时，有效期内更早的访问仍计入依据"
    g={**GRANT,"start":"2026-04-01T00:00:00Z","from_partition":"2026-04"}
    rows=[row("E1",partition="2026-05",at="2026-05-10T00:00:00Z"),row("E2",partition="2026-09",at="2026-11-01T00:00:00Z")]
    c=evaluate(rows,ctx([g]))["rows"][0]
    assert c["window_start"]=="2026-09-01T00:00:00+00:00" and c["pre_window_count"]==1
    assert c["conclusion"]=="待核凭证" and "ACCESS_IN_VALIDITY" in c["reasons"]
def test_full_validity_window():
    c=evaluate([row("E1")],ctx([GRANT],observe_months="full"))["rows"][0]
    assert c["window_start"]==c["validity_start"]
def test_unexpired_grant_gets_no_certificate():
    g={**GRANT,"end":"2027-01-01T00:00:00Z"}
    assert evaluate([row("E1")],ctx([g]))["rows"]==[]
def test_certificate_id_unique_per_grant_and_stable_on_retry():
    "同一授权重试不新增凭证：同输入同凭证号，一授权一凭证"
    grants=[GRANT,{**GRANT,"id":"G-T2","principal":"auditor","resource":"customers","from_partition":"2026-09","to_partition":"2026-10"}]
    rows=[row("E1")]
    first=evaluate(rows,ctx(grants))["rows"];second=evaluate(rows,ctx(grants))["rows"]
    assert [c["certificate_id"] for c in first]==[c["certificate_id"] for c in second]
    assert len({c["certificate_id"] for c in first})==2 and all(c["certificate_id"].startswith("DORM-") for c in first)
def test_bad_observe_months_rejected():
    for bad in (0,-1,True,"x"):
        with pytest.raises(DomainError): evaluate([],ctx([GRANT],observe_months=bad))
def test_real_sample_certificates(result):
    m=result["stages"][7]["metrics"]
    assert m["certificates"]==4 and m["dormant"]==1 and m["pending"]==3
def test_real_sample_dormant_grant_keeps_post_expiry_evidence(result):
    c=next(c for c in result["stages"][7]["rows"] if c["grant_id"]=="G-04")
    assert c["conclusion"]=="休眠证明" and c["post_expiry_count"]==8 and c["basis_count"]==0
def test_real_sample_gap_partitions_pending(result):
    c=next(c for c in result["stages"][7]["rows"] if c["grant_id"]=="G-01")
    assert c["conclusion"]=="待核凭证" and c["gap_partitions"]==["2026-11"] and c["basis_count"]==16
def test_real_sample_certificate_ids_unique(result):
    ids=[c["certificate_id"] for c in result["stages"][7]["rows"]]
    assert len(ids)==len(set(ids))
def test_retry_same_input_adds_no_certificates(sample):
    a=execute(sample);b=execute(sample)
    assert a["run_id"]==b["run_id"] and len(store.history())==1
    assert a["stages"][7]["rows"]==b["stages"][7]["rows"]
def test_interrupted_switch_keeps_old_certificates_and_access(client):
    "切换中断时保留旧版凭证和原始访问"
    p=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json()
    assert client.post("/api/runs",json={"csv":"bad"}).status_code==422
    assert len(store.history())==1
    kept=store.get(p["run_id"])
    assert len(kept["stages"][7]["rows"])==4 and len(kept["stages"][4]["rows"])>0
    client.post("/api/runs",json={"sample":"clean.csv"})
    assert len(store.history())==2 and store.get(p["run_id"])["stages"][7]["rows"]==kept["stages"][7]["rows"]
def test_export_uses_same_conclusion(client,sample):
    "分区复算与证据导出沿用同一结论"
    p=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json()
    r=client.get("/api/runs/"+p["run_id"]+"/archive")
    rows=list(csv.reader(io.StringIO(r.data.decode("utf-8-sig"))))
    certs=p["stages"][7]["rows"]
    for c in certs:
        hit=[x for x in rows[1:] if c["certificate_id"] in x[6]]
        assert len(hit)==1 and hit[0][3]==c["conclusion"]
    assert {x[3] for x in rows[1:]}>={"休眠证明","待核凭证"}
    again=calculate(sample)
    assert [c["conclusion"] for c in again["stages"][7]["rows"]]==[c["conclusion"] for c in certs]
    assert [c["certificate_id"] for c in again["stages"][7]["rows"]]==[c["certificate_id"] for c in certs]
