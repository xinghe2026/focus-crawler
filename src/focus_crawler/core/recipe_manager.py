"""配方库：把配置 + 脚本 + fixtures + README 打包为 zip，可导入。"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

RECIPE_FILES = ("config.json", "list_parser.py", "detail_parser.py", "README.md")


def export_recipe(recipe_dir: str | Path, output_zip: str | Path) -> Path:
    source = Path(recipe_dir)
    if not source.exists():
        raise FileNotFoundError(f"配方目录不存在: {source}")
    target = Path(output_zip)
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(source)
            archive.write(path, str(Path(source.name) / relative))
    return target


def import_recipe(zip_path: str | Path, dest_dir: str | Path) -> Path:
    archive_path = Path(zip_path)
    if not archive_path.exists():
        raise FileNotFoundError(f"配方压缩包不存在: {archive_path}")
    destination = Path(dest_dir)
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        for name in names:
            if Path(name).is_absolute() or ".." in Path(name).parts:
                raise ValueError(f"配方包含非法路径: {name}")
        archive.extractall(destination)
    top_levels = {Path(name).parts[0] for name in names if name.strip()}
    if len(top_levels) == 1:
        return destination / next(iter(top_levels))
    return destination


def copy_recipe(recipe_dir: str | Path, dest_dir: str | Path) -> Path:
    source = Path(recipe_dir)
    target = Path(dest_dir) / source.name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    return target
