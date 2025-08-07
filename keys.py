import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Tuple, Union, Iterator
from io import BufferedReader
from enum import Enum
import json


def read_u32(f: BufferedReader) -> int:
    return struct.unpack('<I', f.read(4))[0]


def read_vec(f: BufferedReader, start: int) -> Optional[List[int]]:
    f.seek(start)
    raw = f.read(4)
    count = struct.unpack('<I', raw)[0]
    expected_len = 4 * count
    raw = f.read(4 * count)
    if len(raw) < expected_len:
        return None
    return list(struct.unpack('<' +  str(count)+ 'I' , raw))


@dataclass
class FileHeader:
    ver: int
    magic1: int
    words_offset: int
    idx_offset: int
    next_offset: int
    magic5: int
    magic6: int
    magic7: int

    @classmethod
    def from_file(cls, f: BufferedReader) -> 'FileHeader':
        raw = f.read(0x10)
        ver, magic1, words_offset, idx_offset = struct.unpack('<4I', raw)
        if ver == 0x10000 and words_offset == 0x10:
            extra = b'\x00' * 12
        elif ver == 0x20000 and words_offset == 0x20:
            extra = f.read(12)
        else:
            raise Exception("Key header is invalid.")

        next_offset, magic5, magic6 = struct.unpack('<3I', extra[:12])
        magic7 = 0

        if ver == 0x10000 and magic1 == 0 and words_offset < idx_offset:
            return cls(ver, magic1, words_offset, idx_offset, 0, 0, 0, 0)
        elif ver == 0x20000 and magic1 == 0 and magic5 == 0 and magic6 == 0 and words_offset < idx_offset and (next_offset == 0 or idx_offset < next_offset):
            magic7 = 0
            return cls(ver, magic1, words_offset, idx_offset, next_offset, magic5, magic6, magic7)
        else:
            raise Exception("Key header is invalid.")


@dataclass
class IndexHeader:
    magic: int
    index_a_offset: int
    index_b_offset: int
    index_c_offset: int
    index_d_offset: int

    @classmethod
    def from_file(cls, f: BufferedReader) -> 'IndexHeader':
        data = f.read(20)
        values = list(struct.unpack('<5I', data))
        return cls(*values)

    def validate(self, idx_end: int):
        a, b, c, d = self.index_a_offset, self.index_b_offset, self.index_c_offset, self.index_d_offset
        def check(l, r): return l < r or r == 0
        if self.magic == 0x04 and check(a, b) and check(b, c) and check(c, d) and check(d, idx_end):
            return
        raise Exception("Key index header is invalid.")



class Keys:
    def __init__(self, path: Union[str, Path]):
        with open(path, 'rb') as f:
            file_size = Path(path).stat().st_size
            hdr = FileHeader.from_file(f)

            f.seek(hdr.words_offset)
            word_index = read_vec(f, hdr.words_offset)
            
            self.offset_delta = hdr.words_offset -f.tell()
            self.word_index = word_index

            idx_end = hdr.next_offset if hdr.next_offset != 0 else file_size
            self.word_data = f.read(hdr.idx_offset - f.tell())

            f.seek(hdr.idx_offset)
            ihdr = IndexHeader.from_file(f)
            ihdr.validate(idx_end)


            self.index_len = read_vec(f,ihdr.index_a_offset + hdr.idx_offset) if ihdr.index_a_offset else []
            self.index_prefix = read_vec(f,ihdr.index_b_offset + hdr.idx_offset) if ihdr.index_b_offset else []
            self.index_suffix = read_vec(f,ihdr.index_c_offset + hdr.idx_offset) if ihdr.index_c_offset else []
            self.index_unordered = read_vec(f,ihdr.index_d_offset + hdr.idx_offset) if ihdr.index_d_offset else []

    def __len__(self):
        return len(self.word_index)
    
    def __iter__(self):
        for offset in self.word_index:
            yield self.get_word_span(offset)

    def get_word_span(self, offset: int) -> Tuple[str, int]:
        
        pages_offset = struct.unpack_from('<I', self.word_data, offset + self.offset_delta)[0]
        word_start = offset + self.offset_delta + 5
        word_end = self.word_data.find(b'\0',word_start)
        word_bytes = self.word_data[word_start:word_end]
        return word_bytes.decode('utf-8'), pages_offset

    def get_page_iter(self, pages_offset: int):
        offset = pages_offset + self.offset_delta
        count = struct.unpack_from('<H', self.word_data, offset)[0]
        offset += 2
        for i in range(count):
            kind = self.word_data[offset]

            has_item = kind >> 4
            kind = kind & 0xf
            if kind == 1:
                hi = self.word_data[offset + 1]
                offset += 2
                page = (hi)
                item = 0
            elif kind == 2:
                hi, lo = self.word_data[offset + 1:offset + 3]
                offset += 3
                page = (hi << 8) | lo
                item = 0
            elif kind == 4:
                hi, mid, lo = self.word_data[offset + 1:offset + 4]
                offset += 4
                page = (hi << 16) | (mid << 8) | lo
                item = 0
            if has_item == 1:
                item = self.word_data[offset]
                offset += 1
            elif has_item == 2:
                item = (self.word_data[offset] << 8) + self.word_data[offset+1]
                offset += 2
            elif has_item != 0:
                print('Unknown item structure')
            yield (page, item)





    def cmp_key(self, target: str, idx: int) -> int:
        offset = self.index_prefix.get(idx)
        raw = self.words #struct.pack('<' + 'I' * len(self.words), *self.words)
        if offset + len(target) + 1 > len(raw):
            raise IndexError()
        found_tail = raw[offset:]
        found = found_tail[:len(target)]
        if found == target.encode():
            return 0 if found_tail[len(target)] == 0 else 1
        return (found > target.encode()) - (found < target.encode())

    def get_idx(self, index: List[int], idx: int) -> Tuple[str, 'PageIter']:
        if idx >= index.len():
            return None
        offset = index.get(idx)
        word, pages_offset = self.get_word_span(offset)
        return word, self.get_page_iter(pages_offset)

    def search_exact(self, target_key: str) -> Tuple[int, 'PageIter']:
        target_key = to_katakana(target_key)
        low = 0
        high = self.index_prefix.len()

        while low <= high:
            mid = (low + high) // 2
            cmp = self.cmp_key(target_key, mid)
            if cmp < 0:
                low = mid + 1
            elif cmp > 0:
                high = mid - 1
            else:
                return mid, self.get_idx(self.index_prefix, mid)[1]

        return None

def to_katakana(input_str: str) -> str:
    diff = ord('ア') - ord('あ')
    result = []
    converted = False
    for ch in input_str:
        if 'ぁ' <= ch <= 'ん':
            result.append(chr(ord(ch) + diff))
            converted = True
        else:
            result.append(ch)
    return ''.join(result) if converted else input_str

if __name__ == "__main__":

    index_map = {}
    index_prefix = {}
    for name in ['numeral', 'compound','headword']:
        keys = Keys(f'.\\NHKACCENT2\\Contents\\NHK_ACCENT\\key\\{name}.keystore')

        for offset in keys.index_prefix:
            word, page_offset = keys.get_word_span(offset)
            pages = list(f"{page:05}{'' if item==0 else '-' +  '{:04X}'.format(item)}"
                            for page,item in keys.get_page_iter(page_offset))
            if word in index_prefix:
                for page in pages:
                    if not page in index_prefix[word]:
                        print(f"Warning: Duplicate page '{page}' found for word '{word}' in index prefix.")
                        index_prefix[word].append(page)
            else:
                index_prefix[word] = pages
        
        for word, page_offset in keys:
            pages = list(keys.get_page_iter(page_offset))
            for page_id, item in pages:
                
                if (page_id,item) in index_map:
                    index_map[(page_id,item)].append(word)
                else:
                    index_map[(page_id,item)]=[word]
    with open('index.json','w',encoding='utf-8') as f:
        json.dump(index_prefix, f, ensure_ascii=False, indent=2)
    reverse_map = {}
    for page_id, item in sorted(index_map.keys()):
        page = f"{page_id:05}{'' if item==0 else '-' + format(item,'04X')}"
        reverse_map[page] = list(sorted(index_map[(page_id, item)]))
    with open('index_reverse.json','w',encoding='utf-8') as f:
        json.dump(reverse_map, f, ensure_ascii=False, indent=2)
