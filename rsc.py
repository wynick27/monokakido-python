import json
import os
import struct
import zlib
import hashlib
from dataclasses import dataclass
from typing import List, Optional, Tuple, Union
from bs4 import BeautifulSoup

@dataclass
class IdxRecord:
    item_id: int
    map_idx: int

    @staticmethod
    def from_bytes(data: bytes) -> 'IdxRecord':
        item_id, map_idx = struct.unpack('<II', data)
        return IdxRecord(item_id, map_idx)


@dataclass
class MapRecord:
    zoffset: int
    ioffset: int

    @staticmethod
    def from_bytes(data: bytes) -> 'MapRecord':
        zoffset, ioffset = struct.unpack('<II', data)
        return MapRecord(zoffset, ioffset)

# ==== 索引 ====
@dataclass
class ResourceFile:
    seqnum: int
    len: int
    offset: int
    file: object
    
class RscIndex:
    def __init__(self, idx: Optional[List[IdxRecord]], map_: List[MapRecord], version: int = 0):
        self.idx = idx
        self.map = map_
        self.version = version

    @staticmethod
    def load_idx(path: str) -> Optional[List[IdxRecord]]:
        file_path = path + ".idx"
        if not os.path.exists(file_path):
            return None
        with open(file_path, "rb") as f:
            length = struct.unpack('<I', f.read(4))[0]
            f.seek(8)
            return [IdxRecord.from_bytes(f.read(8)) for _ in range(length)]

    @staticmethod
    def load_map(path: str) -> Tuple[List[MapRecord], int]:
        file_path = path + ".map"
        with open(file_path, "rb") as f:
            version = struct.unpack('<I', f.read(4))[0]
            length = struct.unpack('<I', f.read(4))[0]
            f.seek(8)
            records = [MapRecord.from_bytes(f.read(8)) for _ in range(length)]
            return records, version

    @staticmethod
    def new(path: str, rsc_name: str) -> 'RscIndex':
        stem = os.path.join(path, rsc_name)
        idx = RscIndex.load_idx(stem)
        map_records, version = RscIndex.load_map(stem)
        return RscIndex(idx, map_records, version)

    def get_map_idx_by_id(self, id_: int) -> int:
        if self.idx is None:
            return id_
        idx_list = self.idx
        if not idx_list:
            return None

        # Fast guess
        for guess in [id_, id_ - 1]:
            if 0 <= guess < len(idx_list) and idx_list[guess].item_id == id_:
                return guess

        # Binary search
        low, high = 0, len(idx_list)
        while low < high:
            mid = (low + high) // 2
            mid_id = idx_list[mid].item_id
            if mid_id < id_:
                low = mid + 1
            else:
                high = mid
        if low < len(idx_list) and idx_list[low].item_id == id_:
            map_idx = idx_list[low].map_idx
            if map_idx >= len(self.map):
                return None
            return map_idx
        return None

    def get_by_id(self, id_: int) -> MapRecord:
        return self.map[self.get_map_idx_by_id(id_)]

    def get_by_idx(self, idx: int) -> Tuple[int, MapRecord]:
        if self.idx:
            rec = self.idx[idx]
            if not rec or rec.map_idx != idx:
                return None
            item_id = rec.item_id
        else:
            item_id = idx

        return item_id, self.map[idx]

    def __len__(self):
        return len(self.map)

class ResourceStoreCrypto:
    SUBSTITUTION_TABLE = [
        0x2B, 0xDF, 0x33, 0xEE, 0x93, 0xE8, 0x68, 0x22,
        0x95, 0xD1, 0xDE, 0xCA, 0x95, 0xFA, 0x4F, 0xFB,
        0xA0, 0xB1, 0x8B, 0x4D, 0x18, 0x82, 0xB2, 0x40,
        0xAB, 0x0F, 0x50, 0xD8, 0x21, 0x30, 0x23
    ]
    
    CHECKSUM_XOR = 0xFBD9A2B4
    
    DATA1 = [
        0x38, 0xEF, 0x41, 0x3A, 0x06, 0x0D, 0x77, 0x5F,
        0x8A, 0x33, 0xA3, 0x38, 0xE9, 0xE4, 0xB9, 0x0B,
        0xF7, 0x7D, 0x1D, 0x4B, 0x9A, 0x44, 0x28, 0xA9,
        0x21, 0xB3, 0x16, 0x6C, 0xC3, 0x2C, 0x6A, 0xE4
    ]
    
    DATA2 = [
        [0x08, 0x09, 0x0D, 0x00, 0x01, 0x0B, 0x0A, 0x06, 0x07, 0x02, 0x0C, 0x05, 0x04, 0x0E, 0x0F, 0x03],
        [0x00, 0x0F, 0x0A, 0x08, 0x0C, 0x0B, 0x0E, 0x07, 0x04, 0x05, 0x06, 0x02, 0x01, 0x09, 0x03, 0x0D],
        [0x0E, 0x0C, 0x0F, 0x09, 0x0D, 0x06, 0x01, 0x04, 0x08, 0x00, 0x03, 0x0B, 0x07, 0x02, 0x0A, 0x05],
        [0x0C, 0x0F, 0x01, 0x0E, 0x02, 0x08, 0x06, 0x03, 0x0A, 0x05, 0x07, 0x0B, 0x04, 0x09, 0x0D, 0x00],
        [0x0D, 0x08, 0x0F, 0x03, 0x05, 0x0B, 0x09, 0x06, 0x0A, 0x0C, 0x02, 0x01, 0x0E, 0x00, 0x07, 0x04],
        [0x05, 0x09, 0x0E, 0x07, 0x0F, 0x03, 0x08, 0x02, 0x0C, 0x0D, 0x01, 0x0B, 0x04, 0x00, 0x0A, 0x06],
        [0x06, 0x0A, 0x08, 0x04, 0x0E, 0x0C, 0x00, 0x09, 0x05, 0x01, 0x03, 0x0F, 0x0B, 0x07, 0x02, 0x0D],
        [0x02, 0x0E, 0x0C, 0x0D, 0x0A, 0x01, 0x06, 0x03, 0x07, 0x04, 0x00, 0x09, 0x0B, 0x08, 0x05, 0x0F],
        [0x05, 0x0B, 0x08, 0x0F, 0x0D, 0x03, 0x06, 0x02, 0x09, 0x01, 0x04, 0x07, 0x0C, 0x0A, 0x00, 0x0E],
        [0x0A, 0x01, 0x09, 0x0E, 0x00, 0x07, 0x03, 0x0D, 0x0C, 0x06, 0x0F, 0x05, 0x04, 0x02, 0x08, 0x0B],
        [0x04, 0x0E, 0x09, 0x0A, 0x0C, 0x01, 0x08, 0x0B, 0x05, 0x00, 0x03, 0x0D, 0x0F, 0x06, 0x07, 0x02],
        [0x07, 0x0B, 0x08, 0x04, 0x03, 0x0E, 0x02, 0x06, 0x0F, 0x0C, 0x05, 0x09, 0x01, 0x0A, 0x0D, 0x00],
        [0x02, 0x03, 0x07, 0x08, 0x01, 0x00, 0x0C, 0x05, 0x04, 0x0D, 0x0B, 0x0E, 0x06, 0x09, 0x0F, 0x0A],
        [0x08, 0x04, 0x0F, 0x0D, 0x07, 0x05, 0x09, 0x03, 0x0B, 0x00, 0x0E, 0x01, 0x0A, 0x02, 0x06, 0x0C],
        [0x0C, 0x06, 0x01, 0x00, 0x0F, 0x09, 0x03, 0x0E, 0x02, 0x08, 0x05, 0x07, 0x0A, 0x0D, 0x0B, 0x04],
        [0x03, 0x00, 0x08, 0x07, 0x05, 0x0B, 0x0D, 0x0C, 0x06, 0x0E, 0x0A, 0x09, 0x01, 0x02, 0x04, 0x0F],
        [0x02, 0x00, 0x0A, 0x08, 0x04, 0x0C, 0x0D, 0x0F, 0x0E, 0x06, 0x0B, 0x03, 0x01, 0x07, 0x09, 0x05],
        [0x0C, 0x07, 0x0B, 0x08, 0x09, 0x06, 0x0E, 0x05, 0x00, 0x03, 0x02, 0x0A, 0x04, 0x01, 0x0F, 0x0D],
        [0x00, 0x06, 0x0D, 0x0F, 0x0B, 0x0C, 0x08, 0x05, 0x01, 0x07, 0x0A, 0x0E, 0x02, 0x09, 0x04, 0x03],
        [0x07, 0x01, 0x00, 0x08, 0x05, 0x0B, 0x0A, 0x06, 0x03, 0x04, 0x0E, 0x0C, 0x0F, 0x0D, 0x02, 0x09],
        [0x05, 0x03, 0x0B, 0x04, 0x07, 0x01, 0x09, 0x0F, 0x06, 0x00, 0x0E, 0x0C, 0x0A, 0x08, 0x0D, 0x02],
        [0x00, 0x03, 0x06, 0x0B, 0x09, 0x0C, 0x0E, 0x04, 0x0D, 0x08, 0x01, 0x02, 0x0F, 0x05, 0x07, 0x0A],
        [0x0B, 0x0E, 0x00, 0x0A, 0x06, 0x01, 0x04, 0x09, 0x08, 0x03, 0x0F, 0x0C, 0x0D, 0x07, 0x05, 0x02],
        [0x0A, 0x0C, 0x04, 0x07, 0x08, 0x0B, 0x05, 0x0F, 0x0D, 0x00, 0x01, 0x0E, 0x09, 0x06, 0x02, 0x03],
        [0x03, 0x0B, 0x0C, 0x0F, 0x08, 0x07, 0x01, 0x09, 0x0D, 0x02, 0x06, 0x04, 0x00, 0x05, 0x0a, 0x0E],
        [0x09, 0x04, 0x0F, 0x01, 0x0B, 0x00, 0x07, 0x0E, 0x05, 0x08, 0x0A, 0x06, 0x0D, 0x0C, 0x02, 0x03],
        [0x05, 0x03, 0x0F, 0x00, 0x0D, 0x04, 0x0C, 0x0B, 0x06, 0x08, 0x0E, 0x02, 0x01, 0x07, 0x09, 0x0A],
        [0x0B, 0x04, 0x0E, 0x0F, 0x07, 0x0A, 0x09, 0x06, 0x03, 0x00, 0x0C, 0x01, 0x08, 0x02, 0x0D, 0x05],
        [0x09, 0x00, 0x02, 0x0E, 0x05, 0x0C, 0x06, 0x0F, 0x03, 0x07, 0x01, 0x04, 0x08, 0x0D, 0x0A, 0x0B],
        [0x0A, 0x08, 0x09, 0x0B, 0x00, 0x04, 0x02, 0x0C, 0x06, 0x07, 0x0E, 0x01, 0x0D, 0x03, 0x05, 0x0F],
        [0x0C, 0x05, 0x01, 0x0F, 0x0E, 0x0B, 0x08, 0x03, 0x0D, 0x00, 0x09, 0x06, 0x07, 0x04, 0x0A, 0x02]
    ]

    @staticmethod
    def derive_key(dict_id: str) -> bytes:
        if isinstance(dict_id, str):
            dict_id = dict_id.encode('utf-8')
        key = bytearray(hashlib.sha256(dict_id).digest())
        start_offset = key[7] % 31
        for i in range(32):
            key[i] ^= ResourceStoreCrypto.SUBSTITUTION_TABLE[(start_offset + i) % 31]
        return bytes(key)

    @staticmethod
    def decrypt(encrypted_data: bytes, key: bytes) -> bytes:
        if len(encrypted_data) < 4:
            raise ValueError("Encrypted data too short")
        
        data_len = len(encrypted_data) - 4
        checksum = struct.unpack('<I', encrypted_data[data_len:])[0]
        output_len = checksum ^ ResourceStoreCrypto.CHECKSUM_XOR
        
        if output_len > data_len:
             # This might happen if there's padding
             pass
        
        # Output buffer for permutation
        output = bytearray(data_len)
        
        # permuteData
        table_idx = output_len % 31
        for offset in range(0, data_len, 16):
            block_size = min(16, data_len - offset)
            permutation = ResourceStoreCrypto.DATA2[table_idx]
            for i in range(block_size):
                output[offset + permutation[i]] = encrypted_data[offset + i]
            table_idx = (table_idx + 1) % 31
            
        # applyXorCipher
        data1_pos = output_len & 0x1F
        key_pos = 0
        for i in range(len(output)):
            output[i] ^= key[key_pos] ^ ResourceStoreCrypto.DATA1[data1_pos]
            data1_pos = (data1_pos + 1) % 32
            key_pos = (key_pos + 1) % 32
            if data1_pos == 0:
                key_pos = 0
        
        return bytes(output[:output_len])

class Rsc:
    def __init__(self, index: RscIndex, files: List[ResourceFile], key: Optional[bytes] = None):
        self.index = index
        self.files = files
        self.key = key
        self.zlib_buf = bytearray()
        self.contents_buf = bytearray()
        self.current_offset = -1
        self.current_len = 0

    @staticmethod
    def parse_fname(rsc_name: str, fname: str) -> Optional[int]:
        if fname.startswith(rsc_name + "-") and fname.endswith(".rsc"):
            try:
                return int(fname[len(rsc_name)+1:-4])
            except ValueError:
                return None
        return None

    @staticmethod
    def files(path: str, rsc_name: str) -> List[ResourceFile]:
        files = []
        for fname in os.listdir(path):
            seq = Rsc.parse_fname(rsc_name, fname)
            if seq is not None:
                full = os.path.join(path, fname)
                files.append(ResourceFile(seq, os.path.getsize(full), 0, open(full, "rb")))
        files.sort(key=lambda f: f.seqnum)
        offset = 0
        for i, f in enumerate(files):
            if f.seqnum != i + 1:
                raise FileNotFoundError("Resource files are not sequentially numbered.")
            f.offset = offset
            offset += f.len
        return files

    @staticmethod
    def new(path: str, rsc_name: str, dict_id: Optional[str] = None) -> 'Rsc':
        index = RscIndex.new(path, rsc_name)
        files = Rsc.files(path, rsc_name)
        # Only derive key if version is 1, matching C++ logic
        key = None
        if index.version == 1 and dict_id:
            key = ResourceStoreCrypto.derive_key(dict_id)
        return Rsc(index, files, key)

    def load_contents(self, zoffset: int):
        f, f_offset = file_offset(self.files, zoffset)
        f.seek(f_offset)
        marker = struct.unpack('<I', f.read(4))[0]
        
        if marker == 0:
            if not self.key:
                raise ValueError("Encountered encrypted chunk but no dict_id provided.")
            encrypted_len = struct.unpack('<I', f.read(4))[0]
            raw_data = f.read(encrypted_len)
            raw_data = ResourceStoreCrypto.decrypt(raw_data, self.key)
        else:
            compressed_len = marker
            raw_data = f.read(compressed_len)
        
        # Check if zlib compressed (usually starts with 0x78)
        if len(raw_data) > 0 and raw_data[0] == 0x78:
            try:
                self.contents_buf = zlib.decompress(raw_data)
            except zlib.error:
                self.contents_buf = raw_data
        else:
            self.contents_buf = raw_data
            
        self.current_offset = zoffset
        self.current_len = len(self.contents_buf)

    def read_direct_data(self, zoffset: int) -> bytes:
        f, f_offset = file_offset(self.files, zoffset)
        f.seek(f_offset)
        length = struct.unpack('<I', f.read(4))[0]
        return f.read(length)

    def get_by_map(self, rec: MapRecord) -> bytes:
        if rec.ioffset == 0xFFFFFFFF:
            return self.read_direct_data(rec.zoffset)
            
        if self.current_offset != rec.zoffset:
            self.load_contents(rec.zoffset)
        ioffset = rec.ioffset
        if ioffset + 4 > self.current_len:
            raise IndexError()
        
        marker = struct.unpack('<I', self.contents_buf[ioffset:ioffset+4])[0]
        if marker == 0:
            if ioffset + 8 > self.current_len:
                raise IndexError()
            length = struct.unpack('<I', self.contents_buf[ioffset+4:ioffset+8])[0]
            return self.contents_buf[ioffset+8:ioffset+8+length]
        else:
            length = marker
            return self.contents_buf[ioffset+4:ioffset+4+length]

    def get(self, id_: int) -> bytes:
        return self.get_by_map(self.index.get_by_id(id_))

    def get_by_idx(self, idx: int) -> Tuple[int, bytes]:
        id_, rec = self.index.get_by_idx(idx)
        return id_, self.get_by_map(rec)

    def __len__(self) -> int:
        return len(self.index)
    
    def export(self, path: str):
        with open(path, 'wb') as f:
            for i in range(len(self)):
                id_, data = self.get_by_idx(i)
                f.write(data)
                f.write(b'\n')
        
def file_offset(files: List[ResourceFile], offset: int) -> Tuple[object, int]:
    for f in files:
        if f.offset <= offset < f.offset + f.len:
            return f.file, offset - f.offset
    raise IndexError()

if __name__ == "__main__":
    import sys
    sys.argv = ["","contents","contents","KJCL.J"]
    if len(sys.argv) < 3:
        print("Usage: python rsc.py <path_to_contents_dir> <rsc_name> [dict_id]")
        sys.exit(1)
    
    path = sys.argv[1]
    rsc_name = sys.argv[2]
    dict_id = sys.argv[3] if len(sys.argv) > 3 else None

    # Load resources
    rsc = Rsc.new(path, rsc_name, dict_id)
    print(f"Entry count: {len(rsc)}")
    print(f"Index Version: {rsc.index.version}")

    result = {}
    for i in range(len(rsc)):
        try:
            id_, data = rsc.get_by_idx(i)
            # Try to decode as UTF-8
            try:
                text = data.decode('utf-8', errors='replace')
                if text.strip().startswith('<'):
                    soup = BeautifulSoup(text, 'html.parser')
                    dic_item = soup.select_one('dic-item')
                    if dic_item:
                        item_id = dic_item.attrs.get('id', str(id_))
                        # Just extract the text content or store the XML as string
                        result[item_id] = text
                    else:
                        result[str(id_)] = text
                else:
                    result[str(id_)] = text
            except UnicodeError:
                # Binary data like font or sound - skip
                pass
                
        except Exception:
            pass

    if result:
        output_name = f"{rsc_name}.json"
        with open(output_name, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"Exported {len(result)} entries to {output_name}")
    else:
        print("No entries were extracted.")