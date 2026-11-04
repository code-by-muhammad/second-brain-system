"""Pure controls for the read-only metadata inspector; no native manager work."""
from pathlib import Path
import runpy
import stat
from types import SimpleNamespace
import unittest
from unittest import mock

code=runpy.run_path(str(Path(__file__).with_name('native_linux_ownership.py')))

class OwnershipControls(unittest.TestCase):
    def test_exact_guard_uid_and_write_bits(self):
        predicate=code['guarded']
        for mode in (0o400,0o444,0o555,0o600,0o644,0o700,0o755):self.assertTrue(predicate(mode,0))
        for mode in (0o622,0o664,0o775,0o777,0o1777):self.assertFalse(predicate(mode,0))
        for uid in (1,1000,65534):self.assertFalse(predicate(0o755,uid))

    def test_order_matches_selected_then_parents(self):
        seen=[]
        def probe(path):
            seen.append(path);return {'path':path,'exists':True,'guard_uid_mode_ok':path!='/opt'}
        result=code['chain']('/opt/fixture/software',probe)
        self.assertEqual(seen,['/opt/fixture/software','/opt/fixture','/opt','/'])
        self.assertEqual(result['unsafe_existing_ancestors'],['/opt'])
        self.assertTrue(result['complete_existing_chain'])

    def test_missing_target_never_claims_complete_guard(self):
        def probe(path):return {'path':path,'exists':path=='/','guard_uid_mode_ok':True}
        result=code['chain']('/opt/fixture',probe)
        self.assertFalse(result['complete_existing_chain'])
        self.assertEqual(result['unsafe_existing_ancestors'],[])

    def test_metadata_error_not_silently_complete(self):
        def probe(path):return {'path':path,'error_type':'PermissionError'}
        result=code['chain']('/usr/bin/python3.12',probe)
        self.assertFalse(result['complete_existing_chain'])

    def test_relative_and_traversal_refused_before_probe(self):
        def forbidden(path):raise AssertionError('no metadata read allowed')
        for path in ('relative','/opt/../etc'):
            with self.assertRaises(ValueError):code['chain'](path,forbidden)

    def test_symlink_is_plain_refusal_not_uid_mode_culprit(self):
        observe=code['inspect'];operating=observe.__globals__['os']
        link=SimpleNamespace(st_mode=stat.S_IFLNK|0o777,st_uid=0,st_gid=0)
        target=SimpleNamespace(st_mode=stat.S_IFREG|0o755,st_uid=0,st_gid=0)
        with mock.patch.object(operating,'lstat',return_value=link),mock.patch.object(operating,'stat',return_value=target),mock.patch.object(operating,'readlink',return_value='python3.12'):
            row=observe('/usr/bin/python')
        self.assertIsNone(row['guard_uid_mode_ok'])
        self.assertTrue(row['harness_plain_rejects'])
        self.assertEqual(row['symlink_target'],'python3.12')
        self.assertTrue(row['stat']['guard_uid_mode_ok'])
        result=code['chain']('/usr/bin/python',lambda path:dict(row,path=path))
        self.assertEqual(result['unsafe_existing_ancestors'],[])

if __name__=='__main__':unittest.main()
