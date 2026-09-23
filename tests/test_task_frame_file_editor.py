import base64
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from task_frame_file_editor import TaskFrameFileEditor


class HostEditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.path=self.root/'file.txt'
        self.path.write_bytes('before\r\n한글\r\n'.encode())
        self.claim=Mock(return_value={'turn_id':'turn','state':'CLAIMED','claimed_by':'worker'})
        self.receipt=Mock(return_value={'session_id':'session','frame_id':'current','anchor_id':'anchor',
            'source_commit':'a'*40,'evidence_refs':{'validation':'validation'}})
        self.gateway=Mock(return_value={'status':'FILE_MUTATION_APPLIED','repository_write':True,'audit':{'phase':'SUCCEEDED'}})
        self.editor=TaskFrameFileEditor(self.root,session_id='session',frame_id='frame',turn_id='turn',worker_id='worker',
            scope={'operations':['MODIFY','CREATE'],'targets':[str(self.path),str(self.root/'new.txt')]},
            verify_claim=self.claim,load_receipt=self.receipt,apply_file=self.gateway)
    def args(self,**changes):
        return dict({'operation':'replace','path':str(self.path),'expected_sha256':hashlib.sha256(self.path.read_bytes()).hexdigest(),
                     'old_text':'before','new_text':'after'},**changes)
    def test_exact_replacement_uses_gateway_preserves_other_bytes_and_binds_preimage(self):
        r=self.editor(self.args(),'one');self.assertTrue(r['repository_write'])
        p=self.gateway.call_args.args[0]
        self.assertEqual('after\r\n한글\r\n'.encode(),base64.b64decode(p['content_base64']))
        self.assertEqual(self.args()['expected_sha256'],p['request']['target_preimage']['sha256'])
        self.assertEqual('worker',p['request']['host_proposal_source']['worker_id'])
        self.assertEqual('session',p['request']['session_id'])
    def test_pinned_read_never_calls_mutation_gateway(self):
        r=self.editor(self.args(operation='read',expected_sha256='',old_text='',new_text=''),'one')
        self.assertEqual('HOST_EDIT_READ',r['status']);self.gateway.assert_not_called()
        self.assertEqual(self.path.read_bytes().decode(),r['text'])
    def test_exact_call_replay_and_conflicting_call(self):
        first=self.editor(self.args(),'one')
        self.assertEqual(first,self.editor(self.args(),'one'));self.gateway.assert_called_once()
        self.assertEqual('HOST_EDIT_REPLAY_CONFLICT',self.editor(self.args(new_text='other'),'one')['error_code'])
    def test_stale_claim_missing_receipt_and_owner_mismatch_block(self):
        self.claim.return_value['state']='COMPLETED'
        self.assertEqual('HOST_EDIT_CLAIM_STALE',self.editor(self.args(),'one')['error_code'])
        self.claim.return_value['state']='CLAIMED';self.receipt.side_effect=FileNotFoundError()
        self.assertEqual('HOST_EDIT_UNAVAILABLE',self.editor(self.args(),'two')['error_code'])
        self.receipt.side_effect=None;self.receipt.return_value['session_id']='someone_else'
        self.assertEqual('HOST_EDIT_RECEIPT_OWNER_MISMATCH',self.editor(self.args(),'three')['error_code'])
        self.gateway.assert_not_called()
    def test_scope_operation_preimage_and_ambiguous_match_fail_closed(self):
        for change,code in [({'path':str(self.root/'other.txt')},'HOST_EDIT_TARGET_DENIED'),
                            ({'operation':'delete'},'HOST_EDIT_OPERATION_DENIED'),
                            ({'expected_sha256':'0'*64},'HOST_EDIT_PREIMAGE_CHANGED'),
                            ({'old_text':'missing'},'HOST_EDIT_EXACT_MATCH_REQUIRED')]:
            with self.subTest(code=code):self.assertEqual(code,self.editor(self.args(**change),code)['error_code'])
        self.gateway.assert_not_called()
    def test_create_requires_absence_and_create_scope(self):
        args=self.args(operation='create',path=str(self.root/'new.txt'),expected_sha256='ABSENT',old_text='',new_text='new')
        self.assertTrue(self.editor(args,'one')['repository_write'])
        self.assertEqual({'status':'ABSENT','sha256':'NONE'},self.gateway.call_args.args[0]['request']['target_preimage'])
        self.editor.operations=frozenset(['MODIFY'])
        self.assertEqual('HOST_EDIT_OPERATION_DENIED',self.editor(args,'two')['error_code'])
    def test_large_file_read_is_windowed_but_edit_hash_covers_whole_file(self):
        before = ('x' * (2 * 1024 * 1024) + '\nunique tail\n').encode()
        self.path.write_bytes(before)
        r = self.editor(self.args(operation='read',old_text='unique tail',new_text=''),'read')
        self.assertTrue(r['truncated'])
        self.assertIn('unique tail',r['text'])
        self.assertLessEqual(len(r['text']),16384)
        self.assertEqual(hashlib.sha256(before).hexdigest(),r['sha256'])
        r = self.editor(self.args(old_text='unique tail',new_text='replaced tail'),'replace')
        self.assertTrue(r['repository_write'])
        self.assertEqual(before.replace(b'unique tail',b'replaced tail'),
                         base64.b64decode(self.gateway.call_args.args[0]['content_base64']))

    def test_gateway_exception_has_unknown_outcome_and_requires_reconciliation(self):
        self.gateway.side_effect = OSError('potentially sensitive transport detail')
        args = self.args()
        result = self.editor(args,'first')
        self.assertEqual('HOST_EDIT_OUTCOME_UNKNOWN',result['status'])
        self.assertIsNone(result['repository_write'])
        self.assertEqual(result,self.editor(args,'first'))
        self.assertEqual('HOST_EDIT_RECONCILIATION_REQUIRED',self.editor(args,'second')['error_code'])
        self.gateway.assert_called_once()
        self.assertNotIn('sensitive',str(result))
        self.assertEqual('HOST_EDIT_READ',self.editor(self.args(operation='read',old_text=''),'read')['status'])

    def test_gateway_refusal_is_not_reported_as_success_or_retried(self):
        self.gateway.return_value={'status':'FILE_MUTATION_BLOCKED','repository_write':False,'reasons':['SCOPE_DENIED']}
        r=self.editor(self.args(),'one');self.assertEqual('FILE_MUTATION_BLOCKED',r['status'])
        self.assertEqual(r,self.editor(self.args(),'one'));self.gateway.assert_called_once()

if __name__=='__main__':unittest.main()
