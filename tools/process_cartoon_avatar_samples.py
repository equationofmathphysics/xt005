#!/usr/bin/env python3
"""Create UI-sized pixel avatars and a review sheet from generated sources."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont


ROOT = Path("output/imagegen/lol-cartoon-samples")
HEROES = (
    ("ahri", "阿狸", "Ahri"),
    ("jinx", "金克丝", "Jinx"),
    ("yasuo", "亚索", "Yasuo"),
    ("teemo", "提莫", "Teemo"),
)
FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc"


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if Path(FONT_PATH).is_file():
        return ImageFont.truetype(FONT_PATH, size=size)
    return ImageFont.load_default()


def avatar(source: Image.Image, size: int) -> Image.Image:
    image = source.convert("RGB").resize((size, size), Image.Resampling.LANCZOS)
    image = ImageEnhance.Contrast(image).enhance(1.08)
    image = ImageEnhance.Sharpness(image).enhance(1.35)
    return image.quantize(
        colors=32,
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.NONE,
    ).convert("RGB")


def main() -> None:
    output_32 = ROOT / "pixel-32"
    output_48 = ROOT / "pixel-48"
    output_32.mkdir(parents=True, exist_ok=True)
    output_48.mkdir(parents=True, exist_ok=True)

    processed: list[tuple[str, str, Image.Image, Image.Image]] = []
    for slug, name_zh, name_en in HEROES:
        with Image.open(ROOT / f"{slug}-source.png") as source:
            small = avatar(source, 32)
            regular = avatar(source, 48)
        small.save(output_32 / f"{slug}.png", optimize=True)
        regular.save(output_48 / f"{slug}.png", optimize=True)
        processed.append((name_zh, name_en, small, regular))

    sheet = Image.new("RGB", (1100, 650), "#090e0d")
    draw = ImageDraw.Draw(sheet)
    draw.text((34, 24), "Cartoon pixel avatar samples", font=font(30), fill="#e7f1ed")
    draw.text(
        (34, 70),
        "32 colors | generated cartoon source | 48 px default, 32 px compact",
        font=font(16),
        fill="#80a89a",
    )

    for index, (name_zh, name_en, small, regular) in enumerate(processed):
        x = 34 + index * 266
        y = 118
        draw.rectangle((x, y, x + 238, y + 450), fill="#0d1512", outline="#29473d")
        draw.text((x + 119, y + 20), f"{name_zh}  {name_en}", font=font(20), anchor="ma", fill="#dcebe5")
        regular_preview = regular.resize((192, 192), Image.Resampling.NEAREST)
        sheet.paste(regular_preview, (x + 23, y + 58))
        draw.rectangle((x + 22, y + 57, x + 215, y + 250), outline="#416f5f")
        draw.text((x + 119, y + 262), "48 x 48 / 4x preview", font=font(14), anchor="ma", fill="#9fbab0")
        small_preview = small.resize((128, 128), Image.Resampling.NEAREST)
        sheet.paste(small_preview, (x + 55, y + 300))
        draw.rectangle((x + 54, y + 299, x + 183, y + 428), outline="#416f5f")
        draw.text((x + 119, y + 438), "32 x 32 / 4x preview", font=font(14), anchor="ma", fill="#9fbab0")

    sheet.save(ROOT / "contact-sheet.png", optimize=True)
    print(f"Processed {len(processed)} cartoon avatar samples in {ROOT}")


if __name__ == "__main__":
    main()
