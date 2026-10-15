"""Build homepage image assets from the approved design sources.

Usage (from drive-angular/):  python -I tools/build-home-assets.py

Inputs:
  design/reference/brown-reference.png  approved homepage design (1586x992)
  design/source/hero-car.png            hero photograph (1672x941)
Outputs (public/assets/home/):
  hero-car-{800,1240,1672}.webp         responsive hero sources, aspect ratio preserved
  car-current.webp, car-electric.webp   option-card vehicles cropped 1:1 from the reference

The card crops are limited to the reference resolution (1x at 1586px). Replace them with
original high-resolution renders when those become available; keep the same aspect ratio.
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = ROOT / 'design' / 'reference' / 'brown-reference.png'
HERO = ROOT / 'design' / 'source' / 'hero-car.png'
OUT = ROOT / 'public' / 'assets' / 'home'

# Card-interior rectangles in reference pixels (left, top, right, bottom), measured inside the 1px borders
# and below the selected card's 3px top border.
CARD_CROPS = {
    'car-current.webp': (1258, 755, 1536, 927),
    'car-electric.webp': (735, 755, 1022, 927),
}
HERO_WIDTHS = (800, 1240, 1672)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    reference = Image.open(REFERENCE).convert('RGB')
    if reference.size != (1586, 992):
        raise SystemExit(f'unexpected reference size {reference.size}')
    for name, box in CARD_CROPS.items():
        reference.crop(box).save(OUT / name, 'WEBP', quality=90, method=6)

    hero = Image.open(HERO).convert('RGB')
    for width in HERO_WIDTHS:
        height = round(hero.height * width / hero.width)
        hero.resize((width, height), Image.LANCZOS).save(OUT / f'hero-car-{width}.webp', 'WEBP', quality=82, method=6)

    for path in sorted(OUT.iterdir()):
        with Image.open(path) as image:
            print(f'{path.name}: {image.size[0]}x{image.size[1]}, {path.stat().st_size // 1024} KiB')


if __name__ == '__main__':
    main()
