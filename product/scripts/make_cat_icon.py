"""生成 BidKing overlay 的小猫 taskbar 图标(多尺寸 .ico)→ hero_ref/assets/bidking_cat.ico。
一次性资产生成器(可重跑)。配色取自 overlay 自绘猫(cat orange #e2893f)。
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "hero_ref" / "assets" / "bidking_cat.ico"

S = 256
ORANGE = (226, 137, 63, 255)      # cat orange (#e2893f)
DARK = (60, 38, 18, 255)          # 深棕描边/五官
BG = (255, 255, 255, 255)         # 白底圆角
PINK = (231, 160, 150, 255)       # 内耳/鼻
WHITE = (245, 245, 245, 255)      # 眼白(在橙脸上)
WHISKER = (150, 150, 150, 255)    # 胡须(灰，白底/橙脸都看得见)


def _rrect(d, box, r, fill):
    d.rounded_rectangle(box, radius=r, fill=fill)


img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# 圆角底
_rrect(d, (8, 8, S - 8, S - 8), 52, BG)

cx, cy = S // 2, S // 2 + 14
# 耳朵(两个三角)
ear_w, ear_h = 70, 78
d.polygon([(cx - 92, cy - 28), (cx - 92 + ear_w, cy - 28), (cx - 92 + 18, cy - 28 - ear_h)], fill=ORANGE)
d.polygon([(cx + 92, cy - 28), (cx + 92 - ear_w, cy - 28), (cx + 92 - 18, cy - 28 - ear_h)], fill=ORANGE)
# 内耳
d.polygon([(cx - 78, cy - 36), (cx - 50, cy - 36), (cx - 70, cy - 76)], fill=PINK)
d.polygon([(cx + 78, cy - 36), (cx + 50, cy - 36), (cx + 70, cy - 76)], fill=PINK)
# 头(椭圆)
d.ellipse((cx - 96, cy - 64, cx + 96, cy + 92), fill=ORANGE)
# 眼睛
for ex in (cx - 42, cx + 42):
    d.ellipse((ex - 17, cy - 18, ex + 17, cy + 22), fill=WHITE)
    d.ellipse((ex - 9, cy - 6, ex + 9, cy + 20), fill=DARK)
# 鼻
d.polygon([(cx - 12, cy + 30), (cx + 12, cy + 30), (cx, cy + 44)], fill=PINK)
# 嘴
d.arc((cx - 26, cy + 30, cx, cy + 60), start=20, end=160, fill=DARK, width=5)
d.arc((cx, cy + 30, cx + 26, cy + 60), start=20, end=160, fill=DARK, width=5)
# 胡须
for dy in (-6, 8, 22):
    d.line((cx - 60, cy + 28 + dy, cx - 116, cy + 18 + dy), fill=WHISKER, width=4)
    d.line((cx + 60, cy + 28 + dy, cx + 116, cy + 18 + dy), fill=WHISKER, width=4)

OUT.parent.mkdir(parents=True, exist_ok=True)
img.save(OUT, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(f"written {OUT}  ({OUT.stat().st_size} bytes)")
