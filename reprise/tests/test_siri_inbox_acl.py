"""Darwin ACL checks on synthetic descriptors and disposable filesystem fixtures."""
import ctypes as C
import errno
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid
sys.path.insert(0, str(Path(__file__).parents[1]))
import siri_inbox_acl as acl
import siri_inbox_store as store
from test_siri_inbox_store import crypt


class ACLDoubleTests(unittest.TestCase):
    def library(self, pointer=None, number=0):
        library=Mock()
        def get(*args): C.set_errno(number); return pointer
        library.acl_get_fd_np.side_effect=get
        library.acl_valid.return_value=0
        library.acl_get_entry.return_value=0
        library.acl_free.return_value=0
        return library

    def test_absence_only_accepts_enoent_and_requires_valid_descriptor(self):
        for number in (0,errno.EACCES,errno.EBADF,errno.EINVAL,errno.ENOMEM,errno.EOPNOTSUPP,errno.ENOENT):
            library=self.library(number=number)
            with self.subTest(number=number),patch.object(acl,'_library',return_value=library),patch.object(acl.os,'fstat') as stat:
                if number==errno.ENOENT:
                    acl.require_no_extended_acl(8); self.assertEqual(stat.call_count,2)
                else:
                    with self.assertRaises(acl.ACLUnavailable): acl.require_no_extended_acl(8)
            library.acl_free.assert_not_called()
        with patch.object(acl.os,'fstat',side_effect=OSError('closed')),patch.object(acl,'_library') as load:
            with self.assertRaises(acl.ACLUnavailable): acl.require_no_extended_acl(8)
            load.assert_not_called()

    def test_present_invalid_and_unexpected_results_fail_closed_and_free_once(self):
        for valid,entry,expected in ((0,0,acl.ACLPresent),(0,-1,acl.ACLUnavailable),(0,1,acl.ACLUnavailable),(-1,0,acl.ACLUnavailable)):
            library=self.library(pointer=123)
            library.acl_valid.return_value=valid; library.acl_get_entry.return_value=entry
            with self.subTest(valid=valid,entry=entry),patch.object(acl,'_library',return_value=library),patch.object(acl.os,'fstat'):
                with self.assertRaises(expected): acl.require_no_extended_acl(8)
            library.acl_free.assert_called_once_with(123)

    def test_free_failure_preserves_primary_refusal(self):
        library=self.library(pointer=123); library.acl_free.return_value=-1
        with patch.object(acl,'_library',return_value=library),patch.object(acl.os,'fstat'):
            with self.assertRaises(acl.ACLPresent): acl.require_no_extended_acl(8)

    def test_non_darwin_missing_library_or_symbol_do_not_fallback(self):
        with patch.object(acl.sys,'platform','linux'),patch.object(acl.ctypes,'CDLL') as load:
            with self.assertRaises(acl.ACLUnavailable): acl._library()
            load.assert_not_called()
        with patch.object(acl.sys,'platform','darwin'),patch.object(acl.ctypes,'CDLL',side_effect=OSError('absent')):
            with self.assertRaises(acl.ACLUnavailable): acl._library()
        with patch.object(acl.sys,'platform','darwin'),patch.object(acl.ctypes,'CDLL',return_value=object()):
            with self.assertRaises(acl.ACLUnavailable): acl._library()


@unittest.skipUnless(sys.platform=='darwin','Darwin system ACL fixture')
class ACLDarwinTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='reprise-144-acl-',dir='/private/tmp')
        self.addCleanup(self.tmp.cleanup); self.root=Path(self.tmp.name)
        self.library=acl._library(); P=C.c_void_p; I=C.c_int; PP=C.POINTER(P)
        for name,args,result in [('acl_init',[I],P),('acl_create_entry',[PP,PP],I),('acl_set_tag_type',[P,I],I),
                                ('acl_set_qualifier',[P,P],I),('acl_get_permset',[P,PP],I),('acl_add_perm',[P,I],I),
                                ('acl_get_flagset_np',[P,PP],I),('acl_add_flag_np',[P,I],I),('acl_set_fd_np',[I,P,I],I)]:
            function=getattr(self.library,name); function.argtypes=args; function.restype=result

    def install_fixture_acl(self,fd,*,deny=False,inherit=False):
        P=C.c_void_p; value=P(self.library.acl_init(1)); self.assertTrue(value)
        try:
            entry=P(); self.assertEqual(self.library.acl_create_entry(C.byref(value),C.byref(entry)),0)
            self.assertEqual(self.library.acl_set_tag_type(entry,2 if deny else 1),0)
            qualifier=C.create_string_buffer(uuid.UUID('abcdefab-cdef-abcd-efab-cdef0000000c').bytes)
            self.assertEqual(self.library.acl_set_qualifier(entry,C.cast(qualifier,P)),0)
            permissions=P(); self.assertEqual(self.library.acl_get_permset(entry,C.byref(permissions)),0)
            self.assertEqual(self.library.acl_add_perm(permissions,2),0)
            if inherit:
                flags=P(); self.assertEqual(self.library.acl_get_flagset_np(entry,C.byref(flags)),0)
                self.assertEqual(self.library.acl_add_flag_np(flags,1<<5),0)
            C.set_errno(0); result=self.library.acl_set_fd_np(fd,value,acl.ACL_TYPE_EXTENDED)
            if result!=0:
                self.fail('Fixture ACL installation failed once, errno='+str(C.get_errno()))
        finally:
            self.assertEqual(self.library.acl_free(value),0)

    def test_no_acl_accepts_and_invalid_descriptor_refuses(self):
        fd=os.open(self.root/'plain',os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
        try:
            before=os.fstat(fd); acl.require_no_extended_acl(fd); after=os.fstat(fd)
            self.assertEqual((before.st_mode,before.st_uid,before.st_ino),(after.st_mode,after.st_uid,after.st_ino))
        finally: os.close(fd)
        with self.assertRaises(acl.ACLUnavailable): acl.require_no_extended_acl(-1)

    def test_explicit_allow_and_deny_are_refused_without_acl_mutation(self):
        for deny in (False,True):
            fd=os.open(self.root/('deny' if deny else 'allow'),os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
            try:
                self.install_fixture_acl(fd,deny=deny)
                before=os.fstat(fd)
                for _ in range(2):
                    with self.assertRaises(acl.ACLPresent): acl.require_no_extended_acl(fd)
                after=os.fstat(fd)
                self.assertEqual((before.st_mode,before.st_ctime_ns),(after.st_mode,after.st_ctime_ns))
            finally: os.close(fd)

    def test_inherited_acl_is_refused(self):
        parent=self.root/'parent'; parent.mkdir(mode=0o700)
        fd=os.open(parent,os.O_RDONLY|os.O_DIRECTORY)
        try: self.install_fixture_acl(fd,inherit=True)
        finally: os.close(fd)
        child=os.open(parent/'child',os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
        try:
            with self.assertRaises(acl.ACLPresent): acl.require_no_extended_acl(child)
        finally: os.close(child)

    def test_store_inspects_each_descriptor_and_rejects_acl_root_before_creation(self):
        seen=[]
        def inspect(fd):
            seen.append(os.fstat(fd).st_ino); acl.require_no_extended_acl(fd)
        with patch.object(store,'require_no_extended_acl',side_effect=inspect):
            box=store.Inbox(self.root,crypt=crypt)
            ident=str(uuid.uuid4()); box.put(ident,'synthetic',now=1); box.list()
        expected=[self.root,self.root/'siri-inbox.noindex',self.root/'siri-inbox.noindex/inbox.lock',
                  self.root/'siri-inbox.noindex/initialized',self.root/'siri-inbox.noindex/inbox.enc']
        self.assertTrue({path.stat().st_ino for path in expected}.issubset(set(seen)))
        other=self.root/'refused';other.mkdir(mode=0o700)
        fd=os.open(other,os.O_RDONLY|os.O_DIRECTORY)
        try: self.install_fixture_acl(fd)
        finally: os.close(fd)
        with self.assertRaises(acl.ACLPresent): store.Inbox(other,crypt=crypt)
        self.assertFalse((other/'siri-inbox.noindex').exists())

    def test_path_replacement_does_not_redirect_descriptor_inspection(self):
        path=self.root/'selected'; original=os.open(path,os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
        replacement=None
        try:
            self.install_fixture_acl(original)
            original_inode=os.fstat(original).st_ino
            path.rename(self.root/'parked')
            replacement=os.open(path,os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
            self.assertNotEqual(path.stat().st_ino,original_inode)
            acl.require_no_extended_acl(replacement)
            with self.assertRaises(acl.ACLPresent): acl.require_no_extended_acl(original)
            self.assertEqual(os.fstat(original).st_ino,original_inode)
        finally:
            if replacement is not None: os.close(replacement)
            os.close(original)

    def test_new_temporary_file_is_checked_before_any_ciphertext_write(self):
        box=store.Inbox(self.root,crypt=crypt)
        observed=[]
        def inspect(fd):
            info=os.fstat(fd)
            temporary=[path for path in (self.root/'siri-inbox.noindex').glob('.inbox-*') if path.stat().st_ino==info.st_ino]
            if temporary:
                observed.append(info.st_size)
                raise acl.ACLUnavailable('synthetic inspection error')
            acl.require_no_extended_acl(fd)
        with patch.object(store,'require_no_extended_acl',side_effect=inspect):
            with self.assertRaises(acl.ACLUnavailable): box.put(str(uuid.uuid4()),'synthetic',now=1)
        self.assertEqual(observed,[0])
        self.assertFalse((self.root/'siri-inbox.noindex/initialized').exists())
        self.assertFalse((self.root/'siri-inbox.noindex/inbox.enc').exists())
        self.assertEqual(list((self.root/'siri-inbox.noindex').glob('.inbox-*')),[])


class ACLPackagingTests(unittest.TestCase):
    def test_relocated_payload_has_inspector_and_runs_with_embedded_python(self):
        import hashlib
        import json
        import shutil
        import subprocess
        from package_helpers import package
        with tempfile.TemporaryDirectory(prefix='reprise-144-payload-',dir='/private/tmp') as folder:
            root=Path(folder); source=Path(__file__).parents[1]
            payload=package(source,root/'first.app')
            shutil.move(root/'first.app',root/'relocated.app')
            payload=root/'relocated.app/Contents/Resources/helpers'
            manifest=json.loads((payload/'payload-manifest.json').read_text())
            for name in ('siri_inbox_acl.py','siri_inbox_model.py','siri_inbox_store.py','siri_inbox_cli.py'):
                self.assertEqual(manifest['files'][name],hashlib.sha256((payload/name).read_bytes()).hexdigest())
            script='''import os,sys,tempfile
sys.path.insert(0,sys.argv[1])
import siri_inbox_acl, siri_inbox_cli, siri_inbox_store
with tempfile.TemporaryFile(dir=sys.argv[2]) as fixture:
 siri_inbox_acl.require_no_extended_acl(fixture.fileno())
print("relocated-acl-ok")
'''
            if sys.platform=='darwin':
                result=subprocess.run([sys.executable,'-I','-B','-c',script,str(payload),str(root)],cwd=root,
                                      capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout.strip(),'relocated-acl-ok')
