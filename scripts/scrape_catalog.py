"""Scrape the *whole* cars.av.by catalogue, not just the first N pages.

cars.av.by serves at most 120 pages (3 000 adverts) for one query: page 121
comes back with an empty filter payload even though ``pageCount`` claims 4 204.
The catalogue is therefore sliced by brand (``brands[0][brand]=<id>``), and
brands bigger than 3 000 listings are sliced again by rotating the site's sort
orders (``sort=1..8``), each of which exposes a different 3 000-listing window.

Every brand is written to its own part file, so a blocked or interrupted run
can be resumed without losing hours of work::

    py -m scripts.scrape_catalog                 # all brands, resumable
    py -m scripts.scrape_catalog --share 0.5     # the top half of every brand
    py -m scripts.scrape_catalog --brands bmw,vw  # just two brands
    py -m scripts.scrape_catalog --merge-only     # parts -> data/cars.json

``--share 0.5`` stops each brand after half of its advertised total, taking the
site's own relevance order (``sort=1``) first, so the result is the most
relevant half of the catalogue. It halves both the runtime and the dataset.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scraper.avby import (  # noqa: E402
    BASE_URL,
    FILTER_URL,
    HEADERS,
    Car,
    ScrapeError,
    _opener,
    parse_next_data,
    parse_page,
    warm_up,
)

PAGE_LIMIT = 120  # hard cap: the site returns an empty payload from page 121 on
SORTS = (1, 2, 3, 4, 5, 6, 7, 8)  # distinct orders; sort>=9 falls back to default
BRAND_URL = BASE_URL + "/{slug}"
FILTER_BRAND_URL = BASE_URL + "/filter?brands%5B0%5D%5Bbrand%5D={brand_id}&sort={sort}&page={page}"


def fetch_html(url: str, timeout: float = 45.0) -> str:
    request = urllib.request.Request(url, headers=HEADERS)
    with _opener().open(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def brand_slugs_from_sitemap() -> list[str]:
    """Every brand slug that has at least one advert URL in the sitemap."""
    index = fetch_html(BASE_URL + "/sitemap.xml")
    maps = re.findall(r"<loc>([^<]+)</loc>", index)
    slugs: set[str] = set()
    for map_url in maps:
        body = fetch_html(map_url)
        for url in re.findall(r"<loc>https://cars\.av\.by/([^/<]+)/", body):
            slugs.add(url)
    slugs.discard("filter")
    return sorted(slugs)


def discover(slug: str) -> dict[str, Any]:
    """Read a brand landing page once: numeric id, advertised total, display name."""
    html = fetch_html(BRAND_URL.format(slug=slug))
    cars, pagination = parse_page(html)
    if not cars:
        raise ScrapeError(f"{slug}: landing page has no adverts")
    brand_id = None
    adverts = parse_next_data(html)["props"]["initialState"]["filter"]["main"]["adverts"]
    for advert in adverts:
        meta = advert.get("metadata") or {}
        if meta.get("brandId") and meta.get("brandSlug") == slug:
            brand_id = int(meta["brandId"])
            break
    return {
        "slug": slug,
        "brand_id": brand_id,
        "name": cars[0].brand,
        "total": pagination.get("total") or 0,
    }


def scrape_brand(
    slug: str,
    brand_id: int,
    total: int,
    delay: float,
    share: float = 1.0,
    on_page: Any = None,
) -> list[Car]:
    """The top ``share`` of one brand: page 1..120 across sort orders."""
    seen: dict[int, Car] = {}
    needed = max(1, math.ceil(total * share))
    for sort in SORTS:
        page = 1
        while page <= PAGE_LIMIT:
            cars, pagination = parse_page(
                fetch_html(FILTER_BRAND_URL.format(brand_id=brand_id, sort=sort, page=page))
            )
            if not cars:
                break
            fresh = 0
            for car in cars:
                if car.id not in seen:
                    seen[car.id] = car
                    fresh += 1
            if on_page:
                on_page(slug, sort, page, fresh, len(seen), needed)
            page_count = pagination.get("page_count") or 0
            if page >= page_count:
                break
            if len(seen) >= needed * 0.999:
                break
            page += 1
            time.sleep(delay)
        if len(seen) >= needed * 0.999:
            break
        time.sleep(delay)
    return list(seen.values())


def part_path(parts: Path, slug: str) -> Path:
    return parts / f"{slug}.json"


def write_part(parts: Path, slug: str, cars: Iterable[Car], total: int, target: int) -> int:
    payload = {
        "slug": slug,
        "total": total,
        "target": target,
        "count": len(list(cars)) if not isinstance(cars, list) else len(cars),
        "cars": [car.to_dict() for car in cars],
    }
    parts.mkdir(parents=True, exist_ok=True)
    target = part_path(parts, slug)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(target)
    return payload["count"]


def merge(parts: Path, out: Path) -> int:
    cars: dict[int, dict[str, Any]] = {}
    for path in sorted(parts.glob("*.json")):
        for car in json.loads(path.read_text(encoding="utf-8"))["cars"]:
            cars.setdefault(car["id"], car)
    dataset = {
        "source": FILTER_URL.format(page=1),
        "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pages": None,
        "count": len(cars),
        "cars": list(cars.values()),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(dataset, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(out)
    return len(cars)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Scrape the full cars.av.by catalogue")
    parser.add_argument("--parts", type=Path, default=ROOT / "data" / "catalog-parts")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "cars.json")
    parser.add_argument("--delay", type=float, default=0.45)
    parser.add_argument(
        "--share",
        type=float,
        default=1.0,
        help="fraction of each brand to collect, e.g. 0.5 for the most relevant half",
    )
    parser.add_argument("--brands", type=str, default="", help="comma separated slugs")
    parser.add_argument("--force", action="store_true", help="re-scrape existing parts")
    parser.add_argument("--merge-only", action="store_true")
    args = parser.parse_args(argv)

    if args.merge_only:
        count = merge(args.parts, args.out)
        print(f"merged {count} unique cars -> {args.out}")
        return 0

    warm_up()
    wanted = {b.strip() for b in args.brands.split(",") if b.strip()}
    if wanted:
        slugs = sorted(wanted)
    else:
        slugs = brand_slugs_from_sitemap()
        print(f"brands in sitemap: {len(slugs)}", flush=True)

    started = time.time()
    for index, slug in enumerate(slugs, 1):
        target = part_path(args.parts, slug)
        if target.exists() and not args.force:
            part = json.loads(target.read_text(encoding="utf-8"))
            want = part.get("target") or int((part.get("total") or 0) * args.share)
            if part.get("count", 0) >= want * 0.98:
                print(
                    f"[{index}/{len(slugs)}] {slug}: cached {part['count']}/{want}"
                    f" of {part.get('total')}",
                    flush=True,
                )
                continue
        try:
            info = discover(slug)
            brand_id = info.get("brand_id")
            if brand_id is None:
                print(f"[{index}/{len(slugs)}] {slug}: no brand id, skipped", flush=True)
                continue
            want = max(1, math.ceil(info["total"] * args.share))
            print(
                f"[{index}/{len(slugs)}] {slug}: id={brand_id} total={info['total']} "
                f"-> need {want} (~{min(PAGE_LIMIT, -(-want // 25))} pages)",
                flush=True,
            )

            def progress(slug_: str, sort: int, page: int, fresh: int, unique: int, needed: int) -> None:
                if page % 25 == 0 or fresh == 0 or unique >= needed:
                    print(
                        f"    {slug_} sort={sort} page={page:>3} +{fresh:>2} unique={unique}/{needed}",
                        flush=True,
                    )

            cars = scrape_brand(slug, brand_id, info["total"], args.delay, args.share, progress)
            count = write_part(args.parts, slug, cars, info["total"], want)
            print(f"    -> {count} unique ({count / max(info['total'], 1) * 100:.1f}%)", flush=True)
        except Exception as exc:
            print(f"[{index}/{len(slugs)}] {slug}: FAILED {exc}", flush=True)
            time.sleep(3)
        time.sleep(args.delay)

    elapsed = time.time() - started
    count = merge(args.parts, args.out)
    print(f"\ndone in {elapsed / 60:.1f} min | merged {count} unique cars -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
