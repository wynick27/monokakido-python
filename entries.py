import argparse
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union


HEADER_SIZE = 36          # 0x24: count, rows, columns, reserved[4], labelTableOffset, firstCharacterTableOffset
ENTRY_SIZE = 8            # pageID(u32) + itemID(u16) + dictionaryID(u16)
LABEL_OFFSET_SIZE = 4
FIRST_CHARACTER_SLOT_SIZE = 4


@dataclass
class AppendixEntry:
    page_id: int
    item_id: int
    dictionary_id: int
    label: Optional[str] = None
    first_character: Optional[str] = None

    def entry_id_string(self) -> str:
        # Same formatting as the reference C++ exporter: 6-digit page + 4-hex item.
        return f"{self.page_id:06}-{self.item_id:04X}"


class AppendixEntryList:
    """Monokakido appendix entry list (.entries).

    File structure (see mkd-tools include/MKD/resource/appendix_entry_list.hpp):
      Header (36 bytes):
        u32 count
        u32 rows
        u32 columns
        u32 reserved[4]
        u32 labelTableOffset            (0 if absent)
        u32 firstCharacterTableOffset   (0 if absent)
      Records (count x 8 bytes):
        u32 pageID
        u16 itemID          (often item | (itemType << 8))
        u16 dictionaryID
      Label table (optional):
        count x u32 offsets into NUL-terminated UTF-8 labels
      First-character table (optional):
        count x 4-byte slots; first 2 bytes are a UTF-16 code unit
    """

    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)
        with open(self.path, "rb") as f:
            self.data = f.read()

        if len(self.data) < HEADER_SIZE:
            raise ValueError("File too small for appendix entry list header")

        self.count, self.rows, self.columns = struct.unpack_from("<III", self.data, 0)
        self.label_table_offset = struct.unpack_from("<I", self.data, 28)[0]
        self.first_character_table_offset = struct.unpack_from("<I", self.data, 32)[0]

        entries_end = HEADER_SIZE + self.count * ENTRY_SIZE
        if entries_end > len(self.data):
            raise ValueError("Entry records exceed file size")
        if self.label_table_offset and (
            self.label_table_offset < entries_end
            or self.label_table_offset + self.count * LABEL_OFFSET_SIZE > len(self.data)
        ):
            raise ValueError("Label table offset out of bounds")
        if self.first_character_table_offset and (
            self.first_character_table_offset < entries_end
            or self.first_character_table_offset + self.count * FIRST_CHARACTER_SLOT_SIZE > len(self.data)
        ):
            raise ValueError("First-character table offset out of bounds")

        self.entries: List[AppendixEntry] = []
        for i in range(self.count):
            base = HEADER_SIZE + i * ENTRY_SIZE
            page_id, = struct.unpack_from("<I", self.data, base)
            item_id, = struct.unpack_from("<H", self.data, base + 4)
            dictionary_id, = struct.unpack_from("<H", self.data, base + 6)
            self.entries.append(AppendixEntry(page_id, item_id, dictionary_id))

        if self.label_table_offset:
            for i in range(self.count):
                off, = struct.unpack_from("<I", self.data, self.label_table_offset + i * LABEL_OFFSET_SIZE)
                end = self.data.find(b"\0", off)
                if end == -1:
                    end = len(self.data)
                self.entries[i].label = self.data[off:end].decode("utf-8", errors="replace")

        if self.first_character_table_offset:
            for i in range(self.count):
                code, = struct.unpack_from(
                    "<H", self.data, self.first_character_table_offset + i * FIRST_CHARACTER_SLOT_SIZE
                )
                if code:
                    self.entries[i].first_character = chr(code)

    def __len__(self) -> int:
        return self.count

    def __iter__(self):
        return iter(self.entries)


def load_headline_map(path: Union[str, Path]) -> Dict[str, str]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {str(k): str(v) for k, v in data.items()}


def resolve_headline(headlines: Optional[Dict[str, str]], entry: AppendixEntry) -> str:
    if not headlines:
        return ""

    item = entry.item_id
    sub_item = item & 0xFF
    candidates = (
        f"{entry.page_id:05}-{item:04X}",
        f"{entry.page_id:05}-{sub_item:04X}",
        f"{entry.page_id:05}",
    )
    for key in candidates:
        if key in headlines:
            return headlines[key]
    return ""


def export_tsv(entry_list: AppendixEntryList, headlines: Optional[Dict[str, str]] = None) -> str:
    lines = ["entry_id\tlabel\tfirst_character\theadline"]
    for entry in entry_list:
        lines.append(
            "\t".join(
                (
                    entry.entry_id_string(),
                    entry.label or "",
                    entry.first_character or "",
                    resolve_headline(headlines, entry),
                )
            )
        )
    return "\n".join(lines) + "\n"


def export_json(entry_list: AppendixEntryList, headlines: Optional[Dict[str, str]] = None) -> dict:
    result = {
        "file": entry_list.path.name,
        "count": entry_list.count,
        "rows": entry_list.rows,
        "columns": entry_list.columns,
        "entries": [],
    }
    for entry in entry_list:
        result["entries"].append(
            {
                "page_id": entry.page_id,
                "item_id": entry.item_id,
                "dictionary_id": entry.dictionary_id,
                "entry_id": entry.entry_id_string(),
                "label": entry.label,
                "first_character": entry.first_character,
                "headline": resolve_headline(headlines, entry),
            }
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse Monokakido appendix .entries files into entry lists.")
    parser.add_argument("files", nargs="+", help="Path(s) to .entries files")
    parser.add_argument("--headlines", help="Optional headline_headlines.json used to resolve headline text")
    parser.add_argument("--output-dir", default=".", help="Directory for output files (default: current)")
    parser.add_argument("--format", choices=("tsv", "json"), default="tsv", help="Output format (default: tsv)")
    args = parser.parse_args()

    headlines = load_headline_map(args.headlines) if args.headlines else None
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for file in args.files:
        entry_list = AppendixEntryList(file)
        stem = Path(file).stem
        if args.format == "json":
            payload = export_json(entry_list, headlines)
            out_path = out_dir / f"{stem}.entries.json"
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        else:
            out_path = out_dir / f"{stem}.entries.tsv"
            with open(out_path, "w", encoding="utf-8", newline="") as f:
                f.write(export_tsv(entry_list, headlines))
        print(f"{file}: {len(entry_list)} entries -> {out_path}")


if __name__ == "__main__":
    main()
