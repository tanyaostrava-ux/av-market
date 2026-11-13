"""The GitHub Pages build must stay self-contained and offline."""

from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "data" / "cars.json"
DATASET_GZ = ROOT / "data" / "cars.json.gz"


def dataset_path() -> Path:
    """The committed repository only has the gzipped snapshot (31 MB is over
    GitHub's 25 MB web-upload limit); a local checkout has the plain one."""
    return DATASET if DATASET.exists() else DATASET_GZ


def read_dataset(path: Path) -> dict:
    if path.suffix == ".gz":
        return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def builder():
    spec = importlib.util.spec_from_file_location("build_static", ROOT / "scripts" / "build-static.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_static_produces_self_contained_site(builder, tmp_path):
    out = tmp_path / "docs"
    meta = builder.build_static(dataset_path(), out)

    dataset = read_dataset(dataset_path())

    assert (out / "index.html").is_file()
    assert (out / "styles.css").is_file()
    assert (out / "app.js").is_file()
    assert (out / "analytics.js").is_file()
    assert (out / "vendor" / "echarts.min.js").is_file()
    assert (out / ".nojekyll").is_file()
    assert 'data-mode="static"' in (out / "index.html").read_text(encoding="utf-8")
    # Pages caches assets, so the built page must point at fingerprinted URLs.
    assert "app.js?v=" in (out / "index.html").read_text(encoding="utf-8")

    published = json.loads(
        gzip.decompress((out / "data" / "cars.json.gz").read_bytes()).decode("utf-8")
    )
    assert published["count"] == dataset["count"]
    assert published["scraped_at"] == dataset["scraped_at"]
    assert len(published["cars"]) == len(dataset["cars"])
    # Pages must fit GitHub's 25 MB per-file upload limit, so the plain JSON
    # must not be shipped next to the gzipped copy.
    assert not (out / "data" / "cars.json").exists()

    published_meta = json.loads((out / "data" / "meta.json").read_text(encoding="utf-8"))
    assert published_meta["count"] == dataset["count"]
    assert published_meta["scraped_at"] == dataset["scraped_at"]
    assert published_meta["source"] == dataset["source"]
    assert published_meta["compressed"] is True
    assert meta["built_at"]


def test_build_static_rebuilds_cleanly(builder, tmp_path):
    out = tmp_path / "docs"
    builder.build_static(dataset_path(), out)
    stale = out / "stale.html"
    stale.write_text("old build", encoding="utf-8")

    builder.build_static(dataset_path(), out)

    assert not stale.exists(), "a rebuild must not leave files from the previous build"


def test_build_static_refuses_foreign_directory(builder, tmp_path):
    out = tmp_path / "docs"
    out.mkdir()
    (out / "keep-me.txt").write_text("user file", encoding="utf-8")

    with pytest.raises(RuntimeError):
        builder.build_static(dataset_path(), out)

    assert (out / "keep-me.txt").is_file()


def test_build_static_requires_dataset(builder, tmp_path):
    with pytest.raises(FileNotFoundError):
        builder.build_static(tmp_path / "missing.json", tmp_path / "docs")
