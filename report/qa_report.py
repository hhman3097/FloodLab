"""Render the report and generate contact sheets for explicit visual review."""
import json
import re
from pathlib import Path

import fitz
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "qa"


def contact(paths, name, columns=2, size=(650, 920)):
    for start in range(0, len(paths), 4):
        subset = paths[start:start+4]
        sheet = Image.new("RGB", (size[0] * columns, (size[1]+30)*2), "white")
        draw = ImageDraw.Draw(sheet)
        for index, path in enumerate(subset):
            image = Image.open(path).convert("RGB")
            image.thumbnail(size)
            x, y = (index % columns)*size[0], (index//columns)*(size[1]+30)
            sheet.paste(image, (x, y+30))
            draw.text((x+10, y+8), path.name, fill="black")
        sheet.save(OUT / f"{name}-{start//4+1:02d}.jpg", quality=90)


def main():
    OUT.mkdir(exist_ok=True)
    for pattern in ("page-*.png", "report-*.jpg"):
        for previous in OUT.glob(pattern):
            previous.unlink()
    document = fitz.open(ROOT / "build/main.pdf")
    paths, overflow = [], []
    for index, page in enumerate(document):
        path = OUT / f"page-{index+1:02d}.png"
        page.get_pixmap(dpi=110).save(path)
        paths.append(path)
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    box = fitz.Rect(span["bbox"])
                    if not page.rect.contains(box):
                        overflow.append({"page": index+1, "text": span["text"], "bbox": list(box)})
    contact(paths, "report")
    source = (ROOT / "main.tex").read_text().split("\\section{引言}", 1)[1].split("\\appendix", 1)[0]
    source = source.replace("\\input{comparison-results.tex}",
                            (ROOT / "comparison-results.tex").read_text())
    audit = {
        "pages": len(document), "main_chinese_characters": len(re.findall(r"[\u4e00-\u9fff]", source)),
        "text_outside_page": overflow, "visual_review": "requires inspection of rendered pages",
        "count_scope": "Main text plus captions/tables; excludes abstract, cover, appendix and references.",
    }
    (OUT / "automated-audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2))
    print(json.dumps(audit, ensure_ascii=False))


if __name__ == "__main__":
    main()
