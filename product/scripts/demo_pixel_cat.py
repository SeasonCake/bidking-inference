"""Live demo of the "推理中" pixel-cat indicator (warm theme).

Mimics the overlay header and cycles between the inference state (status "推理中"
+ animated pixel cat: tail wag / blink) and a rendered-result state, so you can
see the cat move and the show/hide transition without playing a real game.

Run:  python scripts/demo_pixel_cat.py
Close the window (or wait — it loops the demo) to exit.
"""

from __future__ import annotations

import sys
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hero_ref" / "tools"))

from ahmad_tk_overlay import (  # noqa: E402
    SUPPORT_FEED_TEXT,
    SUPPORT_QR_IMAGE_PATH,
    BorderGlow,
    HoverTip,
    LyingCat,
    PixelCat,
    _blend_hex,
)

# Warm theme colours (kept in sync with THEMES["warm"]).
BG = "#f0eee6"
PANEL = "#faf9f5"
PANEL_SOFT = "#ece6d8"
BORDER = "#d6ccba"
INK = "#1f1e1d"
MUTED = "#6b6557"
ACCENT = "#cc785c"
WARM = "#d97757"
GOOD = "#3f7d5a"


def main() -> int:
    root = tk.Tk()
    root.title("pixel-cat demo")
    root.configure(bg=BG)
    root.geometry("460x600+200+90")
    root.attributes("-topmost", True)

    glow = BorderGlow(root)  # traveling border light (animated while "thinking")

    card = tk.Frame(root, bg=PANEL, padx=18, pady=14, highlightbackground=BORDER, highlightthickness=1)
    card.pack(padx=24, pady=24)

    tk.Frame(card, bg=ACCENT, height=2, width=320).pack(fill="x", pady=(0, 8))
    tk.Label(card, text="sophie · 2406 · R2", bg=PANEL, fg=INK, font=("Segoe UI", 15, "bold")).pack(anchor="w")
    tk.Label(card, text="bidding · ref_waiting", bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 10))

    row = tk.Frame(card, bg=PANEL)
    row.pack(anchor="w", fill="x")
    status = tk.Label(row, text="--:--", bg=PANEL_SOFT, fg=MUTED, padx=7, pady=4, font=("Segoe UI", 9))
    status.pack(side="left")
    cat = PixelCat(row, bg=PANEL)
    result = tk.Label(card, text="", bg=PANEL, fg=GOOD, font=("Segoe UI", 20, "bold"))
    result.pack(anchor="w", pady=(12, 0))

    # Bottom-right support mascot preview (real hover behaviour): hover the CAT for
    # the 文案 tooltip, hover the CAN for the Alipay QR popup.
    tk.Label(card, text="↓ 右下角支持组件：悬停猫=文案，悬停罐头=二维码",
             bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(16, 2))
    feed = tk.Frame(card, bg=PANEL)
    feed.pack(anchor="e", pady=(2, 0))
    lying = LyingCat(feed, bg=PANEL)
    lying.pack(side="left", padx=(0, 4))
    lying.start()
    HoverTip(lying, SUPPORT_FEED_TEXT)
    px, cw, ch = 2, 7, 9
    can = tk.Canvas(feed, width=cw * px, height=ch * px, bg=PANEL, highlightthickness=0, bd=0)
    can.create_rectangle(1 * px, 0, (cw - 1) * px, 2 * px, fill=_blend_hex(WARM, "#ffffff", 0.45), width=0)
    can.create_rectangle(1 * px, 1 * px, (cw - 1) * px, (ch - 1) * px, fill=WARM, width=0)
    can.create_rectangle(1 * px, 4 * px, (cw - 1) * px, 6 * px, fill=PANEL, width=0)
    can.create_rectangle(2 * px, 4 * px + 1, 4 * px, 5 * px + 1, fill=WARM, width=0)
    can.pack(side="left")
    try:
        can.configure(cursor="hand2")
        lying.configure(cursor="hand2")
    except tk.TclError:
        pass

    qr_state: dict[str, object] = {"pop": None, "photo": None}

    def show_qr(_e=None):
        try:
            from PIL import Image, ImageTk
        except Exception:
            return
        if qr_state["photo"] is None:
            im = Image.open(SUPPORT_QR_IMAGE_PATH)
            im.thumbnail((200, 200))
            qr_state["photo"] = ImageTk.PhotoImage(im)
        pop = qr_state["pop"]
        if pop is None:
            pop = tk.Toplevel(root)
            pop.overrideredirect(True)
            pop.attributes("-topmost", True)  # else it hides behind the topmost demo window
            pop.configure(bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
            tk.Label(pop, image=qr_state["photo"], bg=PANEL).pack(padx=6, pady=(6, 2))
            tk.Label(pop, text="扫码投喂 · 谢谢老板~", bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack(pady=(0, 6))
            qr_state["pop"] = pop
        else:
            pop.deiconify()
        pop.update_idletasks()
        x = can.winfo_rootx() + can.winfo_width() - pop.winfo_reqwidth()
        y = can.winfo_rooty() - pop.winfo_reqheight() - 6
        pop.geometry(f"+{max(0, int(x))}+{max(0, int(y))}")
        pop.lift()

    def hide_qr(_e=None):
        pop = qr_state["pop"]
        if pop is not None:
            pop.withdraw()

    can.bind("<Enter>", show_qr, add="+")
    can.bind("<Leave>", hide_qr, add="+")

    # Click the can to feed the cat: happy wag + a floating heart (up to 10).
    HEART = ((1, 0), (2, 0), (4, 0), (5, 0)) + tuple(
        (c, r) for r in (1, 2) for c in range(7)
    ) + ((1, 3), (2, 3), (3, 3), (4, 3), (5, 3), (2, 4), (3, 4), (4, 4), (3, 5))
    def feed(_e=None):
        lying.perk()
        hpop = tk.Toplevel(root)
        hpop.overrideredirect(True)
        hpop.attributes("-topmost", True)
        key = "#010203"
        try:
            hpop.attributes("-transparentcolor", key)
        except tk.TclError:
            key = PANEL
        hpx = 3
        hv = tk.Canvas(hpop, width=7 * hpx, height=7 * hpx, bg=key, highlightthickness=0, bd=0)
        hv.pack()
        for hc, hr in HEART:
            hv.create_rectangle(hc * hpx, hr * hpx, hc * hpx + hpx, hr * hpx + hpx, fill=WARM, width=0)
        x0 = lying.winfo_rootx() + lying.winfo_width() // 2
        y0 = lying.winfo_rooty()

        def rise(i):
            if i > 15:
                hpop.destroy()
                return
            hpop.geometry(f"+{x0 - 3 * hpx}+{int(y0 - 4 - i * 1.7)}")
            try:
                hpop.attributes("-alpha", max(0.0, 1.0 - i / 15))
            except tk.TclError:
                pass
            root.after(45, lambda: rise(i + 1))

        rise(0)

    can.bind("<Button-1>", feed, add="+")

    state = {"thinking": True}

    def tick_state() -> None:
        if state["thinking"]:
            status.configure(text=f"{time.strftime('%H:%M:%S')} · 推理中")
            cat.set_background(status.cget("bg"))
            if not cat.winfo_ismapped():
                cat.pack(side="left", padx=(5, 0))
            cat.start()
            glow.start()
            result.configure(text="")
            root.after(3500, flip)
        else:
            cat.stop()
            glow.stop()
            if cat.winfo_ismapped():
                cat.pack_forget()
            status.configure(text=f"{time.strftime('%H:%M:%S')} · 已更新")
            result.configure(text="参考  480,004")
            root.after(2500, flip)

    def flip() -> None:
        state["thinking"] = not state["thinking"]
        tick_state()

    tick_state()
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
