"""The store must read the gzipped snapshot too.

The 50% build ships ``data/cars.json.gz`` (the plain JSON is above GitHub's
25 MB web-upload limit and stays out of the repository), so the API has to work
when only the gzipped file is present.
"""

from __future__ import annotations

import gzip
import json

import pytest

from app import store

SNAPSHOT = {
    "source": "cars.av.by",
    "scraped_at": "2026-11-13T10:00:00+00:00",
    "pages": 1,
    "count": 2,
    "cars": [
        {"id": 1, "brand": "BMW", "model": "X5", "price_usd": 20000},
        {"id": 2, "brand": "Audi", "model": "A4", "price_usd": 15000},
    ],
}


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    plain = tmp_path / "cars.json"
    gz = tmp_path / "cars.json.gz"
    monkeypatch.setattr(store, "DATA_FILE", plain)
    monkeypatch.setattr(store, "DATA_GZ", gz)
    monkeypatch.setattr(store, "_cache", {"mtime": None, "dataset": None})
    return tmp_path


def test_store_falls_back_to_gzip(data_dir):
    (data_dir / "cars.json.gz").write_bytes(
        gzip.compress(json.dumps(SNAPSHOT, ensure_ascii=False).encode("utf-8"))
    )

    assert store.dataset_exists()
    assert store.get_cars()[0]["brand"] == "BMW"
    assert store.get_meta()["count"] == 2


def test_store_prefers_the_plain_snapshot(data_dir):
    (data_dir / "cars.json.gz").write_bytes(
        gzip.compress(json.dumps(SNAPSHOT, ensure_ascii=False).encode("utf-8"))
    )
    newer = dict(SNAPSHOT, count=1, cars=[{"id": 3, "brand": "Kia", "price_usd": 9000}])
    (data_dir / "cars.json").write_text(json.dumps(newer, ensure_ascii=False), encoding="utf-8")

    assert store.get_cars() == [{"id": 3, "brand": "Kia", "price_usd": 9000}]


def test_store_reports_a_missing_dataset(data_dir):
    assert not store.dataset_exists()
    with pytest.raises(FileNotFoundError):
        store.load_dataset()
