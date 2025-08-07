import struct
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional, BinaryIO

#from keys import PageItemId


@dataclass
class FileHeader:
    magic1: int
    magic2: int
    length: int
    rec_offset: int
    words_offset: int
    rec_bytes: int
    magic4: int
    magic5: int

    @classmethod
    def from_file(cls, f: BinaryIO) -> 'FileHeader':
        data = f.read(32)
        if len(data) < 32:
            raise Error("File too short")
        unpacked = struct.unpack('<IIIIIIII', data)
        return cls(*unpacked)

    def validate(self):
        if (
            self.magic1 == 0 and
            self.magic2 == 0x2 and
            self.rec_bytes == 0x18 and
            self.magic4 == 0 and
            self.magic5 == 0
        ):
            return
        raise Exception("KeyFile header is invalid.")


@dataclass
class Offset:
    page_id: int
    item_id: int
    item_type: int
    offset: int

    @classmethod
    def from_bytes(cls, data: bytes) -> 'Offset':
        if len(data) != 24:
            raise Exception("Invalid offset entry size")
        page_id, item_id, item_type, magic1, offset, magic2, magic3, magic4 = struct.unpack('<IBBHIIII', data)
        if magic1 != 0 or magic2 != 0 or magic3 != 0 or magic4 != 0:
            pass
        return cls(page_id, item_id, item_type, offset)


class Headlines:
    def __init__(self, recs: List[Offset], words: bytes):
        self.recs = recs
        self.words = words

    @classmethod
    def from_path(cls, file_path: str) -> 'Headlines':
        with open(file_path, 'rb') as f:
            f.seek(0)
            hdr = FileHeader.from_file(f)
            hdr.validate()

            # Read record offsets
            rec_size = hdr.words_offset - hdr.rec_offset
            f.seek(hdr.rec_offset)
            rec_data = f.read(rec_size)
            if len(rec_data) != rec_size:
                raise Exception("Failed to read offsets")

            recs = []
            for i in range(0, rec_size, 24):
                recs.append(Offset.from_bytes(rec_data[i:i + 24]))

            # Read words data
            f.seek(hdr.words_offset)
            words_data = f.read()
            if not words_data:
                raise Exception("No words section")
            return cls(recs, words_data)
        
    def get_word(self, rec:Offset) -> Optional[str]:
        offset = rec.offset
        # Read null-terminated string from words section
        end = self.words.find(b'\0\0', offset)
        if end == -1:
            raise Exception("No null terminator")
        if end % 2 == 1:
            end += 1
        return self.words[offset:end].decode('utf-16')

    def get(self, id) -> str:
        # Binary search
        low, high = 0, len(self.recs) - 1
        while low <= high:
            mid = (low + high) // 2
            rec = self.recs[mid]
            if rec.page_id == id.page and rec.item_id == id.item:
                return self.get_word(rec)
            elif (rec.page_id, rec.item_id) < (id.page, id.item):
                low = mid + 1
            else:
                high = mid - 1
        raise Exception("InvalidIndex")

if __name__ == "__main__":
    import json
    
    for file in ['headline','short-headline']:
        headline_path = f".\\NHKACCENT2\\Contents\\NHK_ACCENT\\headline\\{file}.headlinestore"
        headlines = Headlines.from_path(headline_path)
        out_map = {}

        for rec in headlines.recs:
            page = f"{rec.page_id:05}{'' if rec.item_id==0 else '-' +  '{:04X}'.format(rec.item_id)}"
            text = headlines.get_word(rec)
            out_map[page] = text
        with open(f"{file}_headlines.json", 'w', encoding='utf-8') as f:
            json.dump(out_map, f, ensure_ascii=False, indent=2)
