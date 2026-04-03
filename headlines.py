import struct
import os
import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, Union, Dict

@dataclass
class HeadlineHeader:
    magic1: int
    magic2: int
    entry_count: int
    records_offset: int
    strings_offset: int
    record_stride: int
    magic4: int
    magic5: int

    @classmethod
    def from_bytes(cls, data: bytes) -> 'HeadlineHeader':
        if len(data) < 32:
            raise ValueError("File too short for header")
        unpacked = struct.unpack('<8I', data[:32])
        return cls(*unpacked)

class Headlines:
    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)
        with open(self.path, 'rb') as f:
            self.data = f.read()
            
        self.header = HeadlineHeader.from_bytes(self.data)
        
        self.entry_count = self.header.entry_count
        self.stride = self.header.record_stride if self.header.record_stride != 0 else 24
        self.records_base = self.header.records_offset
        self.strings_base = self.header.strings_offset
        
        # Performance optimization: cache for decoded strings
        self._str_cache: Dict[int, str] = {0: ""}

    def _get_string(self, offset: int) -> str:
        if offset in self._str_cache:
            return self._str_cache[offset]
        
        abs_off = self.strings_base + offset
        if abs_off >= len(self.data):
             return ""
            
        # Use fast find for null terminator
        curr = abs_off
        null_pos = -1
        while True:
            # Search for \x00\x00 (UTF-16LE null)
            p = self.data.find(b'\x00\x00', curr)
            if p == -1:
                break
                
            # Alignment check: must be at an even offset from the string start
            if (p - abs_off) % 2 == 0:
                null_pos = p
                break
            curr = p + 1
            
        if null_pos == -1:
            res = self.data[abs_off:].decode('utf-16le', errors='replace')
        else:
            res = self.data[abs_off:null_pos].decode('utf-16le', errors='replace')
            
        self._str_cache[offset] = res
        return res

    def get_by_index(self, index: int) -> Tuple[int, int, str]:
        if index >= self.entry_count:
            raise IndexError()
            
        rec_off = self.records_base + index * self.stride
        
        # Use unpack_from to avoid slicing the buffer
        # Structure: page_id(4), item_id(2), magic(2), prefixOff(4), headlineOff(4), suffixOff(4), magic(4)
        page_id, item_id, _, prefix_off, headline_off, suffix_off, _ = struct.unpack_from('<IHHIIII', self.data, rec_off)
        
        prefix = self._get_string(prefix_off)
        headline = self._get_string(headline_off)
        suffix = self._get_string(suffix_off)
        
        return page_id, item_id, prefix + headline + suffix

    def __len__(self):
        return self.entry_count

    def __iter__(self):
        # We can iterate using range to call optimized get_by_index
        for i in range(self.entry_count):
            yield self.get_by_index(i)

if __name__ == "__main__":
    import sys
    import time
    
    if len(sys.argv) < 2:
        print("Usage: python headlines.py <path_to_headlinestore>")
        sys.exit(1)
        
    path = sys.argv[1]
    
    start_time = time.time()
    headlines = Headlines(path)
    count = len(headlines)
    print(f"File: {path}")
    print(f"Entry Count: {count}")
    
    out_map = {}
    for i in range(count):
        page_id, item_id, text = headlines.get_by_index(i)
        page_key = f"{page_id:05}" if item_id == 0 else f"{page_id:05}-{item_id:04x}"
        out_map[page_key] = text
        
        if i % 10000 == 0 and i > 0:
            elapsed = time.time() - start_time
            print(f"Processed {i}/{count} entries... ({elapsed:.2f}s)")
            
    output_name = Path(path).stem + "_headlines.json"
    with open(output_name, 'w', encoding='utf-8') as f:
        json.dump(out_map, f, ensure_ascii=False, indent=2)
        
    total_time = time.time() - start_time
    print(f"Done! Exported to {output_name} in {total_time:.2f} seconds.")
