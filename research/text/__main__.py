"""Print an artificial Chinese text correction example."""
import json
from . import normalize_ocr_text


def main():
    text = "伊森空间觉知\r\n优品均格：优品均格：2.5\n自色意品"
    print(json.dumps({"synthetic": True, "before": text, "after": normalize_ocr_text(text)},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
