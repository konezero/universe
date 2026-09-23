"""Host-owned, receipt-aware edits proposed by one claimed bounded Worker.

The provider never gets receipt coordinates or credentials. The Host intersects
the live Task Frame with its own active Work Receipt, then uses the installed
file gateway. There is no raw-write or native-helper fallback.
"""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
import sys
import threading
from typing import Any, Callable, Mapping

MAX_TEXT_BYTES = 1024 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024
READ_WINDOW_CHARS = 16384
_EDIT_LOCK = threading.RLock()
TOOL = {'type': 'function', 'name': 'universe_edit', 'description': (
    'Host receipt-aware file editor for exact assigned targets only. Read first to obtain '
    'sha256; replace one exact old_text occurrence or create an absent file. '
    'Use this tool for all source edits. A blocked result must not be bypassed. '
    'Read returns a bounded text window and the SHA256 of the WHOLE file. '
    'For read use empty expected_sha256 and new_text; old_text may select a search snippet '
    '(empty means the beginning). Large files are not returned in full. '
    'For create use expected_sha256 ABSENT and empty old_text. Preserve text exactly.'),
    'inputSchema': {'type': 'object', 'additionalProperties': False,
        'required': ['operation', 'path', 'expected_sha256', 'old_text', 'new_text'],
        'properties': {'operation': {'type': 'string', 'enum': ['read', 'replace', 'create']},
            **{k: {'type': 'string'} for k in ('path', 'expected_sha256', 'old_text', 'new_text')}}}}


class EditBlocked(ValueError):
    pass


class TaskFrameFileEditor:
    def __init__(self, root: Path, *, session_id: str, frame_id: str, turn_id: str,
                 worker_id: str, scope: Mapping[str, Any], verify_claim: Callable[[], Any],
                 load_receipt: Callable[[], Mapping[str, Any]] | None = None,
                 apply_file: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None):
        self.root = root.resolve()
        self.session_id, self.frame_id, self.turn_id, self.worker_id = session_id, frame_id, turn_id, worker_id
        self.operations = frozenset(scope['operations'])
        self.targets = frozenset(str(Path(p).resolve()).casefold() for p in scope['targets'])
        self.verify_claim = verify_claim
        self.load_receipt = load_receipt or self._receipt
        self.apply_file = apply_file or self._apply
        self.calls: dict[str, tuple[str, dict[str, Any]]] = {}
        self.effect_uncertain = False

    def _runtime(self):
        runtime = str(self.root / '.ai/runtime')
        if runtime not in sys.path:
            sys.path.insert(0, runtime)
        from reference_runtime import anchor_receipt_runtime
        return anchor_receipt_runtime

    def _receipt(self):
        api = self._runtime()
        bundle = api._load_work_bundle(self.root, self.session_id)
        snapshot = bundle['snapshot']
        fresh = api._snapshot_from_mode_anchor(repo=self.root, session_id=self.session_id,
            mode=bundle['mode'], source_commit=snapshot['source_commit'],
            validation_ref=snapshot['evidence_refs']['validation'], coordinates=snapshot['coordinates'])
        if (snapshot['session_id'] != self.session_id or fresh['anchor_id'] != snapshot['anchor_id']):
            raise EditBlocked('HOST_EDIT_RECEIPT_STALE')
        return snapshot

    def _apply(self, payload):
        return self._runtime().apply_file(self.root, payload)

    def __call__(self, arguments: Mapping[str, Any], call_id: str) -> dict[str, Any]:
        with _EDIT_LOCK:
            try:
                if not call_id or not isinstance(arguments, Mapping):
                    raise EditBlocked('HOST_EDIT_REQUEST_INVALID')
                request_digest = hashlib.sha256(json.dumps(dict(arguments), sort_keys=True, ensure_ascii=True).encode()).hexdigest()
                if call_id in self.calls:
                    previous, result = self.calls[call_id]
                    if previous != request_digest:
                        raise EditBlocked('HOST_EDIT_REPLAY_CONFLICT')
                    return result
                result = self._execute(arguments)
                self.calls[call_id] = request_digest, result
                return result
            except Exception as error:
                # Never disclose credentials or unchecked exception text to the provider.
                code = (str(error) if isinstance(error, EditBlocked) else
                        getattr(error, 'error_code', 'HOST_EDIT_UNAVAILABLE'))
                return {'status': 'HOST_EDIT_BLOCKED', 'repository_write': False,
                        'error_code': code, 'error_type': type(error).__name__}

    def _execute(self, args):
        if set(args) != {'operation','path','expected_sha256','old_text','new_text'} or not all(isinstance(v,str) for v in args.values()):
            raise EditBlocked('HOST_EDIT_REQUEST_INVALID')
        if sum(len(v.encode('utf-8')) for v in args.values()) > MAX_TEXT_BYTES:
            raise EditBlocked('HOST_EDIT_INPUT_TOO_LARGE')
        operation = args['operation']
        if operation not in ('read','replace','create'):
            raise EditBlocked('HOST_EDIT_OPERATION_DENIED')
        raw = Path(args['path'])
        target = raw.resolve()
        if (not raw.is_absolute() or raw.is_symlink() or not target.is_relative_to(self.root)
                or str(target).casefold() not in self.targets):
            raise EditBlocked('HOST_EDIT_TARGET_DENIED')
        observed = self.verify_claim()
        if not isinstance(observed, Mapping) or (observed.get('turn_id'), observed.get('state'), observed.get('claimed_by')) != (self.turn_id,'CLAIMED',self.worker_id):
            raise EditBlocked('HOST_EDIT_CLAIM_STALE')
        if target.exists() and target.stat().st_size > MAX_FILE_BYTES:
            raise EditBlocked('HOST_EDIT_FILE_TOO_LARGE')
        before = target.read_bytes() if target.exists() else None
        if before is not None and len(before) > MAX_FILE_BYTES:
            raise EditBlocked('HOST_EDIT_FILE_TOO_LARGE')
        preimage = hashlib.sha256(before).hexdigest() if before is not None else 'ABSENT'
        if operation == 'read':
            text = before.decode('utf-8') if before is not None else None
            offset = 0
            if args['old_text']:
                match = text.find(args['old_text']) if text is not None else -1
                if match < 0:
                    raise EditBlocked('HOST_EDIT_READ_MATCH_NOT_FOUND')
                offset = max(0, match - 1024)
            window = text[offset:offset + READ_WINDOW_CHARS] if text is not None else None
            return {'status':'HOST_EDIT_READ','path':str(target),'sha256':preimage,
                    'text':window,'offset_chars':offset,
                    'total_chars':len(text) if text is not None else 0,
                    'truncated':text is not None and len(window) < len(text),
                    'repository_write':False}
        if self.effect_uncertain:
            return {'status':'HOST_EDIT_OUTCOME_UNKNOWN','repository_write':None,
                    'error_code':'HOST_EDIT_RECONCILIATION_REQUIRED'}
        required = 'CREATE' if operation == 'create' else 'MODIFY'
        if required not in self.operations or (operation == 'create') != (before is None):
            raise EditBlocked('HOST_EDIT_OPERATION_DENIED')
        if args['expected_sha256'] != preimage:
            raise EditBlocked('HOST_EDIT_PREIMAGE_CHANGED')
        if operation == 'replace':
            text = before.decode('utf-8')
            old, new = args['old_text'], args['new_text']
            if not old or text.count(old) != 1:
                raise EditBlocked('HOST_EDIT_EXACT_MATCH_REQUIRED')
            after = text.replace(old,new,1).encode('utf-8')
        else:
            if args['old_text']:
                raise EditBlocked('HOST_EDIT_REQUEST_INVALID')
            after = args['new_text'].encode('utf-8')
        if len(after) > MAX_FILE_BYTES:
            raise EditBlocked('HOST_EDIT_FILE_TOO_LARGE')
        snapshot = self.load_receipt()
        if snapshot.get('session_id') != self.session_id:
            raise EditBlocked('HOST_EDIT_RECEIPT_OWNER_MISMATCH')
        request = {k:snapshot[k] for k in ('session_id','frame_id','anchor_id','source_commit')}
        request.update({'validation_ref':snapshot['evidence_refs']['validation'],
            'operation':required,'target':str(target),'payload_sha256':hashlib.sha256(after).hexdigest(),
            'target_preimage':{'status':'PRESENT' if before is not None else 'ABSENT',
                               'sha256':preimage if before is not None else 'NONE'},
            'host_capability':{'filesystem_write':'AVAILABLE','pre_write_hook':'AVAILABLE',
                               'evidence_ref':'receipt-aware-text-edit://task-frame-host-editor'},
            'host_proposal_source':{'task_frame_id':self.frame_id,'turn_id':self.turn_id,'worker_id':self.worker_id}})
        try:
            result = dict(self.apply_file({'session_id':self.session_id,'request':request,
                             'content_base64':base64.b64encode(after).decode('ascii')}))
        except Exception as error:
            # The gateway may have written before its audit/response failed. Never
            # claim no mutation or retry the effect without reconciliation.
            self.effect_uncertain = True
            return {'status':'HOST_EDIT_OUTCOME_UNKNOWN','repository_write':None,
                    'error_code':'HOST_EDIT_GATEWAY_OUTCOME_UNKNOWN',
                    'error_type':type(error).__name__}
        return {k:result[k] for k in ('status','repository_write','reasons','audit') if k in result}
