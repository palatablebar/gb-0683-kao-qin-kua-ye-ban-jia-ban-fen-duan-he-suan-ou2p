import io,csv,json,hashlib

def bundle(state):
    out=io.StringIO(newline='');writer=csv.writer(out)
    writer.writerow(['记录类型','批次','来源行','业务ID','原因码','内容'])
    rid=state.get('run_id','preview')
    writer.writerow(['运行清单',rid,'','','',json.dumps(state.get('manifest',{}),ensure_ascii=False,sort_keys=True)])
    writer.writerow(['对账',rid,'','','',json.dumps(state.get('reconcile',{}),ensure_ascii=False,sort_keys=True)])
    for row in state.get('calculations',[]):writer.writerow(['计算',rid,row.get('line',''),row.get('id',''),row.get('code',''),json.dumps(row,ensure_ascii=False,sort_keys=True)])
    for row in state['quarantine']:writer.writerow(['隔离',rid,row['line'],row.get('id',''),row['code'],json.dumps(row.get('raw',{}),ensure_ascii=False,sort_keys=True)])
    return '\ufeff'+out.getvalue()

def checksum(state):
    return hashlib.sha256(bundle(state).encode('utf-8')).hexdigest()

def period_bundle(overview,state):
    # 期间证据：接收批次、班次状态、计算、隔离、对账五类记录，可回查来源。
    out=io.StringIO(newline='');writer=csv.writer(out)
    writer.writerow(['记录类型','批次','来源行','业务ID','原因码','内容'])
    pid=overview['period']['period_id']
    writer.writerow(['运行清单',pid,'','','',json.dumps({'run_id':overview['period']['run_id'],'created_at':overview['period']['created_at'],'status':'current'},ensure_ascii=False,sort_keys=True)])
    for r in overview['receipts']:
        writer.writerow(['接收',pid,'',r['receipt_id'],r['status'],json.dumps({k:r[k] for k in ('source','sha256','received_at','bytes','stats','superseded_at')},ensure_ascii=False,sort_keys=True)])
    for s in overview['shifts']:
        writer.writerow(['班次',pid,'',s['id'],s['status'],json.dumps({'employee':s['employee'],'day':s['day'],'missing':s['missing'],'amount':s['amount'],'receipts':s['receipts']},ensure_ascii=False,sort_keys=True)])
    for row in state.get('calculations',[]):writer.writerow(['计算',pid,row.get('line',''),row.get('id',''),row.get('code',''),json.dumps(row,ensure_ascii=False,sort_keys=True)])
    for row in state['quarantine']:writer.writerow(['隔离',pid,row['line'],row.get('id',''),row['code'],json.dumps(row.get('raw',{}),ensure_ascii=False,sort_keys=True)])
    writer.writerow(['对账',pid,'','','',json.dumps(state.get('reconcile',{}),ensure_ascii=False,sort_keys=True)])
    return '\ufeff'+out.getvalue()
