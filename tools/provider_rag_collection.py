"""Bounded RAG history and source recovery; never rewind live observer cursors."""
import json
import time
from pathlib import Path


def initialize_history(connection):
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS provider_rag_history_activity AS
            SELECT * FROM provider_session_activity WHERE 0;
        CREATE UNIQUE INDEX IF NOT EXISTS provider_rag_history_id ON provider_rag_history_activity(activity_id);
        CREATE UNIQUE INDEX IF NOT EXISTS provider_rag_history_offset ON provider_rag_history_activity(source_id, byte_offset);
        CREATE TABLE IF NOT EXISTS provider_rag_history_cursor (
            source_id TEXT PRIMARY KEY, file_identity TEXT NOT NULL,
            stop_offset INTEGER NOT NULL, next_offset INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL, error_code TEXT, error_offset INTEGER);
        CREATE TABLE IF NOT EXISTS provider_rag_maintenance (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), next_source_id TEXT);
    """)
    columns={row["name"] for row in connection.execute("PRAGMA table_info(provider_rag_maintenance)")}
    if "last_report_json" not in columns:
        connection.execute("ALTER TABLE provider_rag_maintenance ADD COLUMN last_report_json TEXT")


def maintenance_status(observer):
    with observer._connection() as c:
        row=c.execute("SELECT last_report_json FROM provider_rag_maintenance WHERE singleton=1").fetchone()
    return json.loads(row[0]) if row and row[0] else None


def backfill_source(observer, source_id, *, max_events=512, max_bytes=262144):
    from provider_session_observer import (_file_identity, MAX_SINGLE_EVENT_BYTE_LIMIT,
                                           ProviderSessionObserverError)
    started=time.monotonic()
    with observer._connection() as c:
        c.execute("BEGIN IMMEDIATE")
        source=c.execute("SELECT * FROM provider_session_source WHERE source_id=?",(source_id,)).fetchone()
        if source is None:raise ProviderSessionObserverError("SOURCE_NOT_FOUND",source_id)
        path=Path(source["source_path"])
        if not path.is_file():return {"state":"BLOCKED","error_code":"SOURCE_MISSING","added":0}
        identity=_file_identity(path)
        if source["file_identity"] != identity:
            return {"state":"BLOCKED","error_code":"SOURCE_ROTATED","added":0}
        cursor=c.execute("SELECT * FROM provider_rag_history_cursor WHERE source_id=?",(source_id,)).fetchone()
        if cursor is None:
            first=c.execute("SELECT MIN(byte_offset) FROM provider_session_activity WHERE source_id=?",(source_id,)).fetchone()[0]
            stop=int(first if first is not None else source["cursor_offset"])
            c.execute("INSERT INTO provider_rag_history_cursor(source_id,file_identity,stop_offset,state) VALUES (?,?,?,?)",(source_id,identity,stop,"PENDING"))
            cursor=c.execute("SELECT * FROM provider_rag_history_cursor WHERE source_id=?",(source_id,)).fetchone()
        offset=cursor["next_offset"];stop=cursor["stop_offset"]
        if cursor["file_identity"] != identity or path.stat().st_size < stop:
            return {"state":"BLOCKED","error_code":"SOURCE_ROTATED","added":0}
        added=0;scanned=0;read=0;code=None;error_offset=None
        with path.open("rb") as f:
            f.seek(offset)
            while offset < stop and scanned < max_events and (not scanned or time.monotonic()-started < .25):
                start=f.tell();raw=f.readline(MAX_SINGLE_EVENT_BYTE_LIMIT+1)
                if len(raw)>MAX_SINGLE_EVENT_BYTE_LIMIT:code="SOURCE_EVENT_TOO_LARGE";error_offset=start;break
                if not raw.endswith(b"\n") or f.tell()>stop:code="SOURCE_HISTORY_BOUNDARY_INVALID";error_offset=start;break
                if scanned and read+len(raw)>max_bytes:break
                try:
                    event=json.loads(raw.decode("utf-8"))
                    if not isinstance(event,dict):raise ValueError("object required")
                    # Byte positions give history immutable positive ordinals without
                    # changing the ordinals attested by the live observer.
                    if observer._reduce_event(c,source,event,start+1,start,history=True):added+=1
                except (UnicodeDecodeError,ValueError) as error:
                    code=getattr(error,"code","SOURCE_SCHEMA_UNSUPPORTED");error_offset=start;break
                offset=f.tell();scanned+=1;read+=len(raw)
        state="BLOCKED" if code else "COMPLETED" if offset>=stop else "PENDING"
        c.execute("UPDATE provider_rag_history_cursor SET next_offset=?,state=?,error_code=?,error_offset=? WHERE source_id=?",(offset,state,code,error_offset,source_id))
        return {"state":state,"error_code":code,"error_offset":error_offset,"added":added,"next_offset":offset,"stop_offset":stop}


def recover_missing_source(observer, source_id):
    from provider_session_observer import _file_identity, _bounded_session_metadata
    with observer._connection() as c:
        c.execute("BEGIN IMMEDIATE")
        row=c.execute("SELECT * FROM provider_session_source WHERE source_id=?",(source_id,)).fetchone()
        if row is None or Path(row["source_path"]).is_file():return "NOT_NEEDED"
        old=Path(row["source_path"])
        identity=str(row["file_identity"] or "")
        if not identity or identity.endswith(":0"):return "SOURCE_IDENTITY_UNAVAILABLE"
        root=observer._default_provider_home(row["provider"])
        pattern="**/"+old.name if row["provider"]!="GROK" else "**/"+old.parent.name+"/updates.jsonl"
        matches=[]
        if row["provider"] == "CODEX":
            from itertools import chain
            paths=chain((root/"archived_sessions").glob(old.name),(root/"sessions").glob("**/"+old.name))
        else:
            paths=root.glob(pattern)
        for i,path in enumerate(paths):
            if i>=5000:return "SOURCE_DISCOVERY_LIMIT"
            try:
                if path.is_file() and _file_identity(path)==identity:matches.append(path)
            except OSError:continue
        if len(matches)!=1:return "SOURCE_MISSING" if not matches else "SOURCE_LOCATION_AMBIGUOUS"
        path=matches[0]
        metadata=_bounded_session_metadata(row["provider"],path)
        if metadata.get("identity_state")!="VERIFIED" or metadata.get("provider_session_id")!=row["provider_session_id"]:
            return "SOURCE_IDENTITY_MISMATCH"
        duplicate=c.execute("SELECT 1 FROM provider_session_source WHERE provider=? AND provider_session_id=? AND source_path=? AND source_id<>?",(row["provider"],row["provider_session_id"],str(path),source_id)).fetchone()
        if duplicate:return "SOURCE_LOCATION_ALREADY_REGISTERED"
        c.execute("UPDATE provider_session_source SET source_path=? WHERE source_id=?",(str(path),source_id))
        return "RELOCATED"


def prepare_sources(observer, source_ids=None):
    inventory=[s for s in observer.list_sources() if s["enabled"]]
    rows=sorted(inventory,key=lambda s:s["source_id"])
    if source_ids is not None:
        wanted=set(source_ids)
        missing=wanted-{s["source_id"] for s in rows}
        if missing:
            from provider_session_observer import ProviderSessionObserverError
            raise ProviderSessionObserverError("SOURCE_NOT_FOUND", sorted(missing)[0])
        rows=[s for s in rows if s["source_id"] in wanted][:8]
    else:
        with observer._connection() as c:
            pos=c.execute("SELECT next_source_id FROM provider_rag_maintenance WHERE singleton=1").fetchone()
        if pos:
            ids=[s["source_id"] for s in rows]
            if pos[0] in ids:
                i=ids.index(pos[0]);rows=rows[i:]+rows[:i]
    selected=rows[:8];results=[]
    for source in selected:
        # Retry with the current parser at the existing exact cursor. Missing,
        # replaced, and malformed sources retain their typed failure state.
        recovery=recover_missing_source(observer,source["source_id"]) if source.get("reason")=="SOURCE_MISSING" else "NOT_NEEDED"
        scan=observer.scan(source["source_id"])
        current=scan["source"]
        history=backfill_source(observer,source["source_id"]) if current["status"]=="ACTIVE" else {"state":"BLOCKED","error_code":current["reason"],"added":0}
        results.append({"source_id":source["source_id"],"provider":source["provider"],"status":current["status"],"observed_added":scan["added"],"error_code":current.get("reason"),"recovery":recovery,"history":history})
    report={"schema":"universe.rag-source-maintenance.v1","processed_count":len(results),"sources":results}
    if source_ids is None and rows:
        next_id=rows[len(selected)%len(rows)]["source_id"]
        with observer._connection() as c:
            c.execute("INSERT INTO provider_rag_maintenance(singleton,next_source_id,last_report_json) VALUES(1,?,?) ON CONFLICT(singleton) DO UPDATE SET next_source_id=excluded.next_source_id,last_report_json=excluded.last_report_json",(next_id,json.dumps(report)))
    else:
        with observer._connection() as c:
            c.execute("INSERT INTO provider_rag_maintenance(singleton,last_report_json) VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET last_report_json=excluded.last_report_json",(json.dumps(report),))
    return report
