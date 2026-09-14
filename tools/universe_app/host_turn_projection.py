"""Project Host-owned turn acceptance into Bus without sending input."""
from __future__ import annotations
from typing import Any

def reconcile(server: Any) -> dict[str, Any]:
    reader=getattr(server.terminal_host,"turn_delivery_status",None)
    if not callable(reader): return {"started":[],"errors":[]}
    started, errors = [], []
    host=server._session_anchor_terminal_host()
    for terminal in host.list_sessions():
        if terminal.get("state") != "LIVE" or terminal.get("provider") not in {"CODEX","GROK"}: continue
        tid=str(terminal.get("terminal_id") or "")
        anchor=str(terminal.get("session_anchor_ref") or terminal.get("active_session_anchor_ref") or "")
        if not tid or not anchor: continue
        messages=server.session_bus.inbox(host,session_anchor_ref=anchor,projection="ACTIVITY")["messages"]
        pending={m["message_id"]:m for m in messages if m.get("lifecycle_state")=="STARTED"
                 and (m.get("lifecycle") or {}).get("delivery_channel")=="HOST_TURN_DELIVERY"
                 and (m.get("lifecycle") or {}).get("execution_phase")!="RUNNING"}
        if not pending: continue
        try:
            state=reader(tid)
            for delivery in state.get("messages",[]):
                mid=delivery.get("message_id")
                if mid in pending and delivery.get("phase") in {"PROMPT_SUBMITTED", "STARTED"}:
                    if delivery["phase"] == "PROMPT_SUBMITTED" and (pending[mid].get("lifecycle") or {}).get("provider_received_at"):
                        continue
                    server.session_bus.acknowledge_instruction(mid,terminal_id=tid,session_anchor_ref=anchor,phase="STARTED" if delivery["phase"] == "STARTED" else "RECEIVED")
                    started.append(mid)
        except Exception as error:
            errors.append({"operation":"HOST_TURN_PROJECT","terminal_id":tid,
                           "error_code":getattr(error,"code","HOST_TURN_PROJECT_FAILED"),"detail":str(error)})
    return {"started":started,"errors":errors}
