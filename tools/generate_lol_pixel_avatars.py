#!/usr/bin/env python3
"""Build a small, local-only pixel avatar library from Riot Data Dragon art."""

from __future__ import annotations

import argparse
import json
import math
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps


HEROES = (
    ("Ahri", "阿狸"),
    ("Akali", "阿卡丽"),
    ("Annie", "安妮"),
    ("Ashe", "艾希"),
    ("Blitzcrank", "布里茨"),
    ("Braum", "布隆"),
    ("Caitlyn", "凯特琳"),
    ("Darius", "德莱厄斯"),
    ("Draven", "德莱文"),
    ("Ekko", "艾克"),
    ("Ezreal", "伊泽瑞尔"),
    ("Fiddlesticks", "费德提克"),
    ("Garen", "盖伦"),
    ("Gnar", "纳尔"),
    ("Jhin", "烬"),
    ("Jinx", "金克丝"),
    ("Kaisa", "卡莎"),
    ("Katarina", "卡特琳娜"),
    ("Kayle", "凯尔"),
    ("Kindred", "千珏"),
    ("LeeSin", "李青"),
    ("Leona", "蕾欧娜"),
    ("Lux", "拉克丝"),
    ("Malphite", "墨菲特"),
    ("Mordekaiser", "莫德凯撒"),
    ("Ornn", "奥恩"),
    ("Pyke", "派克"),
    ("Teemo", "提莫"),
    ("Thresh", "锤石"),
    ("Viego", "佛耶戈"),
    ("Yasuo", "亚索"),
    ("Zed", "劫"),
)

VERSIONS_URL = "https://ddragon.leagueoflegends.com/api/versions.json"
IMAGE_URL = "https://ddragon.leagueoflegends.com/cdn/{version}/img/champion/{hero}.png"
FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
)


def fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "xt005-local-avatar-builder/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def latest_version() -> str:
    versions = json.loads(fetch_bytes(VERSIONS_URL))
    if not versions:
        raise RuntimeError("Data Dragon returned no versions")
    return str(versions[0])


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def prepare_source(source: Image.Image) -> Image.Image:
    source = ImageOps.exif_transpose(source).convert("RGB")
    side = min(source.size)
    left = (source.width - side) // 2
    top = (source.height - side) // 2
    source = source.crop((left, top, left + side, top + side))
    source = ImageEnhance.Contrast(source).enhance(1.12)
    source = ImageEnhance.Color(source).enhance(1.10)
    source = ImageEnhance.Sharpness(source).enhance(1.22)
    return source


def pixel_avatar(source: Image.Image, colors: int) -> Image.Image:
    reduced = prepare_source(source).resize((32, 32), Image.Resampling.LANCZOS)
    return reduced.quantize(colors=colors, method=Image.Quantize.MEDIANCUT).convert("RGB")


def contact_sheet(avatars: list[dict[str, object]], variant: str, colors: int) -> Image.Image:
    columns = 8
    rows = math.ceil(len(avatars) / columns)
    cell_width = 156
    cell_height = 184
    margin = 24
    sheet = Image.new(
        "RGB",
        (margin * 2 + columns * cell_width, 104 + margin + rows * cell_height),
        "#090e0d",
    )
    draw = ImageDraw.Draw(sheet)
    title_font = font(28)
    label_font = font(16)
    meta_font = font(13)
    draw.text((margin, 20), "LoL workspace pixel avatars", font=title_font, fill="#e9f3ef")
    draw.text(
        (margin, 62),
        f"32 x 32 source pixels | {colors} colors | 4x nearest-neighbor preview | {variant}",
        font=meta_font,
        fill="#83a99b",
    )

    for index, entry in enumerate(avatars):
        row, column = divmod(index, columns)
        x = margin + column * cell_width
        y = 104 + row * cell_height
        avatar = entry["image"]
        assert isinstance(avatar, Image.Image)
        preview = avatar.resize((128, 128), Image.Resampling.NEAREST)
        sheet.paste(preview, (x + 14, y))
        draw.rectangle((x + 13, y - 1, x + 142, y + 128), outline="#315247", width=1)
        draw.text(
            (x + cell_width // 2, y + 137),
            f"{entry['cn']}  {entry['id']}",
            font=label_font,
            anchor="ma",
            fill="#dcebe5",
        )
        draw.text(
            (x + cell_width // 2, y + 161),
            f"#{index + 1:02d}",
            font=meta_font,
            anchor="ma",
            fill="#709185",
        )
    return sheet


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/imagegen/lol-pixel-avatars"),
    )
    parser.add_argument("--version", default="")
    args = parser.parse_args()

    version = args.version or latest_version()
    source_dir = args.output / "sources"
    variants = (("pixel-16", 16), ("pixel-24", 24))
    source_dir.mkdir(parents=True, exist_ok=True)
    for variant, _ in variants:
        (args.output / variant / "32").mkdir(parents=True, exist_ok=True)
        (args.output / variant / "128").mkdir(parents=True, exist_ok=True)

    sources: dict[str, Image.Image] = {}
    for hero_id, _ in HEROES:
        source_path = source_dir / f"{hero_id}.png"
        if not source_path.exists():
            source_path.write_bytes(fetch_bytes(IMAGE_URL.format(version=version, hero=hero_id)))
        with Image.open(source_path) as image:
            sources[hero_id] = image.convert("RGB")

    manifest: dict[str, object] = {
        "source": "Riot Data Dragon",
        "version": version,
        "usage": "local personal workspace avatars",
        "logical_size": [32, 32],
        "default_variant": "pixel-24",
        "variants": {
            "pixel-16": {"colors": 16},
            "pixel-24": {"colors": 24},
        },
        "heroes": [],
    }
    for variant, colors in variants:
        rendered: list[dict[str, object]] = []
        for index, (hero_id, cn_name) in enumerate(HEROES, 1):
            avatar = pixel_avatar(sources[hero_id], colors)
            filename = f"{index:02d}-{hero_id.lower()}.png"
            avatar.save(args.output / variant / "32" / filename, optimize=True)
            avatar.resize((128, 128), Image.Resampling.NEAREST).save(
                args.output / variant / "128" / filename,
                optimize=True,
            )
            rendered.append({"id": hero_id, "cn": cn_name, "image": avatar})
            if variant == "pixel-24":
                manifest["heroes"].append(
                    {
                        "index": index,
                        "id": hero_id,
                        "name_zh": cn_name,
                        "file": f"{variant}/32/{filename}",
                    }
                )
        contact_sheet(rendered, variant, colors).save(
            args.output / f"contact-sheet-{variant}.png",
            optimize=True,
        )

    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Generated {len(HEROES)} avatars from Data Dragon {version} in {args.output}")


if __name__ == "__main__":
    main()
