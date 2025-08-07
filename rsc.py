import json
import os
import struct
import zlib
from dataclasses import dataclass
from typing import List, Optional, Tuple

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
    def __init__(self, idx: Optional[List[IdxRecord]], map_: List[MapRecord]):
        self.idx = idx
        self.map = map_

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
    def load_map(path: str) -> List[MapRecord]:
        file_path = path + ".map"
        with open(file_path, "rb") as f:
            f.seek(4)
            length = struct.unpack('<I', f.read(4))[0]
            f.seek(8)
            return [MapRecord.from_bytes(f.read(8)) for _ in range(length)]

    @staticmethod
    def new(path: str, rsc_name: str) -> 'RscIndex':
        stem = os.path.join(path, rsc_name)
        idx = RscIndex.load_idx(stem)
        map_ = RscIndex.load_map(stem)
        return RscIndex(idx, map_)

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

class Rsc:
    def __init__(self, index: RscIndex, files: List[ResourceFile]):
        self.index = index
        self.files = files
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
    def new(path: str, rsc_name: str) -> 'Rsc':
        index = RscIndex.new(path, rsc_name)
        files = Rsc.files(path, rsc_name)
        return Rsc(index, files)

    def load_contents(self, zoffset: int):
        f, f_offset = file_offset(self.files, zoffset)
        f.seek(f_offset)
        raw_len = struct.unpack('<I', f.read(4))[0]
        self.zlib_buf = bytearray(f.read(raw_len))
        self.contents_buf = zlib.decompress(self.zlib_buf)
        self.current_offset = zoffset
        self.current_len = len(self.contents_buf)

    def get_by_map(self, rec: MapRecord) -> bytes:
        if self.current_offset != rec.zoffset:
            self.load_contents(rec.zoffset)
        ioffset = rec.ioffset
        if ioffset + 4 > self.current_len:
            raise IndexError()
        length = struct.unpack('<I', self.contents_buf[ioffset:ioffset+4])[0]
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

from bs4 import BeautifulSoup,Tag,NavigableString

def get_text(soup: Tag):
    for tag in soup.find_all('round_box'):
        # 创建新的内容：括号包裹的文本
        new_text = NavigableString(f'({tag.get_text()})')
        tag.replace_with(new_text)
    return soup.get_text()


def parse_html(soup:Tag):
    result = {}
    if soup.name == 'dic-item':
        result['id'] = soup.attrs['id']
        result['items'] = []
        for child in soup.children:
            result['items'].append(parse_html(child))
    elif soup.name == 'head-g':
        head = soup.select_one(".head > h")
        if head:
            result = parse_html(head)
        body = parse_html(soup.select_one(".body"))
        result.update(body) 
        if 'con_table' in result:
            if 'accent' in result['con_table'] and len(result['con_table']) == 1:
                result['accent'] = result['con_table']['accent']
            else:
                print("Warning: Unexpected con_table structure")
            
    elif soup.name == 'ref':
        if len(soup.contents) != 1 or soup.contents[0].name != 'a':
            print("Warning")
        return {'id':soup.contents[0]['href'],'word':soup.contents[0].get_text()}

    elif soup.name == 'accent' or soup.name == 'accent_round' or soup.name == 'accent_example':
        accent_head = soup.select_one('accent_head')
        if accent_head:
            if accent_head.contents != 1 and accent_head.contents[0].name != 'square_box':
                print("Warning: Unexpected accent_head structure")
            result['square_box'] = accent_head.get_text()
        accent_text = soup.select_one('accent_text')
        if not accent_text:
            accent_text = soup
        sound = accent_text.select_one('sound')

        if sound:
            sound.extract()
            result['sound'] = sound.a['href']
        else:
            print("Warning: Missing sound link")
        square_box = accent_text.select_one('square_box')
        if square_box:  
            square_box.extract()
            result['square_box'] = square_box.get_text()
        result['text'] = get_text(accent_text)
        
        
    else:
        if any(isinstance(child,NavigableString) for child in soup.contents):
            if len(soup.contents) == 1:
                return str(soup.contents[0].string)
            else:
                result = []
                for child in soup.contents:
                    if isinstance(child,NavigableString):
                        result.append(str(child))
                    else:
                        result.append({child.name:parse_html(child)})
        else:
            for attr in soup.attrs:
                if attr == 'class':
                    continue
                if attr != 'id':
                    print("unknown attr")
                result[attr] = soup.attrs[attr]
            for child in soup.contents:
                data = parse_html(child)
                if child.name in result:
                    if isinstance(result[child.name],list):
                        result[child.name].append(data)
                    else:
                        result[child.name] = [result[child.name], data]
                else:
                    result[child.name] = data
            
    return result




if __name__ == "__main__":
    rsc = Rsc.new(".\\NHKACCENT2\\Contents\\NHK_ACCENT\\contents", "contents")
    print(f"Entry count: {len(rsc)}")

    result = {}
    for i in range(len(rsc)):
        id_, data = rsc.get_by_idx(i)
        text = data.decode('utf-8')
        soup= BeautifulSoup(text)
        data = parse_html(soup.select_one('dic-item'))
        result[data['id']] = data['items']
        print(f"ID: {id_}, Data Length: {len(data)}")
    with open("accent_dict.json", "w", encoding="utf-8") as f:
        json.dump(result,f,indent=2,ensure_ascii=False)