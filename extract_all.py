#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Monokakido dictionary batch extractor.

给定一个目录（单个词典产品，或包含多个词典产品的文件夹），本脚本会：

  * 读取每个产品的 ``Contents/<name>.json`` 元数据，发现其中的子词典
    （``DSProductContents``）以及每个子词典的目录与 dict id
    （``DSContentDirectory`` / ``DSContentIdentifier``）；
  * 对每个子词典，把下面的各部分全部解压到输出目录：

    - ``contents/``  RSC 正文          -> ``contents.json``
    - ``headline/``  headlinestore     -> ``<名>_headlines.json``
                    RSC 标题           -> ``headline.json``
    - ``key/``       keystore          -> ``<名>_keys_forward.json`` /
                                         ``<名>_keys_reverse.json``
    - ``audio/``     NRSC 音频         -> ``audio/`` 下的文件
    - ``images/``    NRSC 图片         -> ``images/`` 下的文件
    - ``fonts/``     RSC 字体          -> ``fonts/<FontName>.ttf|.otf``
    - ``index/``     RSC 索引          -> ``index.json``
    - ``*.entries``  附录条目           -> ``*.entries.tsv`` / ``*.entries.json``

默认输出目录是 ``<输入目录>_extracted``，可用 ``--output-dir`` 覆盖。
"""

import argparse
import base64
import datetime
import json
import plistlib
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from nrsc import NamedResourceStore          # noqa: E402
from rsc import Rsc                           # noqa: E402
from keys import Keys                         # noqa: E402
from headlines import Headlines               # noqa: E402
from entries import (                          # noqa: E402
    AppendixEntryList,
    export_json as entries_to_json,
    export_tsv as entries_to_tsv,
)

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover - only needed for dic-item XML
    BeautifulSoup = None


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #

def sanitize(name: str) -> str:
    """把可能包含路径分隔符的名字转成安全的目录名。"""
    return "".join("_" if c in '/\\:*?"<>|' else c for c in name).strip() or "content"


def write_json(obj, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def has_suffix_files(directory: Path, *suffixes: str) -> bool:
    if not directory.is_dir():
        return False
    for entry in directory.iterdir():
        if entry.is_file() and entry.suffix in suffixes:
            return True
    return False


def close_rsc(rsc) -> None:
    """关闭 Rsc 打开的数据文件句柄。"""
    for rf in getattr(rsc, "files", []):
        try:
            rf.file.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 元数据读取
# --------------------------------------------------------------------------- #

def is_product_dir(path: Path) -> bool:
    return (path / "Contents" / (path.name + ".json")).is_file()


def discover_products(input_dir: Path):
    """返回输入目录里（或其自身）的所有词典产品根目录。"""
    if is_product_dir(input_dir):
        return [input_dir]
    return sorted(
        (p for p in input_dir.iterdir() if p.is_dir() and is_product_dir(p)),
        key=lambda p: p.name.lower(),
    )


def load_product_metadata(product: Path) -> dict:
    meta_path = product / "Contents" / (product.name + ".json")
    with open(meta_path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_plist_dir_map(contents_root: Path) -> dict:
    """扫描 Contents 下各子目录的 DSContentInfo.plist，建立 identifier -> 目录 的映射。"""
    result = {}
    if not contents_root.is_dir():
        return result
    for child in contents_root.iterdir():
        if not child.is_dir():
            continue
        plist_path = child / "DSContentInfo.plist"
        if not plist_path.is_file():
            continue
        try:
            with open(plist_path, "rb") as f:
                info = plistlib.load(f)
            identifier = str(info.get("DSContentIdentifier", "")).strip()
            if identifier:
                result[identifier] = child.name
        except Exception:
            continue
    return result


def resolve_content_dir(contents_root: Path, entry: dict, plist_map: dict) -> str:
    """确定某个子词典真实的内容目录。

    优先使用 JSON 里的 ``DSContentDirectory``，若该目录下的 DSContentInfo.plist
    声明的 identifier 与 JSON 一致则直接采用；否则按 identifier 到各子目录的
    DSContentInfo.plist 里查找（可修正某些产品 JSON 里目录名过期的问题）。
    """
    identifier = str(entry.get("DSContentIdentifier", "")).strip()
    directory = str(entry.get("DSContentDirectory", "")).strip()

    candidate = contents_root / directory
    if candidate.is_dir():
        plist_path = candidate / "DSContentInfo.plist"
        if plist_path.is_file():
            try:
                with open(plist_path, "rb") as f:
                    info = plistlib.load(f)
                if str(info.get("DSContentIdentifier", "")).strip() == identifier:
                    return directory
            except Exception:
                pass
        else:
            # 没有 plist 可以反驳，相信 JSON。
            return directory

    if identifier and identifier in plist_map:
        return plist_map[identifier]
    return directory


# --------------------------------------------------------------------------- #
# RSC 正文/数据解码
# --------------------------------------------------------------------------- #

def smart_decode(data: bytes) -> str:
    """尽量把 RSC 里的文本载荷按正确编码解出来（UTF-16LE / UTF-8）。"""
    if data.startswith(b"\xff\xfe"):
        return data[2:].decode("utf-16le", errors="replace")
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16be", errors="replace")
    # UTF-16LE 里 ASCII 字符的高字节落在奇数下标上且为 0，据此判断编码。
    odd = data[1::2]
    if len(odd) >= 4 and odd.count(0) * 2 >= len(odd):
        try:
            return data.decode("utf-16le")
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace")


def _jsonable(obj):
    """把 plistlib 解析出的对象转成 JSON 可序列化的结构。"""
    if isinstance(obj, plistlib.UID):
        return int(obj.data)
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, bytes):
        return {"__base64__": base64.b64encode(obj).decode("ascii")}
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    return obj


def extract_contents_rsc(rsc, out_path: Path):
    """把 RSC 正文解压成 JSON（复刻 rsc.py 的逻辑，键为 dic-item id 或条目 id）。"""
    result = {}
    skipped = 0
    for i in range(len(rsc)):
        try:
            item_id, data = rsc.get_by_idx(i)
            text = data.decode("utf-8", errors="replace")
            key = str(item_id)
            stripped = text.strip()
            if stripped.startswith("<"):
                # 仅在真正含 <dic-item> 时才做 XML 解析，避免无谓开销。
                if "dic-item" in text and BeautifulSoup is not None:
                    soup = BeautifulSoup(text, "html.parser")
                    dic_item = soup.select_one("dic-item")
                    if dic_item is not None:
                        key = dic_item.attrs.get("id", str(item_id))
            result[key] = text
        except Exception:
            skipped += 1
    if result:
        write_json(result, out_path)
    return len(result), skipped


def extract_data_rsc(rsc, out_path: Path, parse_plist: bool = False, encoding: str = None):
    """把普通 RSC 数据（标题/索引等）解压成 JSON，键为条目 id。"""
    result = {}
    skipped = 0
    for i in range(len(rsc)):
        try:
            item_id, data = rsc.get_by_idx(i)
            if parse_plist and data.startswith(b"bplist"):
                value = _jsonable(plistlib.loads(data))
            elif encoding:
                value = data.decode(encoding, errors="replace")
            else:
                value = smart_decode(data)
            result[str(item_id)] = value
        except Exception:
            skipped += 1
    if result:
        write_json(result, out_path)
    return len(result), skipped


# --------------------------------------------------------------------------- #
# keystore / headlinestore / NRSC / 字体 / 附录
# --------------------------------------------------------------------------- #

def extract_keystore(ks_path: Path, dict_id: str, out_dir: Path):
    stem = ks_path.stem
    k = Keys(str(ks_path), dict_id or None)
    num = len(k)
    if num == 0:
        return f"{ks_path.name}: 无 prefix 索引，跳过"

    forward = {}
    reverse = {}
    for i in range(num):
        key, entries = k.get_by_index(1, i)
        fset = forward.setdefault(key, set())
        for page, item in entries:
            eid = f"{page:05}" + ("" if item == 0 else f"-{item:04X}")
            fset.add(eid)
            reverse.setdefault(eid, set()).add(key)

    forward = {kk: sorted(vv) for kk, vv in sorted(forward.items())}
    reverse = {kk: sorted(vv) for kk, vv in sorted(reverse.items())}
    write_json(forward, out_dir / f"{stem}_keys_forward.json")
    write_json(reverse, out_dir / f"{stem}_keys_reverse.json")
    return f"{ks_path.name}: {len(forward)} 词条 / {len(reverse)} 条目"


def extract_headlinestore(hs_path: Path, out_dir: Path):
    """解出 headlinestore，返回 (报告, 用于条目反查的标题映射)。

    标题映射的键统一用大写十六进制（与 entries.py 的 resolve_headline 及
    条目 id 的 ``{:04X}`` 格式一致），item_id 为 0 时只保留 ``page_id``。
    """
    stem = hs_path.stem
    hl = Headlines(str(hs_path))
    out_map = {}
    for i in range(len(hl)):
        page_id, item_id, text = hl.get_by_index(i)
        key = f"{page_id:05}" if item_id == 0 else f"{page_id:05}-{item_id:04X}"
        out_map[key] = text
    write_json(out_map, out_dir / f"{stem}_headlines.json")
    return f"{hs_path.name}: {len(out_map)} 标题", out_map


_MEDIA_MAGIC = (
    (b"\x89PNG", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF8", ".gif"),
    (b"RIFF", None),  # 下面再细分 webp
    (b"\x00\x00\x01\x00", ".ico"),
)


def guess_media_ext(data: bytes) -> str:
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    for magic, ext in _MEDIA_MAGIC:
        if ext and data.startswith(magic):
            return ext
    if data[4:8] == b"ftyp":
        return ".m4a"
    if data[:2] == b"ID3" or data[:3] == b"MP+":
        return ".mp3"
    if data[0] == 0xFF and (data[1] & 0xF6) == 0xF0:
        return ".aac"
    return ".aac"  # 无扩展名的音频统一按 aac 处理


def extract_nrsc(src_dir: Path, out_dir: Path):
    nrsc = NamedResourceStore(str(src_dir))
    count = 0
    for name, record in nrsc.entries():
        try:
            data = nrsc.get_data(record)
        except Exception as e:
            print(f"    [warn] {name}: {e}")
            continue
        fname = name if "." in name else name + guess_media_ext(data)
        out_path = out_dir / fname
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)
        count += 1
    return f"{src_dir.name}: {count} 个文件"


_FONT_MAGIC = (
    (b"OTTO", ".otf"),
    (b"\x00\x01\x00\x00", ".ttf"),
    (b"true", ".ttf"),
    (b"ttcf", ".ttc"),
    (b"wOFF", ".woff"),
    (b"wOF2", ".woff2"),
)


def guess_font_ext(data: bytes) -> str:
    for magic, ext in _FONT_MAGIC:
        if data.startswith(magic):
            return ext
    return ".bin"


def extract_font(font_dir: Path, out_dir: Path):
    rsc = Rsc.new(str(font_dir), "font")
    try:
        buf = bytearray()
        for i in range(len(rsc)):
            try:
                _, data = rsc.get_by_idx(i)
                if data:
                    buf.extend(data)
            except Exception:
                continue
    finally:
        close_rsc(rsc)
    if not buf:
        return f"{font_dir.name}: 空字体"
    ext = guess_font_ext(bytes(buf))
    out_path = out_dir / f"{font_dir.name}{ext}"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bytes(buf))
    return f"{font_dir.name}: {len(buf)} 字节 -> {out_path.name}"


def extract_entries(entries_files, base: Path, out_dir: Path, headlines: dict = None):
    summary = []
    for ef in sorted(entries_files):
        rel = ef.relative_to(base)
        try:
            entry_list = AppendixEntryList(str(ef))
        except Exception as e:
            summary.append(f"{rel}: 读取失败 {e}")
            continue
        tsv_path = out_dir / (str(rel) + ".tsv")
        json_path = out_dir / (str(rel) + ".json")
        tsv_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(tsv_path, "w", encoding="utf-8", newline="") as f:
            f.write(entries_to_tsv(entry_list, headlines))
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(entries_to_json(entry_list, headlines), f, ensure_ascii=False, indent=2)
        summary.append(f"{rel}: {len(entry_list)} 条目")
    return "; ".join(summary) if summary else None


# --------------------------------------------------------------------------- #
# 单个子词典
# --------------------------------------------------------------------------- #

def process_content(product: Path, entry: dict, plist_map: dict, output_root: Path):
    contents_root = product / "Contents"
    directory = resolve_content_dir(contents_root, entry, plist_map)
    identifier = str(entry.get("DSContentIdentifier", "")).strip()
    base = contents_root / directory
    out_dir = output_root / product.name / sanitize(identifier)

    title = ""
    try:
        title = entry.get("DSContentTitle", {}).get("ja") or entry.get("DSContentTitle", {}).get("en") or ""
    except Exception:
        title = ""

    print(f"\n=== {product.name} / {identifier}  ({title})  ->  {out_dir} ===")
    if not base.is_dir():
        print(f"  [warn] 内容目录不存在: {base}")
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    reports = []

    # 1) contents/ RSC 正文
    contents_dir = base / "contents"
    if has_suffix_files(contents_dir, ".rsc") and has_suffix_files(contents_dir, ".map"):
        try:
            rsc = Rsc.new(str(contents_dir), "contents", identifier or None)
            try:
                n, skipped = extract_contents_rsc(rsc, out_dir / "contents.json")
                reports.append(f"contents: {n} 条目 (跳过 {skipped})")
            finally:
                close_rsc(rsc)
        except Exception as e:
            reports.append(f"contents: 失败 {e}")
            print(f"  [error] contents: {e}")

    # 2) headline/
    headline_dir = base / "headline"
    headline_map = {}
    if headline_dir.is_dir():
        hs_files = sorted(headline_dir.glob("*.headlinestore"))
        if hs_files:
            for hs in hs_files:
                try:
                    report, hmap = extract_headlinestore(hs, out_dir)
                    reports.append(report)
                    # 多个 headlinestore 合并时，先出现者优先（与参考实现一致）。
                    for k, v in hmap.items():
                        headline_map.setdefault(k, v)
                except Exception as e:
                    reports.append(f"{hs.name}: 失败 {e}")
                    print(f"  [error] {hs.name}: {e}")
        elif has_suffix_files(headline_dir, ".rsc") and has_suffix_files(headline_dir, ".map"):
            try:
                rsc = Rsc.new(str(headline_dir), "headline", identifier or None)
                try:
                    n, skipped = extract_data_rsc(rsc, out_dir / "headline.json", parse_plist=False, encoding="utf-16le")
                    reports.append(f"headline(RSC): {n} 条目 (跳过 {skipped})")
                finally:
                    close_rsc(rsc)
            except Exception as e:
                reports.append(f"headline(RSC): 失败 {e}")
                print(f"  [error] headline(RSC): {e}")

    # 3) key/*.keystore
    key_dir = base / "key"
    if key_dir.is_dir():
        for ks in sorted(key_dir.glob("*.keystore")):
            try:
                reports.append(extract_keystore(ks, identifier, out_dir))
            except Exception as e:
                reports.append(f"{ks.name}: 失败 {e}")
                print(f"  [error] {ks.name}: {e}")

    # 4) audio/ (NRSC，或旧式 RSC)
    audio_dir = base / "audio"
    if audio_dir.is_dir():
        if has_suffix_files(audio_dir, ".nidx", ".nrsc"):
            try:
                reports.append(extract_nrsc(audio_dir, out_dir / "audio"))
            except Exception as e:
                reports.append(f"audio: 失败 {e}")
                print(f"  [error] audio: {e}")
        elif has_suffix_files(audio_dir, ".rsc") and has_suffix_files(audio_dir, ".map"):
            try:
                rsc = Rsc.new(str(audio_dir), "audio", identifier or None)
                try:
                    extract_nrsc_like_rsc(rsc, out_dir / "audio")
                    reports.append(f"audio(RSC): {len(rsc)} 文件")
                finally:
                    close_rsc(rsc)
            except Exception as e:
                reports.append(f"audio(RSC): 失败 {e}")
                print(f"  [error] audio(RSC): {e}")

    # 5) images/ / graphics/ (NRSC)
    for img_name in ("images", "graphics", "img"):
        img_dir = base / img_name
        if img_dir.is_dir() and has_suffix_files(img_dir, ".nidx", ".nrsc"):
            try:
                reports.append(extract_nrsc(img_dir, out_dir / "images"))
            except Exception as e:
                reports.append(f"{img_name}: 失败 {e}")
                print(f"  [error] {img_name}: {e}")
            break

    # 6) fonts/
    fonts_dir = base / "fonts"
    if fonts_dir.is_dir():
        for font_dir in sorted(fonts_dir.iterdir()):
            if font_dir.is_dir() and has_suffix_files(font_dir, ".rsc"):
                try:
                    reports.append(extract_font(font_dir, out_dir / "fonts"))
                except Exception as e:
                    reports.append(f"{font_dir.name}: 失败 {e}")
                    print(f"  [error] font {font_dir.name}: {e}")

    # 7) index/ (RSC，例如 RUIGO)
    index_dir = base / "index"
    if index_dir.is_dir() and has_suffix_files(index_dir, ".rsc") and has_suffix_files(index_dir, ".map"):
        try:
            rsc = Rsc.new(str(index_dir), "index", identifier or None)
            try:
                n, skipped = extract_data_rsc(rsc, out_dir / "index.json", parse_plist=True)
                reports.append(f"index: {n} 条目 (跳过 {skipped})")
            finally:
                close_rsc(rsc)
        except Exception as e:
            reports.append(f"index: 失败 {e}")
            print(f"  [error] index: {e}")

    # 8) *.entries 附录
    entries_files = [p for p in base.rglob("*.entries") if p.is_file()]
    if entries_files:
        try:
            s = extract_entries(entries_files, base, out_dir, headline_map or None)
            if s:
                reports.append(s)
        except Exception as e:
            reports.append(f"entries: 失败 {e}")
            print(f"  [error] entries: {e}")

    print("  " + "\n  ".join(reports))


def extract_nrsc_like_rsc(rsc, out_dir: Path):
    """把旧式 RSC 音频按序号写成文件。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    for i in range(len(rsc)):
        try:
            item_id, data = rsc.get_by_idx(i)
            if not data:
                continue
            fname = f"{item_id:06}{guess_media_ext(data)}"
            (out_dir / fname).write_bytes(data)
        except Exception:
            continue


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(
        description="批量解压 Monokakido 词典的各部分（nrsc/rsc/keystore/headlinestore/entries）。"
    )
    parser.add_argument("input", help="输入目录：词典产品目录，或包含多个产品目录的文件夹")
    parser.add_argument("-o", "--output-dir", help="输出目录（默认：<输入目录>_extracted）")
    parser.add_argument("--list", action="store_true", help="只列出发现的词典与子词典，不解压")
    args = parser.parse_args()

    input_dir = Path(args.input).expanduser()
    if not input_dir.is_dir():
        print(f"错误：输入目录不存在：{input_dir}")
        sys.exit(1)

    output_root = Path(args.output_dir) if args.output_dir else input_dir.parent / (input_dir.name + "_extracted")

    products = discover_products(input_dir)
    if not products:
        print(f"在 {input_dir} 中没有找到任何词典产品（缺 Contents/<name>.json）。")
        sys.exit(1)

    print(f"发现 {len(products)} 个词典产品，输出目录：{output_root}\n")

    total_sub = 0
    for product in products:
        try:
            metadata = load_product_metadata(product)
        except Exception as e:
            print(f"[error] 读取 {product.name} 元数据失败：{e}")
            continue

        contents_root = product / "Contents"
        plist_map = build_plist_dir_map(contents_root)
        entries = metadata.get("DSProductContents", [])
        if not entries:
            print(f"[warn] {product.name} 的 JSON 里没有 DSProductContents")
            continue

        print(f"■ {product.name}：{len(entries)} 个子词典")
        for entry in entries:
            identifier = str(entry.get("DSContentIdentifier", "")).strip()
            directory = resolve_content_dir(contents_root, entry, plist_map)
            title = ""
            try:
                t = entry.get("DSContentTitle") or {}
                title = t.get("ja") or t.get("en") or ""
            except Exception:
                title = ""
            print(f"    - {identifier}  (目录 {directory})  {title}")
            total_sub += 1

        if not args.list:
            for entry in entries:
                try:
                    process_content(product, entry, plist_map, output_root)
                except Exception as e:
                    print(f"[error] 处理 {product.name}/{entry.get('DSContentIdentifier')} 失败：{e}")

    print(f"\n完成。共 {len(products)} 个产品、{total_sub} 个子词典，输出到 {output_root}")

    if args.list:
        print("\n（--list 模式，未执行解压。）")


if __name__ == "__main__":
    main()
