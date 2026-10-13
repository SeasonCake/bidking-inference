"""Quick probe for AuctionAnalyzer exe packer type."""
from __future__ import annotations

import re
import struct
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXE = (
    REPO
    / "external_references"
    / "AuctionAnalyzer4.13.3"
    / "AuctionAnalyzer4.13.3.exe"
)


def main() -> None:
    data = EXE.read_bytes()
    print("size", len(data))

    patterns = [
        b"PyInstaller",
        b"pyi-windows-manifest",
        b"PYZ\x00",
        b"PYZ-00",
        b"python3",
        b"libpython",
        b"nuitka",
        b"Nuitka",
        b"onefile",
        b"cx_Freeze",
        b"py2exe",
        b"electron.asar",
        b"app.asar",
        b"streamlit",
        b"pandas",
        b"numpy",
        b"tkinter",
        b"PyQt",
        b"customtkinter",
        b"flask",
        b"fastapi",
        b"uvicorn",
        b"auction",
        b"Auction",
        b"bidking",
        b"Inno Setup",
        b"Nullsoft",
        b"7z",
    ]
    for pat in patterns:
        idx = data.find(pat)
        if idx != -1:
            ctx = data[max(0, idx - 30) : idx + len(pat) + 60]
            printable = "".join(chr(b) if 32 <= b < 127 else "." for b in ctx)
            print(f"{pat!r} @ {idx}: {printable[:140]}")

    cookie = b"MEI\x0c\x0b\x0a\x0b\x0e"
    print("PyInstaller cookie", data.rfind(cookie))

    # Nuitka onefile payload marker
    for marker in [b"NUITKA_ONEFILE_PARENT", b"NUITKA_PACKAGE", b"__nuitka__"]:
        print(marker, data.find(marker))

    # interesting ASCII strings near auction/analyzer
    for m in re.finditer(rb"[A-Za-z0-9_./\\-]{8,}", data):
        s = m.group().decode("ascii", "ignore")
        low = s.lower()
        if any(k in low for k in ("auction", "analyzer", "bid", "streamlit", "main.py", "app.py")):
            if len(s) < 120:
                print("str", m.start(), s)


if __name__ == "__main__":
    main()
