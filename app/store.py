"""Dataset loading and filter-option derivation."""

from __future__ import annotations

import gzip
import json
from collections import Counter
from pathlib import Path
from threading import Lock
from typing import Any

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "cars.json"
DATA_GZ = DATA_FILE.parent / (DATA_FILE.name + ".gz")

_lock = Lock()
_cache: dict[str, Any] = {"mtime": None, "dataset": None}


def _dataset_path() -> Path | None:
    """Prefer the plain snapshot; fall back to the gzipped one.

    The 50% catalogue is ~31 MB of JSON, which GitHub's web upload rejects
    (25 MB per file), so the repository keeps ``data/cars.json.gz`` instead.
    Locally the plain file wins, which keeps the API and tests unchanged.
    """
    if DATA_FILE.exists():
        return DATA_FILE
    if DATA_GZ.exists():
        return DATA_GZ
    return None


def dataset_exists() -> bool:
    return _dataset_path() is not None


def load_dataset() -> dict[str, Any]:
    """Load ``data/cars.json`` (or ``.json.gz``) and reload it when it changes."""
    path = _dataset_path()
    if path is None:
        raise FileNotFoundError(
            f"{DATA_FILE} is missing. Run: refresh-50k.bat "
            "(or py -m scripts.scrape_catalog --share 0.5)"
        )
    mtime = path.stat().st_mtime
    if _cache["mtime"] != mtime:
        with _lock:
            if _cache["mtime"] != mtime:
                if path.suffix == ".gz":
                    with gzip.open(path, "rt", encoding="utf-8") as handle:
                        _cache["dataset"] = json.load(handle)
                else:
                    _cache["dataset"] = json.loads(path.read_text(encoding="utf-8"))
                _cache["mtime"] = mtime
    return _cache["dataset"]


def get_cars() -> list[dict[str, Any]]:
    return load_dataset()["cars"]


def get_meta() -> dict[str, Any]:
    data = load_dataset()
    return {
        "source": data.get("source"),
        "scraped_at": data.get("scraped_at"),
        "count": data.get("count", len(data.get("cars", []))),
        "pages": data.get("pages"),
    }


def get_filter_options(cars: list[dict[str, Any]]) -> dict[str, Any]:
    brands = Counter(c["brand"] for c in cars if c.get("brand"))
    bodies = Counter(c["body_type"] for c in cars if c.get("body_type"))
    fuels = Counter(c["engine_type"] for c in cars if c.get("engine_type"))
    # Model names are only unique inside a brand ("X5" belongs to BMW), so the
    # pair is the key the dashboard's cascading "Модель" select works with.
    models = Counter(
        (c["brand"], c["model"])
        for c in cars
        if c.get("brand") and c.get("model")
    )
    years = [c["year"] for c in cars if c.get("year")]
    prices = [c["price_usd"] for c in cars if c.get("price_usd")]
    return {
        "brands": [{"name": name, "count": n} for name, n in brands.most_common()],
        "models": [
            {"brand": brand, "name": model, "count": n}
            for (brand, model), n in sorted(
                models.items(), key=lambda item: (item[0][0], -item[1], item[0][1])
            )
        ],
        "bodies": [{"name": name, "count": n} for name, n in bodies.most_common()],
        "fuels": [{"name": name, "count": n} for name, n in fuels.most_common()],
        "year_min": min(years) if years else None,
        "year_max": max(years) if years else None,
        "price_min": min(prices) if prices else None,
        "price_max": max(prices) if prices else None,
    }
