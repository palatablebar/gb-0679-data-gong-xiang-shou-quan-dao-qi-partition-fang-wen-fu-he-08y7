import hashlib,json
from calendar import monthrange
from app.errors import DomainError
from app.values import instant
from app.modules.m3_grants.rules import GRANTS,validate_grants
def months_back(at,count):
    "at 往前 count 个月，日期裁剪到月末"
    index=at.year*12+at.month-1-count;year,month=divmod(index,12)
    return at.replace(year=year,month=month+1,day=min(at.day,monthrange(year,month+1)[1]))
def touched_months(start,end):
    "[start,end) 半开区间触到的 YYYY-MM 月份"
    if end<=start: return []
    last=months_back(end,1) if (end.day,end.hour,end.minute,end.second,end.microsecond)==(1,0,0,0,0) else end
    months=[];year,month=start.year,start.month
    while (year,month)<=(last.year,last.month):
        months.append(f"{year:04d}-{month:02d}");year,month=(year+1,1) if month==12 else (year,month+1)
    return months
def evidence(row):
    return {"event_id":row.get("event_id",""),"access_time":row.get("at") or row.get("access_time",""),"partition":row.get("partition",""),"observed":row.get("observed",""),"violation":row.get("violation",""),"_sources":sorted(row.get("_sources",[]))}
def certificate_id(seed,grant_id): return "DORM-"+hashlib.sha256(f"{seed}|{grant_id}".encode()).hexdigest()[:16]
def evaluate(rows,context):
    params=context.get("parameters",{}) or {}
    grants=validate_grants(params.get("grants",GRANTS))
    as_of=instant(params["as_of"]) if params.get("as_of") else (max((instant(g["end"]) for g in grants),default=None))
    observe=params.get("observe_months",1)
    if observe is None: observe=1
    full=observe=="full"
    if not full and (not isinstance(observe,int) or isinstance(observe,bool) or observe<1): raise DomainError("BAD_OBSERVE_MONTHS","观察月数需要正整数或 full")
    seed=context.get("run_id") or hashlib.sha256(json.dumps([params,sorted(str(r.get("event_id","")) for r in rows)],sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:20]
    certs=[]
    if as_of is not None:
        present={(row.get("resource"),row.get("partition")) for row in rows}
        for g in grants:
            start,end=instant(g["start"]),instant(g["end"])
            if end>as_of: continue
            w_end=min(end,as_of);w_start=start if full else max(start,months_back(w_end,observe))
            relevant=[p for p in touched_months(w_start,w_end) if g["from_partition"]<=p<g["to_partition"]]
            covered=[p for p in relevant if (g["resource"],p) in present]
            gaps=[p for p in relevant if p not in covered]
            basis=[];post=[]
            for row in rows:
                if row.get("principal")!=g["principal"] or row.get("resource")!=g["resource"]: continue
                if not (g["from_partition"]<=row.get("partition","")<g["to_partition"]): continue
                at=instant(row.get("at") or row.get("access_time"))
                if start<=at<end: basis.append((at,row))
                elif at>=end: post.append((at,row))
            basis.sort(key=lambda pair:(pair[0],str(pair[1].get("event_id",""))));post.sort(key=lambda pair:(pair[0],str(pair[1].get("event_id",""))))
            in_window=sum(1 for at,_ in basis if w_start<=at<w_end)
            reasons=[]
            if gaps: reasons.append("OBSERVATION_GAP")
            if basis: reasons.append("ACCESS_IN_VALIDITY")
            conclusion="待核凭证" if reasons else "休眠证明"
            certs.append({"certificate_id":certificate_id(seed,g["id"]),"grant_id":g["id"],"principal":g["principal"],"resource":g["resource"],"effect":g["effect"],
                "validity_start":start.isoformat(),"validity_end":end.isoformat(),"as_of":as_of.isoformat(),
                "window_start":w_start.isoformat(),"window_end":w_end.isoformat(),"window":f"{w_start.date().isoformat()}→{w_end.date().isoformat()}",
                "partition_window":f"{g['from_partition']}→{g['to_partition']}",
                "relevant_partitions":relevant,"covered_partitions":covered,"gap_partitions":gaps,"coverage":f"{len(covered)}/{len(relevant)}",
                "basis":[evidence(r) for _,r in basis],"basis_count":len(basis),"in_window_count":in_window,"pre_window_count":len(basis)-in_window,
                "post_expiry":[evidence(r) for _,r in post],"post_expiry_count":len(post),
                "conclusion":conclusion,"reasons":reasons or ["ZERO_ACCESS_OBSERVED"],"action":"人工复核" if reasons else "可回收",
                "_sources":sorted({s for _,r in basis+post for s in r.get("_sources",[])})})
    metrics={"certificates":len(certs),"dormant":sum(c["conclusion"]=="休眠证明" for c in certs),"pending":sum(c["conclusion"]=="待核凭证" for c in certs),"gap_partitions":sum(len(c["gap_partitions"]) for c in certs),"post_expiry_events":sum(c["post_expiry_count"] for c in certs)}
    return {"rows":certs,"quarantine":[],"metrics":metrics}
