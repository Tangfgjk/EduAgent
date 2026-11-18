"""A shared, declarative slide canvas for browser, PPTX, PDF and video."""

import base64, io, os, threading
from functools import lru_cache

W, H = 1200, 675
_lock = threading.RLock()


def wrap(text, width):
    lines = []
    current = ""
    size = 0
    for char in text:
        n = 2 if ord(char) > 255 else 1
        if char == "\n" or size + n > width:
            lines.append(current)
            current = "" if char == "\n" else char
            size = 0 if char == "\n" else n
        else:
            current += char
            size += n
    if current:
        lines.append(current)
    return lines


class FormulaRenderError(ValueError):
    """Valid formula data could not be typeset; safe to report without raw input."""


@lru_cache(maxsize=256)
def formula_png(formula):
    import json
    import shutil
    import subprocess
    from pathlib import Path

    formula = formula.strip().strip("$")
    import hashlib
    import uuid
    from deeptutor.runtime.home import get_runtime_home
    cache = get_runtime_home() / "data/system/formula-cache"
    cache.mkdir(parents=True,exist_ok=True)
    cached = cache / (hashlib.sha256(("mathjax-v2:"+formula).encode()).hexdigest()+".png")
    if cached.is_file():
        data=cached.read_bytes()
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return base64.b64encode(data).decode()
    node = shutil.which("node")
    script = Path(__file__).resolve().parents[2] / "web/scripts/render-slide-formula.mjs"
    if not node or not script.is_file():
        raise RuntimeError("缺少本地公式渲染环境，请安装 Node.js 并在 web 目录执行 npm ci")
    with _lock:
        try:
            result = subprocess.run(
                [node, "--max-old-space-size=256", str(script)],
                input=json.dumps({"formula": formula}, ensure_ascii=False),
                capture_output=True, text=True, encoding="utf-8", timeout=25,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
        except subprocess.TimeoutExpired as exc:
            raise FormulaRenderError("公式渲染超时，请拆分复杂公式") from exc
        if result.returncode:
            if "ERR_MODULE_NOT_FOUND" in result.stderr:
                raise RuntimeError("本地公式依赖缺失，请在 web 目录执行 npm ci")
            raise FormulaRenderError("公式渲染失败，请检查 LaTeX 语法并拆分过长公式")
        try:
            encoded = json.loads(result.stdout)["data"]
            temporary = cache / (uuid.uuid4().hex+".tmp")
            temporary.write_bytes(base64.b64decode(encoded))
            os.replace(temporary,cached)
            return encoded
        except (ValueError, KeyError) as exc:
            raise RuntimeError("本地公式渲染服务返回无效结果") from exc


def elements(page):
    v = page["visual"]
    out = []

    def rect(x, y, w, h, fill):
        out.append({"type": "rect", "x": x, "y": y, "w": w, "h": h, "fill": fill})

    def text(x, y, w, content, size=24, color="#233b34", bold=False):
        lines = wrap(content, max(10, int(w / (size * 0.53))))
        out.append(
            {
                "type": "text",
                "x": x,
                "y": y,
                "w": w,
                "h": len(lines) * size * 1.25,
                "lines": lines,
                "size": size,
                "color": color,
                "bold": bold,
            }
        )

    def line(x1, y1, x2, y2, color="#438f72"):
        out.append({"type": "line", "x": x1, "y": y1, "x2": x2, "y2": y2, "color": color})

    rect(0, 0, W, H, "#f5f7f2")
    rect(0, 0, 12, H, "#246b55")
    text(45, 25, 1110, v["title"], 34, "#174d3a", True)
    text(45, 90, 1110, v["content"], 20)
    top = 205
    if v.get("formula"):
        png = formula_png(v["formula"])
        from PIL import Image

        image = Image.open(io.BytesIO(base64.b64decode(png)))
        ratio = min(1000 / image.width, 72 / image.height)
        out.append(
            {
                "type": "image",
                "x": 50,
                "y": 185,
                "w": image.width * ratio,
                "h": image.height * ratio,
                "data": png,
            }
        )
        top = 275
    panels = v["panels"]
    if v["layout"] == "coordinate":
        rect(650, top, 500, 340, "#ffffff")
        cx, cy = 900, top + 170
        line(670, cy, 1130, cy, "#a7b6ae")
        line(cx, top + 15, cx, top + 325, "#a7b6ae")
        palette = ["#246b55", "#ce7847", "#5467a0"]
        for i, (a, b, c) in enumerate(v.get("lines", [])):
            if b:
                # Clip the line to x/y plot bounds.
                pts = []
                for x in (-10, 10):
                    y = (c - a * x) / b
                    if -7 <= y <= 7:
                        pts.append((x, y))
                if a:
                    for y in (-7, 7):
                        x = (c - b * y) / a
                        if -10 <= x <= 10:
                            pts.append((x, y))
                if len(pts) >= 2:
                    line(
                        cx + pts[0][0] * 20,
                        cy - pts[0][1] * 20,
                        cx + pts[1][0] * 20,
                        cy - pts[1][1] * 20,
                        palette[i],
                    )
            elif a and -10 <= c / a <= 10:
                line(cx + c / a * 20, top + 30, cx + c / a * 20, top + 310, palette[i])
            text(675, top + 10 + i * 26, 450, f"{a:g}x + {b:g}y = {c:g}", 16, palette[i])
        for i, p in enumerate(panels):
            y = top + i * int(340 / len(panels))
            text(50, y, 560, p["heading"], 23, "#174d3a", True)
            text(50, y + 32, 560, p["text"], 19)
    elif v["layout"] == "process":
        count = len(panels)
        pw = 1080 / count
        for i, p in enumerate(panels):
            x = 45 + i * pw
            rect(x, top, pw - 22, 325, "#ffffff")
            text(x + 15, top + 18, pw - 50, str(i + 1) + ". " + p["heading"], 22, "#174d3a", True)
            text(x + 15, top + 90, pw - 50, p["text"], 19)
            if i < count - 1:
                text(x + pw - 24, top + 130, 40, "→", 24)
    else:
        count = len(panels)
        columns = 2 if count > 1 else 1
        rows = (count + columns - 1) // columns
        pw = 1100 / columns
        ph = (H - top - 45) / rows
        for i, p in enumerate(panels):
            x = 45 + (i % columns) * pw
            y = top + (i // columns) * ph
            rect(x, y, pw - 18, ph - 16, "#ffffff")
            rect(x, y, 5, ph - 16, "#438f72" if i % 2 == 0 else "#ce7847")
            text(x + 20, y + 14, pw - 55, p["heading"], 23, "#174d3a", True)
            text(x + 20, y + 58, pw - 55, p["text"], 20)
    for el in out:
        if el["type"] == "text" and el["y"] + el["h"] > H - 12:
            raise ValueError("页面文字超出版式，请分成更多页")
    return out


def png(page, path):
    from PIL import Image, ImageDraw, ImageFont
    from deeptutor.learning.classroom_media import font_path

    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)
    for e in elements(page):
        if e["type"] == "rect":
            draw.rectangle((e["x"], e["y"], e["x"] + e["w"], e["y"] + e["h"]), fill=e["fill"])
        elif e["type"] == "line":
            draw.line((e["x"], e["y"], e["x2"], e["y2"]), fill=e["color"], width=3)
        elif e["type"] == "text":
            font = ImageFont.truetype(font_path(), e["size"])
            for i, t in enumerate(e["lines"]):
                draw.text((e["x"], e["y"] + i * e["size"] * 1.25), t, font=font, fill=e["color"])
        elif e["type"] == "image":
            picture = (
                Image.open(io.BytesIO(base64.b64decode(e["data"])))
                .convert("RGBA")
                .resize((round(e["w"]), round(e["h"])))
            )
            img.paste(picture, (round(e["x"]), round(e["y"])), picture)
    img.save(path)


def export(body, root, kind):
    if kind == "pdf":
        from reportlab.pdfgen import canvas
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.lib.utils import ImageReader

        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        deck = canvas.Canvas(str(root / "lesson.pdf"), pagesize=(W, H))
        for page in body["pages"]:
            for e in elements(page):
                if e["type"] == "rect":
                    deck.setFillColor(e["fill"])
                    deck.rect(e["x"], H - e["y"] - e["h"], e["w"], e["h"], fill=1, stroke=0)
                elif e["type"] == "line":
                    deck.setStrokeColor(e["color"])
                    deck.line(e["x"], H - e["y"], e["x2"], H - e["y2"])
                elif e["type"] == "text":
                    deck.setFillColor(e["color"])
                    deck.setFont("STSong-Light", e["size"])
                    for i, t in enumerate(e["lines"]):
                        deck.drawString(e["x"], H - e["y"] - e["size"] - i * e["size"] * 1.25, t)
                elif e["type"] == "image":
                    deck.drawImage(
                        ImageReader(io.BytesIO(base64.b64decode(e["data"]))),
                        e["x"],
                        H - e["y"] - e["h"],
                        e["w"],
                        e["h"],
                        mask="auto",
                    )
            deck.showPage()
        deck.setFont("STSong-Light", 20)
        deck.drawString(45, H - 50, "资料来源")
        y = H - 90
        for src in body["sources"]:
            if y < 40:
                deck.showPage()
                deck.setFont("STSong-Light", 20)
                y = H - 50
            deck.drawString(45, y, src["label"])
            y -= 32
        deck.save()
        return ["lesson.pdf"]
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR

    deck = Presentation()
    deck.slide_width = Inches(13.333)
    deck.slide_height = Inches(7.5)
    scale = 914400 / 90
    for page in body["pages"]:
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        for e in elements(page):
            x, y = round(e["x"] * scale), round(e["y"] * scale)
            if e["type"] == "rect":
                shape = slide.shapes.add_shape(
                    MSO_SHAPE.RECTANGLE, x, y, round(e["w"] * scale), round(e["h"] * scale)
                )
                shape.fill.solid()
                shape.fill.fore_color.rgb = RGBColor.from_string(e["fill"][1:])
                shape.line.fill.background()
            elif e["type"] == "line":
                shape = slide.shapes.add_connector(
                    MSO_CONNECTOR.STRAIGHT, x, y, round(e["x2"] * scale), round(e["y2"] * scale)
                )
                shape.line.color.rgb = RGBColor.from_string(e["color"][1:])
                shape.line.width = Pt(2)
            elif e["type"] == "image":
                slide.shapes.add_picture(
                    io.BytesIO(base64.b64decode(e["data"])),
                    x,
                    y,
                    round(e["w"] * scale),
                    round(e["h"] * scale),
                )
            elif e["type"] == "text":
                frame = slide.shapes.add_textbox(
                    x, y, round(e["w"] * scale), round((e["h"] + 8) * scale)
                ).text_frame
                frame.margin_left = 0
                frame.margin_top = 0
                for i, t in enumerate(e["lines"]):
                    p = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
                    p.text = t
                    p.font.name = "Microsoft YaHei"
                    p.font.size = Pt(e["size"] * 0.8)
                    p.font.bold = e["bold"]
                    p.font.color.rgb = RGBColor.from_string(e["color"][1:])
                    p.space_after = Pt(0)
        slide.notes_slide.notes_text_frame.text = (
            page["visual"].get("notes", "") + "\n来源：" + ",".join(page.get("source_ids", []))
        )
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    slide.shapes.add_textbox(Inches(0.6), Inches(0.6), Inches(12), Inches(6)).text_frame.text = (
        "资料来源\n" + "\n".join(s["label"] for s in body["sources"])
    )
    deck.save(root / "lesson.pptx")
    return ["lesson.pptx"]
