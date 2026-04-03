import struct
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Tuple, Union, Iterator
import json

@dataclass
class EntryId:
    page: int
    item: int
    extra: int = 0
    type: int = 0
    has_type: bool = False

def decode_keystore_entry(data: bytes, offset: int) -> Tuple[EntryId, int]:
    if offset >= len(data):
        raise ValueError("Truncated entry data")
    
    flags = data[offset]
    offset += 1
    entry = EntryId(page=0, item=0)
    
    # Page
    if flags & 0x01:
        entry.page = data[offset]
        offset += 1
    elif flags & 0x02:
        entry.page = struct.unpack('>H', data[offset:offset+2])[0]
        offset += 2
    elif flags & 0x04:
        # 3-byte big-endian
        entry.page = (data[offset] << 16) | (data[offset+1] << 8) | data[offset+2]
        offset += 3
    
    # Item
    if flags & 0x10:
        entry.item = data[offset]
        offset += 1
    elif flags & 0x20:
        entry.item = struct.unpack('>H', data[offset:offset+2])[0]
        offset += 2
        
    # Extra
    if flags & 0x40:
        entry.extra = data[offset]
        offset += 1
    elif flags & 0x80:
        entry.extra = struct.unpack('>H', data[offset:offset+2])[0]
        offset += 2
        
    # Type
    if flags & 0x08:
        entry.type = data[offset]
        offset += 1
        entry.has_type = True
        
    return entry, offset

def decode_entry_ids(data: bytes, wide_count: bool) -> List[EntryId]:
    if wide_count:
        if len(data) < 4: raise ValueError("Truncated count")
        count = struct.unpack('<I', data[:4])[0]
        offset = 4
    else:
        if len(data) < 2: raise ValueError("Truncated count")
        count = struct.unpack('<H', data[:2])[0]
        offset = 2
        
    entries = []
    for _ in range(count):
        entry, offset = decode_keystore_entry(data, offset)
        entries.append(entry)
    return entries

@dataclass
class ConversionEntry:
    page: int
    item: int

class Keys:
    def __init__(self, path: Union[str, Path], dict_id: Optional[str] = None):
        self.path = Path(path)
        self.dict_id = dict_id
        with open(self.path, 'rb') as f:
            self.data = f.read()
            
        self._parse_header()
        self._parse_indices()
        self._parse_conv_table()

    def _parse_header(self):
        if len(self.data) < 16:
            raise Exception("File too small for header")
        
        self.version, self.magic1, self.words_offset, self.index_offset = struct.unpack('<4I', self.data[:16])
        
        if self.version not in (0x10000, 0x20000):
            raise Exception(f"Invalid keystore version: 0x{self.version:x}")
            
        self.conv_table_offset = 0
        if self.version == 0x20000:
            if len(self.data) < 32:
                raise Exception("File too small for V2 header")
            self.conv_table_offset, m5, m6, m7 = struct.unpack('<4I', self.data[16:32])

    def _parse_indices(self):
        idx_base = self.index_offset
        if len(self.data) < idx_base + 20:
            raise Exception("Index section header truncated")
            
        magic, oA, oB, oC, oD = struct.unpack('<5I', self.data[idx_base:idx_base+20])
        if magic != 0x04:
            raise Exception(f"Invalid index magic: 0x{magic:x}")
            
        index_end = self.conv_table_offset if self.conv_table_offset != 0 else len(self.data)
        section_size = index_end - idx_base
        
        offsets = [oA, oB, oC, oD, section_size]
        self.indices = []
        
        for i in range(4):
            start = offsets[i]
            if start == 0:
                self.indices.append([])
                continue
                
            # Find next non-zero offset
            end = section_size
            for j in range(i + 1, 5):
                if offsets[j] != 0:
                    end = offsets[j]
                    break
            
            if end <= start:
                self.indices.append([])
                continue
                
            idx_data = self.data[idx_base + start : idx_base + end]
            count = struct.unpack('<I', idx_data[:4])[0]
            # Each index entry is 4 bytes
            offsets_list = struct.unpack(f'<{count}I', idx_data[4:4 + count*4])
            self.indices.append(list(offsets_list))

    def _parse_conv_table(self):
        self.conv_table = []
        if self.conv_table_offset == 0:
            return
            
        if not (self.dict_id in ("KNEJ.EJ", "KNJE.JE")):
            return
            
        count = struct.unpack('<I', self.data[self.conv_table_offset : self.conv_table_offset + 4])[0]
        entry_size = 8
        base = self.conv_table_offset + 4
        for i in range(count):
            page, item, padding = struct.unpack('<IHH', self.data[base + i*entry_size : base + (i+1)*entry_size])
            self.conv_table.append(ConversionEntry(page, item))

    def __len__(self):
        # Return size of the primary index (Prefix/B)
        return len(self.indices[1])

    def get_word_entry(self, offset: int) -> Tuple[str, int, int]:
        abs_off = self.words_offset + offset
        pages_offset = struct.unpack('<I', self.data[abs_off : abs_off + 4])[0]
        flags = self.data[abs_off + 4]
        
        str_begin = abs_off + 5
        null_pos = self.data.find(b'\0', str_begin)
        if null_pos == -1:
            raise Exception("Unterminated word string")
            
        key = self.data[str_begin:null_pos].decode('utf-8')
        return key, pages_offset, flags

    def get_entry_ids(self, pages_offset: int, flags: int) -> List[Tuple[int, int]]:
        wide_count = (flags & 0x04) != 0
        abs_off = self.words_offset + pages_offset
        
        # We don't know the exact end of pages data, so we decode as much as needed
        data_to_decode = self.data[abs_off:]
        entries = decode_entry_ids(data_to_decode, wide_count)
        
        result = []
        for e in entries:
            page, item = e.page, e.item
            if self.conv_table and page < len(self.conv_table):
                mapped = self.conv_table[page]
                page, item = mapped.page, mapped.item
            result.append((page, item))
            
        return result

    def get_by_index(self, idx_type: int, index: int) -> Tuple[str, List[Tuple[int, int]]]:
        if idx_type >= 4 or index >= len(self.indices[idx_type]):
            raise IndexError()
            
        offset = self.indices[idx_type][index]
        key, pages_off, flags = self.get_word_entry(offset)
        return key, self.get_entry_ids(pages_off, flags)

    def __iter__(self):
        # Default to Prefix index (B)
        for i in range(len(self.indices[1])):
            yield self.get_by_index(1, i)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='Extract and merge Monokakido Keystore files.')
    parser.add_argument('files', nargs='+', help='Path to .keystore files')
    parser.add_argument('--dict-id', help='Dictionary ID (e.g. KNEJ.EJ) for conversion table')
    parser.add_argument('--output', default='keys', help='Base name for output files (default: keys)')
    parser.add_argument('--indices', default='1', help='Comman-separated index types to extract (0:Len, 1:Prefix, 2:Suffix, 3:Other). Default: 1')
    
    args = parser.parse_args()
    
    idx_types = [int(x.strip()) for x in args.indices.split(',')]
    
    forward_index = {}
    reverse_index = {}
    
    for file_path in args.files:
        print(f"Processing: {file_path}")
        k = Keys(file_path, args.dict_id)
        
        for idx_type in idx_types:
            if idx_type >= 4:
                print(f"Warning: Invalid index type {idx_type} in {file_path}")
                continue
            
            num_entries = len(k.indices[idx_type])
            if num_entries == 0:
                continue
                
            print(f"  Index Type {idx_type}: {num_entries} entries")
            for i in range(num_entries):
                key, entries = k.get_by_index(idx_type, i)
                
                if key not in forward_index:
                    forward_index[key] = set()
                
                for p, it in entries:
                    # Format entry ID as page-item (e.g., 00123-0abc)
                    eid = f"{p:05}{'' if it == 0 else '-' + '{:04X}'.format(it)}"
                    forward_index[key].add(eid)
                    
                    if eid not in reverse_index:
                        reverse_index[eid] = set()
                    reverse_index[eid].add(key)
    
    # Convert sets to sorted lists for JSON serialization
    print("Formatting output...")
    final_forward = {k: sorted(list(v)) for k, v in sorted(forward_index.items())}
    final_reverse = {k: sorted(list(v)) for k, v in sorted(reverse_index.items())}
    
    fwd_path = f"{args.output}_forward.json"
    rev_path = f"{args.output}_reverse.json"
    
    with open(fwd_path, 'w', encoding='utf-8') as f:
        json.dump(final_forward, f, ensure_ascii=False, indent=2)
    with open(rev_path, 'w', encoding='utf-8') as f:
        json.dump(final_reverse, f, ensure_ascii=False, indent=2)
        
    print(f"Success!")
    print(f"  Forward Index: {len(final_forward)} keys -> {fwd_path}")
    print(f"  Reverse Index: {len(final_reverse)} entries -> {rev_path}")
