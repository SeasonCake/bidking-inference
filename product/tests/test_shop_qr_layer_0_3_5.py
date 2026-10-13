"""SHOP-UI-02 owner/layer/lifecycle contracts; no actual Tk or visible PASS."""
from __future__ import annotations
import ast
import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest

B=Path(__file__).resolve().parents[1]
SOURCE=B/'hero_ref/tools/ahmad_tk_overlay.py'
S=Path('C:/example/bidking-workspace/scratch/bidking_035_shop_qr_layer_20260906_01')
URL='https://www.kufaka.com/shop/GGTO42L9'

class LayerError(Exception):
    pass

def _method(tree):
    cls=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='AhmadTkOverlay')
    return next(node for node in cls.body if isinstance(node,ast.FunctionDef) and node.name=='_add_shop_entry')

def _surface(*,failure=None,missing=False,size=225):
    method=_method(ast.parse(SOURCE.read_text(encoding='utf-8')))
    button,dialog=MagicMock(),MagicMock()
    button.winfo_rootx.return_value=20
    button.winfo_rooty.return_value=30
    button.winfo_height.return_value=20
    windows=[];photos=[];opened=[]
    def image(**kwargs):
        assert Path(kwargs['file'])==B/'hero_ref/assets/shop_qr.png'
        if missing:raise LayerError('missing synthetic QR')
        photo=MagicMock();photo.width.return_value=photo.height.return_value=size
        photos.append(photo);return photo
    def create(parent):
        assert parent is dialog
        window=MagicMock();windows.append(window)
        if failure:getattr(window,failure).side_effect=LayerError('synthetic layer failure')
        return window
    namespace={'tk':SimpleNamespace(PhotoImage=image,Toplevel=create,Label=lambda *a,**k:MagicMock(),TclError=LayerError),
               'SHOP_QR_IMAGE_PATH':B/'hero_ref/assets/shop_qr.png','SHOP_URL':URL,
               'PANEL_SOFT':'soft','ACCENT':'accent','webbrowser':SimpleNamespace(open=lambda url:opened.append(url))}
    module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),copy.deepcopy(method)],type_ignores=[])
    exec(compile(ast.fix_missing_locations(module),str(SOURCE),'exec'),namespace)
    namespace['_add_shop_entry'](SimpleNamespace(_rounded_button=lambda *a,**k:button),dialog)
    bindings={call.args[0]:call.args[1] for call in button.bind.call_args_list}
    destroyed={call.args[0]:call.args[1] for call in dialog.bind.call_args_list}['<Destroy>']
    return SimpleNamespace(button=button,dialog=dialog,windows=windows,photos=photos,opened=opened,
                           enter=bindings['<Enter>'],leave=bindings['<Leave>'],click=bindings['<Button-1>'],destroyed=destroyed)

def test_popup_has_explicit_owner_local_topmost_and_raise():
    obj=_surface();obj.enter(None)
    popup=obj.windows[0]
    popup.transient.assert_called_once_with(obj.dialog)
    popup.attributes.assert_called_once_with('-topmost',True)
    popup.lift.assert_called_once_with(obj.dialog)
    popup.overrideredirect.assert_called_once_with(True)
    popup.configure.assert_called_once_with(bg='white',padx=20,pady=20)
    popup.geometry.assert_called_once_with('+20+53')
    obj.dialog.attributes.assert_not_called()
    popup.focus_set.assert_not_called();popup.focus_force.assert_not_called();popup.grab_set.assert_not_called()

def test_repeat_hover_leave_child_and_parent_destroy_keep_single_popup():
    obj=_surface()
    for index in range(3):
        obj.enter(None)
        assert len(obj.windows)==index+1
        if index:obj.windows[index-1].destroy.assert_called_once()
        obj.windows[index].destroy.assert_not_called()
    current=obj.windows[-1]
    obj.destroyed(SimpleNamespace(widget=obj.button))
    current.destroy.assert_not_called()
    obj.leave(None);current.destroy.assert_called_once()
    obj.leave(None);current.destroy.assert_called_once()
    obj.enter(None)
    obj.destroyed(SimpleNamespace(widget=obj.dialog))
    obj.windows[-1].destroy.assert_called_once()
    obj.click(None)
    assert obj.opened==[URL]

@pytest.mark.parametrize('failure',['transient','attributes','configure','geometry','lift'])
def test_window_is_tracked_before_layer_or_layout_failure(failure):
    obj=_surface(failure=failure);obj.enter(None)
    assert len(obj.windows)==1
    obj.windows[0].destroy.assert_called_once()
    obj.leave(None);obj.windows[0].destroy.assert_called_once()
    obj.click(None);assert obj.opened==[URL]

@pytest.mark.parametrize('missing,size',[(True,225),(False,224)])
def test_bad_resource_never_creates_popup_but_link_remains(missing,size):
    obj=_surface(missing=missing,size=size);obj.enter(None)
    assert obj.windows==[]
    obj.click(None);assert obj.opened==[URL]

def test_only_shop_show_changed_and_diagnostic_guard_stays():
    before=ast.parse((S/'BASELINE/hero_ref/tools/ahmad_tk_overlay.py').read_text(encoding='utf-8'))
    after=ast.parse(SOURCE.read_text(encoding='utf-8'))
    a,b=_method(before),_method(after)
    show_a=next(n for n in a.body if isinstance(n,ast.FunctionDef) and n.name=='show')
    show_b=next(n for n in b.body if isinstance(n,ast.FunctionDef) and n.name=='show')
    show_a.body=show_b.body=[ast.Pass()]
    assert ast.dump(before)==ast.dump(after)
    cls=next(n for n in after.body if isinstance(n,ast.ClassDef) and n.name=='AhmadTkOverlay')
    dialog=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_open_token_dialog')
    gates=[n for n in ast.walk(dialog) if isinstance(n,ast.If) and ast.unparse(n.test)=='not self.network_diagnostic_cohort']
    assert any(any(isinstance(c,ast.Call) and isinstance(c.func,ast.Attribute) and c.func.attr=='_add_shop_entry'
                   for c in ast.walk(gate)) for gate in gates)
