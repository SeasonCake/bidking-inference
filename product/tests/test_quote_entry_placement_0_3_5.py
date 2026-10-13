"""QUOTE-ENTRY-02 wiring/lifecycle only; no Tk window or visual verdict."""
from __future__ import annotations
import ast
import copy
from pathlib import Path
from types import SimpleNamespace
import pytest

B = Path(__file__).resolve().parents[1]
SOURCE = B / "hero_ref/tools/ahmad_tk_overlay.py"
S = Path("C:/example/bidking-workspace/scratch/bidking_035_quote_entry_placement_20260906_01")


def _class(tree):
    return next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AhmadTkOverlay")


def _init(tree):
    return next(node for node in _class(tree).body if isinstance(node, ast.FunctionDef) and node.name == "__init__")


def _assigns(node, name):
    return isinstance(node, ast.Assign) and any(isinstance(target, ast.Attribute) and target.attr == name
                                               and isinstance(target.value, ast.Name) and target.value.id == "self"
                                               for target in node.targets)


def _entry_call(tree):
    calls = [node.value for node in ast.walk(_init(tree)) if _assigns(node, "quote_button")]
    assert len(calls) == 1, "one quote entry instance only"
    return calls[0]


def _assert_parent(tree):
    call = _entry_call(tree)
    assert ast.unparse(call.args[0]) == "self.quote_status_row"


def test_entry_uses_stable_status_strip_not_footer():
    _assert_parent(ast.parse(SOURCE.read_text(encoding="utf-8")))


class Widget:
    def __init__(self, parent=None, **options):
        self.parent = self.master = parent
        self.options, self.children, self.bindings = options, [], {}
        self.destroyed = False
        self.pack_options = None
        if parent is not None:
            parent.children.append(self)
    def cget(self, name):
        return self.options.get(name)
    def configure(self, cnf=None, **kwargs):
        self.options.update(cnf or {})
        self.options.update(kwargs)
    config = configure
    def pack(self, **kwargs):
        self.pack_options = kwargs
    pack_configure = pack
    grid = grid_configure = place = place_configure = pack
    def pack_forget(self):
        self.pack_options = None
    grid_forget = place_forget = pack_forget
    def bind(self, event, callback, add=None):
        if add != "+":
            self.bindings[event] = []
        self.bindings.setdefault(event, []).append(callback)
    def create_window(self, *args, **kwargs):
        pass
    def winfo_children(self):
        return list(self.children)
    def destroy(self):
        for child in list(self.children):
            child.destroy()
        self.destroyed = True
        if self.parent is not None:
            self.parent.children.remove(self)


class Tip:
    instances = []
    def __init__(self, widget, text):
        self.widget, self.text = widget, text
        self.instances.append(self)
    def set_text(self, text):
        self.text = text


def _surface(tree=None):
    tree = tree or ast.parse(SOURCE.read_text(encoding="utf-8"))
    init = _init(tree)
    start = next(i for i, node in enumerate(init.body) if _assigns(node, "quote_status_row"))
    end = next(i for i, node in enumerate(init.body[start:], start)
               if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "prices")
    methods = [copy.deepcopy(node) for node in _class(tree).body if isinstance(node, ast.FunctionDef)
               and node.name in {"_rounded_button", "_render_flags", "render_standby"}]
    assert len(methods) == 3
    namespace = {"tk": SimpleNamespace(Canvas=Widget, Frame=Widget, Label=Widget, TclError=RuntimeError),
                 "HoverTip": Tip, "_flag_color": lambda level: level,
                 "_text": lambda value, fallback="": str(value) if value is not None else fallback,
                 **dict.fromkeys(("BG", "PANEL_SOFT", "ACCENT", "FONT_UI", "BORDER"), "theme-token")}
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
                             ast.ClassDef(name="Surface", bases=[], keywords=[], body=methods, decorator_list=[])], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(SOURCE), "exec"), namespace)
    obj = namespace["Surface"]()
    obj.shell = Widget(bg="theme-token")
    obj._apply_font_scale = lambda widget: None  # Geometry and actual DPI remain central's GUI check.
    obj._sync_rounded_card = obj._draw_rounded_card = lambda holder: None
    obj._open_calls = []
    obj._open_quote_preferences = lambda event=None: obj._open_calls.append(event)
    namespace["self"] = obj
    exec(compile(ast.Module(body=copy.deepcopy(init.body[start:end]), type_ignores=[]), str(SOURCE), "exec"), namespace)
    return obj


@pytest.mark.parametrize("cycles", [1, 3])
def test_actual_flags_refresh_never_destroys_or_duplicates_quote(cycles):
    obj = _surface()
    button, holder, tip = obj.quote_button, obj.quote_button._rounded_holder, obj.quote_tip
    assert holder.parent is obj.quote_status_row and obj.flags.parent is obj.quote_status_row
    assert holder not in obj.flags.children
    assert holder.pack_options == {"side": "right", "padx": (6, 0)}
    assert obj.flags.pack_options == {"side": "left", "fill": "x", "expand": True}
    assert obj.quote_status_row.pack_options == {"fill": "x", "pady": (5, 4)}
    button.bindings["<Button-1>"][0]("click")
    assert obj._open_calls == ["click"] and tip.widget is button
    tip.set_text("当前无可调整三档，未应用")
    arms = [[], [{"label": "正常", "level": "neutral", "detail": "正常详情"}],
            [{"label": "长状态" + str(i), "level": "watch", "detail": "详情" + str(i)} for i in range(4)],
            [{"label": "等待实时包", "level": "watch", "detail": "待机"}]]
    for _ in range(cycles):
        for flags in arms:
            obj._render_flags(flags)
            assert not button.destroyed and not holder.destroyed and obj.quote_tip is tip
            assert tip.text == "当前无可调整三档，未应用"
            assert [child.options["text"] for child in obj.flags.children] == ([flag["label"] for flag in flags] or ["正常监测"])
            for child, flag in zip(obj.flags.children, flags):
                if flag["detail"]:
                    assert any(t.widget is child and t.text == flag["detail"] for t in Tip.instances)
            assert len(obj.quote_status_row.children) == 2
    button.configure(bg="changed-theme")
    assert holder._round_card["bg"] == "changed-theme"
    assert holder in obj._rounded_cards


def test_thin_style_single_instance_and_footer_removed():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    call = _entry_call(tree)
    options = {keyword.arg: ast.unparse(keyword.value) for keyword in call.keywords}
    assert options["text"] == "'报价'" and options["bg"] == "PANEL_SOFT" and options["fg"] == "ACCENT"
    assert options["pady"] == "0" and options["radius"] == "3" and "7" in options["font"]
    _assert_parent(tree)
    obj = _surface(tree)
    assert obj.quote_button._rounded_holder._round_card["inset"] == 1
    assert obj.quote_button.options["pady"] == 0


def test_every_other_function_and_nonlayout_initializer_statement_unchanged():
    baseline = ast.parse((S / "BASELINE/hero_ref/tools/ahmad_tk_overlay.py").read_text(encoding="utf-8"))
    current = ast.parse(SOURCE.read_text(encoding="utf-8"))
    before, after = _init(baseline), _init(current)
    def layout_statement(node):
        names = {child.attr for child in ast.walk(node) if isinstance(child, ast.Attribute)
                 and isinstance(child.value, ast.Name) and child.value.id == "self"}
        return bool(names.intersection({"flags", "quote_status_row", "quote_button", "quote_tip"}))
    assert [ast.dump(node) for node in before.body if not layout_statement(node)] == [ast.dump(node) for node in after.body if not layout_statement(node)]
    before.body = after.body = [ast.Pass()]
    assert ast.dump(baseline) == ast.dump(current)


@pytest.mark.parametrize("bad_parent", ["flags", "footer_actions_row"])
def test_parent_checker_rejects_destroyable_or_footer_parent(bad_parent):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    _entry_call(tree).args[0] = ast.Attribute(value=ast.Name(id="self", ctx=ast.Load()), attr=bad_parent, ctx=ast.Load())
    with pytest.raises(AssertionError):
        _assert_parent(tree)


@pytest.mark.parametrize("flags", [[], [{"label": "已结算", "level": "neutral", "detail": "结算等待"}]])
def test_real_standby_method_retains_entry(flags):
    obj = _surface()
    obj.title, obj.status = Widget(), Widget()
    obj.action_rows = {"最近": Widget()}
    obj._capture_status = lambda: {}
    obj._is_settlement_summary = lambda data: False
    for name in ("_set_header_subtitle", "_set_settlement_button_state", "_clear_values", "_apply_action_cell",
                 "_set_label", "_set_candidate_autofit", "_sync_game_launch_candidate", "_update_detail_rows",
                 "_render_minimap", "_record_summary_diagnostic"):
        setattr(obj, name, lambda *a, **k: None)
    button, tip = obj.quote_button, obj.quote_tip
    obj.render_standby({"flags": flags, "stale": {"reason": "session_ahead", "age_seconds": 2}})
    assert not button.destroyed and not button._rounded_holder.destroyed and obj.quote_tip is tip
    assert [child.options["text"] for child in obj.flags.children] == ([item["label"] for item in flags] or ["待机"])
    button.bindings["<Button-1>"][0]("after-standby")
    assert obj._open_calls == ["after-standby"]
