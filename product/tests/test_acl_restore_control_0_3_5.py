"""Real owned control-bit matrix + restore completion/error contracts."""
import ctypes
from ctypes import wintypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def guard():
    spec=importlib.util.spec_from_file_location('acl_restore_control_guard',ROOT/'scripts/local_integrity_namespace_guard.py')
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    return module


def _snapshot(guard,path):
    sddl,protected,owner=guard._security_descriptor(path)
    st=path.stat(follow_symlinks=False)
    result=dict(sddl=sddl,protected=protected,owner=owner,identity=[st.st_dev,st.st_ino])
    if path.is_file():result['default_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _control(sddl,flags):
    return re.sub(r'D:[^(]*','D:'+flags,sddl,count=1)


def test_real_win32_saved_control_matrix_and_mixed_parent_child(guard,tmp_path):
    if os.name!='nt':pytest.skip('actual Windows API required')
    root=Path(os.environ.get('ACL_RESTORE_TEST_ROOT',str(tmp_path/'owned-controls')))
    assert root.is_absolute() and not root.exists()
    root.mkdir(parents=True)
    paths=[root]
    for name in ('ai','legacy','protected','protected_ai'):
        directory=root/name;directory.mkdir();file=directory/'child.bin';file.write_bytes(b'owned-control-byte-baseline\n')
        paths.extend([directory,file])
    assert len(paths)==9
    for path in paths:guard._reject_reparse(path,directory=path.is_dir())
    original={str(p):_snapshot(guard,p) for p in paths}
    (tmp_path/'REAL_ORIGINAL.json').write_text(json.dumps(original,indent=2),encoding='utf-8')
    rows=[]
    try:
        for name,flags in [('ai','AI'),('legacy',''),('protected','P'),('protected_ai','PAI')]:
            selected=[root/name,root/name/'child.bin']
            for path in selected:
                current=_snapshot(guard,path)
                sddl=_control(current['sddl'],flags)
                if name=='protected_ai':
                    # Explicit owner ACEs as well as inherited-ACE cases; no
                    # other object is changed and original ACLs remain saved.
                    sddl=sddl[:sddl.index('D:')]+'D:PAI(A;'+('OICI' if path.is_dir() else '')+';FA;;;'+current['owner']+')'
                guard._set_security_descriptor(path,sddl,protected='P' in flags)
            before={str(p):_snapshot(guard,p) for p in selected}
            records=[guard._SecurityRecord('directory' if p.is_dir() else 'file',p.relative_to(root).as_posix(),before[str(p)]['sddl'],before[str(p)]['protected']) for p in selected]
            for path in selected:
                guard._set_security_descriptor(path,_control(before[str(path)]['sddl'],'P'),protected=True)
            guard._restore_records(root,records)
            after={str(p):_snapshot(guard,p) for p in selected}
            rows.append(dict(case=name,before=before,after=after,exact=before==after))
            assert after==before
        # AI parent + explicitly legacy, unprotected child needs parent first.
        selected=[root/'ai',root/'ai/child.bin']
        child=_snapshot(guard,selected[1])
        guard._set_security_descriptor(selected[1],_control(child['sddl'],''),protected=False)
        before={str(p):_snapshot(guard,p) for p in selected}
        records=[guard._SecurityRecord('directory' if p.is_dir() else 'file',p.relative_to(root).as_posix(),before[str(p)]['sddl'],before[str(p)]['protected']) for p in selected]
        for path in selected:guard._set_security_descriptor(path,_control(before[str(path)]['sddl'],'P'),protected=True)
        # Also reject assumptions about journal list order: hierarchy owns order.
        guard._restore_records(root,list(reversed(records)))
        after={str(p):_snapshot(guard,p) for p in selected}
        rows.append(dict(case='ai_parent_legacy_child',before=before,after=after,exact=before==after))
        assert after==before
    finally:
        records=[guard._SecurityRecord('directory' if p.is_dir() else 'file','.' if p==root else p.relative_to(root).as_posix(),original[str(p)]['sddl'],original[str(p)]['protected']) for p in paths]
        errors=[]
        try:guard._restore_records(root,records)
        except Exception as exc:errors.append(str(exc))
        final={str(p):_snapshot(guard,p) for p in paths}
        result=dict(rows=rows,fully_restored=final==original,restore_errors=errors,final=final,token_admin=bool(guard._is_user_an_admin()))
        (tmp_path/'REAL_RESULT.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print('REAL_ACL_RESULT='+json.dumps(dict(cases=len(rows),fully_restored=final==original,restore_errors=errors,token_admin=result['token_admin'])))
        assert final==original and not errors


@pytest.mark.parametrize('protected',[False,True])
def test_setter_rejects_success_return_without_exact_readback(guard,monkeypatch,tmp_path,protected):
    if os.name!='nt':pytest.skip('actual descriptor parser required')
    sddl='O:SYG:SYD:'+('P' if protected else '')+'(A;;FA;;;SY)'
    calls=[]
    monkeypatch.setattr(guard,'_set_file_security',lambda *args:calls.append(args) or True)
    monkeypatch.setattr(guard,'_security_descriptor',lambda path:('O:SYG:SYD:AI(A;;FA;;;SY)',False,'S-1-5-18'))
    with pytest.raises(guard.NamespaceGuardError,match='namespace_security_apply_unverified'):
        guard._set_security_descriptor(tmp_path/'not-created',sddl,protected=protected)
    assert len(calls)==1


@pytest.mark.parametrize('late_kind',['control','owner','protected','unreadable'])
def test_whole_batch_readback_catches_late_drift_and_keeps_journal(guard,monkeypatch,tmp_path,late_kind):
    root=tmp_path/'fake-root'
    records=[guard._SecurityRecord('directory','.', 'O:SYG:SYD:AI(A;;FA;;;SY)',False),
             guard._SecurityRecord('file','child','O:SYG:SYD:(A;;FA;;;SY)',False)]
    current={};order=[]
    monkeypatch.setattr(guard,'_reject_reparse',lambda *a,**k:None)
    def setter(path,sddl,*,protected):
        order.append(path.name);current[path]=(sddl,protected,'owner')
        if path.name=='child':
            if late_kind=='control':current[root]=(records[0].sddl.replace('D:AI','D:'),False,'owner')
            elif late_kind=='owner':current[root]=(records[0].sddl.replace('O:SY','O:BA'),False,'owner')
            elif late_kind=='protected':current[root]=(records[0].sddl,True,'owner')
    def getter(path):
        if late_kind=='unreadable' and path==root:raise PermissionError('synthetic readback failure')
        return current[path]
    monkeypatch.setattr(guard,'_set_security_descriptor',setter)
    monkeypatch.setattr(guard,'_security_descriptor',getter)
    journal=tmp_path/'journal.json';journal.write_text('saved-original',encoding='utf-8')
    lease=guard.NamespaceLease(root,records,journal,'d','f','owner',None)
    with pytest.raises(guard.NamespaceRestoreError) as caught:lease.close()
    assert order==[root.name,'child'] and caught.value.failed_count==1 and caught.value.attempted_count==2
    assert journal.read_text('utf-8')=='saved-original' and not lease._closed


def test_setter_error_and_readback_failure_count_once_attempt_all(guard,monkeypatch,tmp_path):
    records=[guard._SecurityRecord('directory','.', 'original',False),guard._SecurityRecord('file','child','original',False)]
    attempted=[]
    monkeypatch.setattr(guard,'_reject_reparse',lambda *a,**k:None)
    def setter(path,*a,**k):
        attempted.append(path)
        if path.name=='child':raise PermissionError('synthetic setter failure')
    def getter(path):
        if path.name=='child':raise PermissionError('synthetic readback failure')
        return ('original',False,'owner')
    monkeypatch.setattr(guard,'_set_security_descriptor',setter);monkeypatch.setattr(guard,'_security_descriptor',getter)
    with pytest.raises(guard.NamespaceRestoreError) as caught:guard._restore_records(tmp_path,records)
    assert caught.value.failed_count==1 and caught.value.attempted_count==2
    assert attempted==[tmp_path/'child',tmp_path]


def test_begin_rollback_restores_saved_inventory_and_preserves_primary(guard,monkeypatch,tmp_path):
    child=tmp_path/'child';primary=OSError('synthetic first setter post-change failure');seen=[]
    monkeypatch.setattr(guard,'_require_windows',lambda:None)
    monkeypatch.setattr(guard,'_reject_reparse',lambda *a,**k:None)
    monkeypatch.setattr(guard,'_prepare_recovery_root',lambda *a,**k:None)
    monkeypatch.setattr(guard,'_journal_path',lambda *a:tmp_path/'journal.json')
    monkeypatch.setattr(guard,'_protected_paths',lambda *a:([tmp_path],[child]))
    monkeypatch.setattr(guard,'_security_descriptor',lambda *a:('original',False,'owner'))
    monkeypatch.setattr(guard,'_write_journal',lambda path,*a:path.write_text('original',encoding='utf-8'))
    monkeypatch.setattr(guard,'_set_security_descriptor',lambda *a,**k:(_ for _ in ()).throw(primary))
    monkeypatch.setattr(guard,'_restore_records',lambda root,records:seen.extend(records))
    with pytest.raises(OSError) as caught:
        guard._begin_namespace_lease(tmp_path,locked_directory_sddl='d',locked_file_sddl='f',expected_owner='o',recovery_root=tmp_path,
                                     require_elevated=False,secure_recovery=False)
    assert caught.value is primary and caught.value.namespace_restore_state=='restored'
    assert [r.relative for r in seen]==['.','child']
    assert (tmp_path/'journal.json').read_text('utf-8')=='original'


def test_no_setter_attempt_does_not_start_rollback(guard,monkeypatch,tmp_path):
    monkeypatch.setattr(guard,'_require_windows',lambda:None);monkeypatch.setattr(guard,'_reject_reparse',lambda *a,**k:None)
    monkeypatch.setattr(guard,'_prepare_recovery_root',lambda *a,**k:None);monkeypatch.setattr(guard,'_journal_path',lambda *a:tmp_path/'journal.json')
    monkeypatch.setattr(guard,'_protected_paths',lambda *a:([tmp_path],[]));monkeypatch.setattr(guard,'_security_descriptor',lambda *a:('original',False,'owner'))
    monkeypatch.setattr(guard,'_write_journal',lambda *a:(_ for _ in ()).throw(OSError('synthetic journal error')))
    monkeypatch.setattr(guard,'_restore_records',lambda *a:pytest.fail('rollback before setter attempt'))
    with pytest.raises(OSError,match='synthetic journal error'):
        guard._begin_namespace_lease(tmp_path,locked_directory_sddl='d',locked_file_sddl='f',expected_owner='o',recovery_root=tmp_path,
                                     require_elevated=False,secure_recovery=False)


def test_real_begin_close_and_full_inventory_rollback_stale_retry(guard,monkeypatch,tmp_path):
    if os.name!='nt':pytest.skip('actual Windows API required')
    root=Path(os.environ.get('ACL_RESTORE_LEASE_TEST_ROOT',str(tmp_path/'owned-lease')))
    assert root.is_absolute() and not root.exists()
    root.mkdir(parents=True);child=root/'child.bin';child.write_bytes(b'owned-lease-byte-baseline\n')
    natural={str(p):_snapshot(guard,p) for p in (root,child)}
    (tmp_path/'LEASE_NATURAL.json').write_text(json.dumps(natural,indent=2),encoding='utf-8')
    recovery=Path(os.environ.get('ACL_RESTORE_RECOVERY_TEST_ROOT',str(tmp_path/'recovery')))
    assert recovery.is_absolute() and not recovery.exists()
    recovery.mkdir(parents=True)
    journal=guard._journal_path(root,recovery)
    assert not journal.exists()
    actual_setter=guard._set_security_descriptor
    observed=[]
    try:
        # The saved root is AI, but its not-yet-applied child is legacy/non-AI.
        actual_setter(child,_control(natural[str(child)]['sddl'],''),protected=False)
        before={str(p):_snapshot(guard,p) for p in (root,child)}
        owner=before[str(root)]['owner']
        prefix=before[str(root)]['sddl'].split('D:',1)[0]
        kwargs=dict(locked_directory_sddl=prefix+'D:P(A;OICI;FRFXRCWDWO;;;'+owner+')',
                    locked_file_sddl=prefix+'D:P(A;;FRFXRCWDWO;;;'+owner+')',
                    expected_owner=owner,recovery_root=recovery,require_elevated=False,secure_recovery=False)
        lease=guard._begin_namespace_lease(root,**kwargs)
        assert journal.exists()
        lease.close();lease.close()
        after={str(p):_snapshot(guard,p) for p in (root,child)}
        assert after==before and not journal.exists()
        observed.append({'case':'actual_begin_attest_close_idempotent','exact':True})
        primary=OSError('synthetic error after actual first setter')
        attempts=[]
        def fail_first(path,sddl,*,protected):
            actual_setter(path,sddl,protected=protected);attempts.append(str(path))
            if len(attempts)==1:raise primary
        with monkeypatch.context() as patch:
            patch.setattr(guard,'_set_security_descriptor',fail_first)
            with pytest.raises(OSError) as caught:guard._begin_namespace_lease(root,**kwargs)
        assert caught.value is primary and caught.value.namespace_restore_state=='restored'
        assert str(child) in attempts and journal.exists()
        assert {str(p):_snapshot(guard,p) for p in (root,child)}==before
        observed.append({'case':'actual_rollback_includes_unapplied_legacy_child','exact':True,'attempts':attempts})
        # Reuse only this original same-identity journal, through the real entry.
        retry=guard._begin_namespace_lease(root,**kwargs)
        retry.close()
        assert not journal.exists() and {str(p):_snapshot(guard,p) for p in (root,child)}==before
        observed.append({'case':'actual_same_identity_stale_journal_retry','exact':True})
    finally:
        monkeypatch.setattr(guard,'_set_security_descriptor',actual_setter)
        records=[guard._SecurityRecord('directory','.',natural[str(root)]['sddl'],natural[str(root)]['protected']),
                 guard._SecurityRecord('file','child.bin',natural[str(child)]['sddl'],natural[str(child)]['protected'])]
        errors=[]
        try:guard._restore_records(root,records)
        except Exception as exc:errors.append(str(exc))
        final={str(p):_snapshot(guard,p) for p in (root,child)}
        result=dict(cases=observed,fully_restored=final==natural,restore_errors=errors,journal=str(journal),journal_exists=journal.exists(),
                    final=final,token_admin=bool(guard._is_user_an_admin()),production_equivalent_token=False)
        (tmp_path/'LEASE_RESULT.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print('REAL_LEASE_RESULT='+json.dumps({k:v for k,v in result.items() if k not in ('final','cases','journal')}))
        assert final==natural and not errors
