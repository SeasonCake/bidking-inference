"""Current synthetic server response -> real client/enforce -> headless handler.

No Tk root or window is created. Endpoint/HTTP and widget/worker fixtures are
explicit seams; authority, candidate commit and response parsing stay real.
"""
from __future__ import annotations
import base64
import ast
import copy
import importlib.util
import json
from pathlib import Path
import sys
import socket
import threading
from types import SimpleNamespace
import pytest
from bidking_lab.secure import activation,client_activate as ca,enforce
from test_activation_candidate_atomic_commit_0_3_1 import isolated_activation_runtime,_DEVICE

ROOT=Path(__file__).resolve().parents[1]
for module,rel in ((ca,'client_activate.py'),(enforce,'enforce.py'),(activation,'activation.py')):
    assert Path(module.__file__).resolve()==ROOT/'src/bidking_lab/secure'/rel
SERVER_ROOT=Path('C:/example/bidking-workspace/scratch/bidking_hotfix2_roaming_eight_20260906_01')

def _frozen_module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module
    spec.loader.exec_module(module)
    return module

CORE=_frozen_module('roam_c1_frozen_core',SERVER_ROOT/'FINAL/scripts/activation_core.py')
SERVER=_frozen_module('roam_c1_frozen_server',SERVER_ROOT/'FINAL/scripts/activation_server.py')
SERVER.activation_core=CORE
SECRET=b'ROAM-C1-SYNTHETIC-SECRET'
NOW=2_000_000
CAP_TEXT='换绑次数已用完，请在当前绑定的电脑使用'

@pytest.fixture
def wire(tmp_path,monkeypatch,isolated_activation_runtime):
    monkeypatch.setattr(ca,'_ACTIVATION_CLEAR_EPOCH',0)
    monkeypatch.setattr(ca,'_ACTIVATION_CLEAR_STATE',None)
    monkeypatch.setattr(ca,'_load_activation_http_e2ee_config',lambda:None)
    def no_network(*args,**kwargs):raise AssertionError('network forbidden in synthetic client test')
    monkeypatch.setattr(socket.socket,'connect',no_network)
    root=tmp_path/'synthetic-server'
    root.mkdir()
    SERVER.initialize_pet_usage_database(root/'pet.sqlite3',secret=SECRET,
        legacy_paths=(root/'pet.json',root/'pet.anchor.jsonl'))
    service=SERVER.ActivationService(secret=SECRET,master_key_b64=base64.b64encode(b'K'*32).decode(),
        bindings_path=root/'bindings.json',revoked_path=root/'revoked.txt',telemetry_path=root/'telemetry.jsonl',
        trials_path=root/'trials.json',paid_starts_path=root/'paid_starts.json',pet_usage_db_path=root/'pet.sqlite3',
        pet_usage_path=root/'pet.json',device_block_key=b'L'*32)
    token=CORE.mint_token('roam-c1-card',SECRET,iat=NOW-50,exp=NOW-50+90*86400,roaming=True)
    assert ca.save_token(token)
    calls=[]
    def post(_url,device,signed,_timeout):
        response=service.handle_activate({'device':device,'token':signed})
        calls.append(response)
        return response
    monkeypatch.setattr(ca,'_post_activate',post)
    def seed(used,device=_DEVICE):
        SERVER._atomic_write_json(service.bindings_path,{'roam-c1-card':{
            'schema_version':1,'device':device,'roaming_rebinds_used':used}})
        SERVER._atomic_write_json(service.paid_starts_path,{'roam-c1-card':NOW-10})
    return SimpleNamespace(service=service,token=token,seed=seed,calls=calls,post=post)

def test_real_server_cap_has_specific_client_and_ui_copy(wire):
    wire.seed(8,device='OTHER-SYNTHETIC-COMPUTER')
    result=enforce.refresh_activation()
    assert not result.activated and result.reason=='device_mismatch'
    assert wire.calls[-1][0]==403 and wire.calls[-1][1]['detail']=='roaming_rebind_limit'
    assert enforce.activation_state(result)['text']==CAP_TEXT
    assert result.status_line()=='激活: '+CAP_TEXT

def test_real_server_success_used1_reaches_display_view(wire):
    wire.seed(1)
    result=enforce.refresh_activation()
    assert result.activated and enforce.should_run_full(result)
    assert enforce.activation_state(result).get('roaming_rebinds')=={
        'schema_version':1,'policy_epoch':1,'limit':8,'used':1,'remaining':7}

def _meta(used=1):
    return dict(schema_version=1,policy_epoch=1,limit=8,used=used,remaining=8-used)

@pytest.mark.parametrize('used',[0,1,7,8])
def test_current_paid_count_never_changes_authority_or_mini_time(wire,used):
    wire.seed(used)
    result=enforce.refresh_activation()
    state=enforce.activation_state(result)
    assert result.activated and state['run_full'] and not state['needs_token']
    assert state['roaming_rebinds']==_meta(used) and state['roaming_rebind_limit_reached'] is False
    assert state['text'].startswith('会员 · 剩余 ') and '/8' not in state['text']
    assert result.feature_usage['pet_companion']['kind']=='full'

BAD_META=[None,{}, {'schema_version':1,'policy_epoch':1,'limit':8,'used':1},
    dict(_meta(),other=0),dict(_meta(),schema_version=2),dict(_meta(),policy_epoch=2),
    dict(_meta(),limit=9),dict(_meta(),used=True),dict(_meta(),used=1.0),
    dict(_meta(),remaining='7'),dict(_meta(),used=-1),dict(_meta(),used=9),dict(_meta(),remaining=6)]

@pytest.mark.parametrize('meta',BAD_META)
def test_bad_optional_wire_meta_does_not_invalidate_grant_or_pet(wire,monkeypatch,meta):
    wire.seed(1)
    def post(*args):
        code,body=wire.post(*args)
        payload=json.loads(body['lease'].rsplit('.',1)[0])
        payload['roaming_rebinds']=meta
        body=dict(body,lease=CORE.sign_lease(payload,SECRET))
        return code,body
    monkeypatch.setattr(ca,'_post_activate',post)
    result=enforce.refresh_activation()
    assert result.activated and enforce.should_run_full(result)
    state=enforce.activation_state(result)
    assert state['roaming_rebinds'] is None and state['feature_usage']['pet_companion']['kind']=='full'

@pytest.mark.parametrize('change',[{'detail':'ordinary'}, {'detail':True}, {'reason':'revoked'},
    {'mode':'trial'}, {'code':401}, {'code':503}, {'detail':None}])
def test_false_cap_detail_or_status_does_not_set_display_flag(wire,monkeypatch,change):
    wire.seed(8,device='OTHER')
    def post(*args):
        code,body=wire.post(*args)
        body=dict(body,**{key:value for key,value in change.items() if key!='code'})
        return change.get('code',code),body
    monkeypatch.setattr(ca,'_post_activate',post)
    result=enforce.refresh_activation()
    state=enforce.activation_state(result)
    assert not result.activated and not state['run_full']
    assert state['roaming_rebind_limit_reached'] is False and state['roaming_rebinds'] is None
    assert CAP_TEXT not in state['text']

def test_cap_with_bad_numbers_keeps_only_the_trusted_reason(wire,monkeypatch):
    wire.seed(8,device='OTHER')
    def post(*args):
        code,body=wire.post(*args)
        return code,dict(body,roaming_rebinds={'used':8})
    monkeypatch.setattr(ca,'_post_activate',post)
    state=enforce.activation_state(enforce.refresh_activation())
    assert state['text']==CAP_TEXT and state['roaming_rebinds'] is None

def test_display_projection_is_no_protocol_and_keeps_exact_thirteen_authority_fields():
    class Bomb:
        def __str__(self):raise AssertionError('display called str')
        def __int__(self):raise AssertionError('display called int')
        def __eq__(self,other):raise AssertionError('display called eq')
    class Key(str):
        __hash__=str.__hash__
        def __eq__(self,other):raise AssertionError('key protocol called')
    result=ca.ActivationResult(True,'paid',lease_exp=NOW+500)
    authority=enforce._project_activation_result_fields(result,result_type=ca.ActivationResult)
    assert len(authority)==13
    for value in (Bomb(),dict(_meta(),used=Bomb()),{Key('schema_version'):1,'policy_epoch':1,'limit':8,'used':1,'remaining':7}):
        result.roaming_rebinds=value
        result.roaming_rebind_limit_reached=Bomb()
        assert ca._activation_roaming_display(result)==(None,False)
        assert enforce._project_activation_result_fields(result,result_type=ca.ActivationResult)==authority
    result.__dict__['unknown_optional']=None
    assert enforce._project_activation_result_fields(result,result_type=ca.ActivationResult) is None
    result.__dict__.pop('unknown_optional')
    result.__dict__.pop('lease_exp')
    assert enforce._project_activation_result_fields(result,result_type=ca.ActivationResult) is None
    assert set(enforce._recover_activation_result_fields(result,result_type=ca.ActivationResult))<=set(enforce._ACTIVATION_RESULT_FIELD_NAMES)

@pytest.mark.parametrize('source,mode,activated',[('cache','paid',True),('client','paid',True),('server','trial',True),('server','paid',False)])
def test_nononline_nonpaid_or_failure_never_inherits_success_count(source,mode,activated):
    result=ca.ActivationResult(activated,mode,source=source,roaming_rebinds=_meta())
    assert ca._activation_roaming_display(result)==(None,False)

@pytest.fixture
def headless(wire):
    """Exact dialog/update/deny methods, fake widgets and queued workers only."""
    from product_profile import NETWORK_DIAGNOSTIC_DIALOG_LINES,STANDARD_SUBSCRIPTION_PRICE_ROWS
    workers,callbacks,widgets=[],[],[]
    class Widget:
        def __init__(self,kind,parent=None,**kwargs):
            self.kind,self.parent,self.options=kind,parent,dict(kwargs)
            self.children,self.bindings,self.protocols,self.configures=[],{},{},[]
            self.value=''
            self.destroyed=False
            if parent is not None and hasattr(parent,'children'):parent.children.append(self)
            widgets.append(self)
        def configure(self,**kwargs):self.options.update(kwargs); self.configures.append(kwargs)
        def pack(self,**kwargs):self.options['pack']=kwargs
        def grid(self,**kwargs):self.options['grid']=kwargs
        def grid_columnconfigure(self,*args,**kwargs):pass
        def focus_set(self):pass
        def insert(self,index,value):self.value=value
        def get(self):return self.value
        def delete(self,*args):self.value=''
        def bind(self,name,callback):self.bindings[name]=callback
        def protocol(self,name,callback):self.protocols[name]=callback
        def destroy(self):self.destroyed=True
        def title(self,value):self.options['title']=value
        def attributes(self,*args):pass
        def resizable(self,*args):self.options['resizable']=args
        def deiconify(self):
            if self.destroyed:raise RuntimeError('destroyed fake widget')
        def lift(self):pass
        def update_idletasks(self):pass
        def winfo_rootx(self):return 0
        def winfo_rooty(self):return 0
        def winfo_reqwidth(self):return 469
        def winfo_reqheight(self):return 428
        def winfo_x(self):return 0
        def winfo_y(self):return 0
        def geometry(self,value):self.options['geometry']=value
        def after(self,delay,callback):callbacks.append((delay,callback))
    class Worker:
        def __init__(self,target,**kwargs):self.target=target
        def start(self):workers.append(self.target)
    source=ROOT/'hero_ref/tools/ahmad_tk_overlay.py'
    tree=ast.parse(source.read_text(encoding='utf-8'))
    names={'_open_token_dialog','_update_activation_ui','_activation_product_denied'}
    methods=[node for node in ast.walk(tree) if isinstance(node,ast.FunctionDef) and node.name in names]
    assert len(methods)==3
    namespace=dict(tk=SimpleNamespace(**{kind:(lambda parent=None,_kind=kind,**kw:Widget(_kind,parent,**kw)) for kind in ('Toplevel','Label','Frame','Entry','Button')},TclError=RuntimeError),
        threading=SimpleNamespace(Thread=Worker),NETWORK_DIAGNOSTIC_DIALOG_LINES=NETWORK_DIAGNOSTIC_DIALOG_LINES,
        STANDARD_SUBSCRIPTION_PRICE_ROWS=STANDARD_SUBSCRIPTION_PRICE_ROWS,
        _monitor_work_area_for_point=lambda *args:(0,0,2000,1200),_clamp_rect_into_workarea=lambda x,y,*args:(x,y),
        _window_outer_rect=lambda dlg:(0,0,469,428))
    namespace.update({name:name for name in ('BG','TEXT','PANEL','MUTED','GOOD','BAD','WARN','WARM','FONT_UI')})
    module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),
        ast.ClassDef(name='AhmadTkOverlay',bases=[],keywords=[],body=methods,decorator_list=[])],type_ignores=[])
    exec(compile(ast.fix_missing_locations(module),str(source),'exec'),namespace)
    overlay=namespace['AhmadTkOverlay']()
    overlay.root=Widget('Root')
    overlay.activation_label=Widget('Label')
    overlay._act_ready=True
    overlay._expired_latched=False
    overlay._set_activation_entry_available=lambda value:None
    overlay._hide_activation_label=overlay._show_activation_label=lambda:None
    # This three-method adapter verifies ROAM counts/feedback only. QUOTE tests
    # independently exercise the real SHOP branch, hover and click handlers.
    overlay._add_shop_entry=lambda dialog:None
    def open_dialog(profile=False):
        overlay.network_diagnostic_cohort=profile
        overlay._act_state=enforce.activation_state()
        overlay._open_token_dialog()
        dlg=overlay._token_dialog
        status=next(w for w in dlg.children if w.kind=='Label' and w.options.get('font')==('FONT_UI',8) and w.options.get('anchor')=='w')
        entry=next(w for w in dlg.children if w.kind=='Entry')
        buttons={w.options.get('text'):w for w in widgets if w.kind=='Button' and w.parent.parent is dlg}
        return SimpleNamespace(dlg=dlg,status=status,entry=entry,buttons=buttons,close=dlg.protocols['WM_DELETE_WINDOW'])
    return SimpleNamespace(overlay=overlay,open=open_dialog,widgets=widgets,workers=workers,callbacks=callbacks)

@pytest.mark.parametrize('profile',[False,True])
@pytest.mark.parametrize('used',[0,8])
def test_idle_dialog_and_success_consume_real_current_count(wire,headless,profile,used):
    wire.seed(used)
    result=enforce.refresh_activation()
    view=headless.open(profile)
    assert view.status.options['text']==f'上次验证：换绑剩余 {8-used}/8 次'
    count=len(view.status.configures)
    headless.overlay._update_activation_ui()
    headless.overlay._update_activation_ui()
    assert len(view.status.configures)==count
    labels=[w.options.get('text','') for w in headless.widgets if w.kind=='Label']
    rule='漫游卡可换绑8次（首次绑定不计）｜历史永久权益不变'
    assert (rule in labels) is (not profile)
    view.entry.value=wire.token
    view.buttons['激活'].options['command']()
    assert view.status.options['text']=='激活中…'
    headless.overlay._update_activation_ui()
    assert view.status.options['text']=='激活中…'
    headless.workers.pop(0)()
    delay,after=headless.callbacks.pop(0)
    assert delay==0
    after()
    assert view.status.options['text']==f'激活成功 · 换绑剩余 {8-used}/8 次'
    assert headless.callbacks[0][0]==900
    headless.overlay._update_activation_ui()
    assert view.status.options['text'].startswith('激活成功')

@pytest.mark.parametrize('profile',[False,True])
def test_cap_dialog_and_late_old_view_callbacks_keep_feedback_owner(wire,headless,profile):
    wire.seed(8,device='OTHER')
    result=enforce.refresh_activation()
    old=headless.open(profile)
    assert old.status.options['text']==CAP_TEXT
    stale_refresh=headless.overlay._activation_roaming_view_refresh
    old.entry.value=wire.token
    old.buttons['激活'].options['command']()
    headless.workers.pop(0)()
    old.close()
    new=headless.open(profile)
    current_refresh=headless.overlay._activation_roaming_view_refresh
    old.close()
    assert headless.overlay._token_dialog is new.dlg
    assert headless.overlay._activation_roaming_view_refresh is current_refresh
    headless.callbacks.pop(0)[1]()
    stale_refresh({'run_full':True,'mode':'paid','roaming_rebinds':_meta(0)})
    assert new.status.options['text']==CAP_TEXT
    new.entry.value='not-a-single-token'
    new.buttons['激活'].options['command']()
    assert new.status.options['text']=='粘贴内容不是单一token，请重新复制纯token'
    current_refresh(enforce.activation_state(result))
    assert new.status.options['text']=='粘贴内容不是单一token，请重新复制纯token'

@pytest.mark.parametrize('invalid',[False,True])
def test_real_30d_pet_survives_optional_count_and_invalid_pet_stays_denied(wire,monkeypatch,invalid):
    token=CORE.mint_token('roam-c1-card',SECRET,iat=NOW-50,exp=NOW-50+30*86400,roaming=True)
    assert ca.save_token(token)
    wire.seed(1)
    def post(*args):
        code,body=wire.post(*args)
        if invalid:
            lease=json.loads(body['lease'].rsplit('.',1)[0])
            lease['feature_usage']['pet_companion']['remaining_seconds']=True
            body=dict(body,lease=CORE.sign_lease(lease,SECRET))
        return code,body
    monkeypatch.setattr(ca,'_post_activate',post)
    result=enforce.refresh_activation()
    state=enforce.activation_state(result)
    assert result.activated and state['run_full'] and state['roaming_rebinds']==_meta(1)
    pet=state['feature_usage']['pet_companion']
    if invalid:
        assert pet['kind']=='none' and pet['status']=='state_error'
    else:
        assert pet['kind']=='quota' and pet['remaining_seconds']==43200

def test_local_commit_failure_drops_success_display_and_preserves_fail_closed(wire,monkeypatch):
    wire.seed(1)
    def fail(*args,**kwargs):raise OSError('synthetic cache commit failure')
    monkeypatch.setattr(ca,'_save_cache',fail)
    result=enforce.refresh_activation()
    state=enforce.activation_state(result)
    assert not result.activated and result.reason=='local_commit_failed'
    assert state['roaming_rebinds'] is None and state['roaming_rebind_limit_reached'] is False
    assert '本机激活未完成' in state['text'] and not state['needs_token']
    assert not activation.is_activated() and not enforce._is_graceable_heartbeat_failure(result)

def test_grace_only_keeps_last_verified_view_and_cache_never_supplies_current_count(wire,monkeypatch,headless):
    wire.seed(7)
    assert enforce.refresh_activation().activated
    view=headless.open()
    failure=ca._ActivationPostFailure('connect_timeout',retry_fallback=False,stage='tcp')
    monkeypatch.setattr(ca,'_post_activate',lambda *args:failure)
    result=enforce.refresh_activation()
    assert not result.activated and result.roaming_rebinds is None
    assert enforce.activation_state(result)['roaming_rebinds'] is None
    state=enforce.activation_state()
    assert state['transport_grace'] is True and state['roaming_rebinds']==_meta(7)
    headless.overlay._update_activation_ui()
    assert view.status.options['text']=='上次验证：换绑剩余 1/8 次'
    # Simulate no in-memory online grant, retaining only real disk cache.
    monkeypatch.setattr(enforce,'_LAST',None)
    monkeypatch.setattr(enforce,'_LAST_ONLINE_GRANT',None)
    cold=ca.activate(force_server=True)
    assert not cold.activated and cold.roaming_rebinds is None

@pytest.mark.parametrize('aba',[False,True])
def test_late_real_cap_cannot_replace_new_grant_display(wire,monkeypatch,aba):
    wire.seed(8,device='OTHER')
    entered,release=threading.Event(),threading.Event()
    first=True
    def post(*args):
        nonlocal first
        response=wire.post(*args)
        if first:
            first=False
            entered.set()
            assert release.wait(2)
        return response
    monkeypatch.setattr(ca,'_post_activate',post)
    returned=[]
    worker=threading.Thread(target=lambda:returned.append(enforce.refresh_activation()))
    worker.start()
    try:
        assert entered.wait(2)
        other=CORE.mint_token('second-c1-card',SECRET,iat=NOW-50,exp=NOW-50+90*86400,roaming=True)
        assert ca.save_token(other)
        latest=enforce.refresh_activation()
        if aba:
            wire.seed(3)
            assert ca.save_token(wire.token)
            latest=enforce.refresh_activation()
        assert latest.activated
        provider=activation._provider
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive()
    assert returned[0].reason=='local_identity_changed' and returned[0].roaming_rebinds is None
    assert enforce.current() is latest and activation._provider is provider
    assert enforce.activation_state()['roaming_rebinds']==_meta(3 if aba else 0)

@pytest.mark.parametrize('profile',[False,True])
def test_clear_feedback_and_reopen_cannot_revive_old_count(wire,headless,profile):
    wire.seed(1)
    enforce.refresh_activation()
    view=headless.open(profile)
    old_refresh=headless.overlay._activation_roaming_view_refresh
    view.buttons['换绑/清除'].options['command']()
    headless.overlay._update_activation_ui()
    assert view.status.options['text']=='清除中…'
    view.close()
    reopened=headless.open(profile)
    headless.workers.pop(0)()
    headless.callbacks.pop(0)[1]()
    assert '已清除本机 token' in reopened.status.options['text']
    headless.overlay._update_activation_ui()
    old_refresh({'run_full':True,'mode':'paid','roaming_rebinds':_meta(0)})
    assert '已清除本机 token' in reopened.status.options['text']
    assert '/8' not in reopened.status.options['text']

def test_idle_existing_tick_updates_count_and_removes_unknown_without_extra_jobs(wire,headless,monkeypatch):
    wire.seed(1)
    enforce.refresh_activation()
    view=headless.open()
    wire.seed(7)
    enforce.refresh_activation()
    headless.overlay._update_activation_ui()
    assert view.status.options['text']=='上次验证：换绑剩余 1/8 次'
    def post(*args):
        code,body=wire.post(*args)
        payload=json.loads(body['lease'].rsplit('.',1)[0])
        payload.pop('roaming_rebinds')
        return code,dict(body,lease=CORE.sign_lease(payload,SECRET))
    monkeypatch.setattr(ca,'_post_activate',post)
    enforce.refresh_activation()
    headless.overlay._update_activation_ui()
    assert view.status.options['text']==''
    assert not headless.workers and not headless.callbacks

@pytest.mark.parametrize('kind',['bound','trial'])
def test_real_bound_or_trial_response_does_not_advertise_roaming(wire,monkeypatch,kind):
    if kind=='bound':
        token=CORE.mint_token('bound-c1-card',SECRET,iat=NOW-50,exp=NOW-50+90*86400,roaming=False)
        assert ca.save_token(token)
    else:
        def post(_url,device,_token,_timeout):
            code,body=wire.service.handle_activate({'device':device})
            payload=json.loads(body['lease'].rsplit('.',1)[0])
            payload['roaming_rebinds']=_meta(0)
            return code,dict(body,lease=CORE.sign_lease(payload,SECRET))
        monkeypatch.setattr(ca,'_post_activate',post)
    result=enforce.refresh_activation()
    state=enforce.activation_state(result)
    assert result.activated and state['run_full']
    assert state['roaming_rebinds'] is None and not state['roaming_rebind_limit_reached']
