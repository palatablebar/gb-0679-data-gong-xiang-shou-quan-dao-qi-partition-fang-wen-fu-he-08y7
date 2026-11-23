import csv,io,json
def build_archive(run):
    stream=io.StringIO(newline="");writer=csv.writer(stream)
    writer.writerow(["run_id","input_sha256","rule_version","kind","source_lines","reason","evidence"])
    meta=run["manifest"]
    audit=run["stages"][5]
    for row in audit["rows"]:
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"accepted",json.dumps(row.get("_sources",[])),row.get("status",""),json.dumps(row,ensure_ascii=False)])
    for row in audit.get("cumulative_quarantine",[]):
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"quarantine",json.dumps(row.get("_sources",[])),row.get("reason",""),json.dumps(row,ensure_ascii=False)])
    for stage in run["stages"][7:]:
        for cert in stage.get("rows",[]):
            writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],cert.get("conclusion",""),json.dumps(cert.get("_sources",[])),";".join(cert.get("reasons",[])),json.dumps(cert,ensure_ascii=False)])
    return "﻿"+stream.getvalue()
