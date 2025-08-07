import os
import struct
import zlib
from dataclasses import dataclass
from typing import List, Tuple, Optional



class Format:
    Uncompressed = 0
    Zlib = 1


@dataclass
class NrscIdxRecord:
    format: int
    fileseq: int
    id_str_offset: int
    file_offset: int
    length: int

    @staticmethod
    def from_bytes(data: bytes) -> 'NrscIdxRecord':
        fmt, fileseq, id_str_offset, file_offset, length = struct.unpack('<HHIII', data)
        return NrscIdxRecord(fmt, fileseq, id_str_offset, file_offset, length)

    def format_type(self) -> int:
        if self.format == Format.Uncompressed:
            return Format.Uncompressed
        elif self.format == Format.Zlib:
            return Format.Zlib
        else:
            raise Exception("Invalid format")

    def fileseq_index(self) -> int:
        return self.fileseq

    def file_offset_u64(self) -> int:
        return self.file_offset

    def len_usize(self) -> int:
        return self.length


class NrscIndex:
    def __init__(self, idx: List[NrscIdxRecord], ids: str):
        self.idx = idx
        self.ids = ids

    @staticmethod
    def new(path: str) -> 'NrscIndex':
        idx_path = os.path.join(path, "index.nidx")
        with open(idx_path, "rb") as f:
            header = f.read(8)
            length = struct.unpack('<I', header[4:8])[0]
            idx_size = length * 16  # 16 bytes per record
            idx_data = f.read(idx_size)

            idx = [NrscIdxRecord.from_bytes(idx_data[i:i + 16])
                    for i in range(0, idx_size, 16)]

            ids = f.read().decode("utf-8", errors="replace")
            return NrscIndex(idx, ids)

    def _string_data_offset(self):
        return len(self.idx) * 16 + 8

    def get_id_at(self, offset: int) -> str:
        base = self._string_data_offset()
        relative = offset - base
        if relative > 0 and self.ids[relative - 1] != '\0':
            raise IndexError()
        try:
            end = self.ids.index('\0', relative)
            return self.ids[relative:end]
        except ValueError:
            raise IndexError()

    def get_by_id(self, id_: str) -> NrscIdxRecord:
        for rec in self.idx:
            try:
                if self.get_id_at(rec.id_str_offset) == id_:
                    return rec
            except IndexError:
                continue
        return None

    def get_by_idx(self, idx: int) -> Tuple[str, NrscIdxRecord]:
        rec = self.idx[idx]
        id_str = self.get_id_at(rec.id_str_offset)
        return id_str, rec


@dataclass
class ResourceFile:
    seqnum: int
    len: int
    offset: int
    file: object


class NrscData:
    def __init__(self, files: List[ResourceFile]):
        self.files = files
        self.read_buf = bytearray()
        self.decomp_buf = bytearray()

    def get_by_nidx_rec(self, rec: NrscIdxRecord) -> bytes:
        file = self.files[rec.fileseq_index()]
        file.file.seek(rec.file_offset_u64())
        length = rec.len_usize()

        if len(self.read_buf) < length:
            self.read_buf = bytearray(length)
        file.file.readinto(self.read_buf)

        if rec.format_type() == Format.Uncompressed:
            return bytes(self.read_buf)
        elif rec.format_type() == Format.Zlib:
            decompressed = zlib.decompress(bytes(self.read_buf))
            return decompressed


class Nrsc:
    def __init__(self, index: NrscIndex, data: NrscData):
        self.index = index
        self.data = data

    @staticmethod
    def parse_fname(fname: str) -> Optional[int]:
        if fname.endswith(".nrsc"):
            try:
                return int(fname[:-5])
            except ValueError:
                return None
        return None

    @staticmethod
    def files(path: str) -> List[ResourceFile]:
        entries = os.listdir(path)
        files = []
        for fname in entries:
            full_path = os.path.join(path, fname)
            seqnum = Nrsc.parse_fname(fname)
            if seqnum is not None:
                files.append(ResourceFile(
                    seqnum=seqnum,
                    len=os.path.getsize(full_path),
                    offset=0,
                    file=open(full_path, "rb")
                ))
        files.sort(key=lambda x: x.seqnum)
        for i, f in enumerate(files):
            if f.seqnum != i:
                raise Exception(f"File sequence number mismatch: expected {i}, got {f.seqnum}")
            f.offset = sum(fi.len for fi in files[:i])
        return files

    @staticmethod
    def new(path: str) -> 'Nrsc':
        files = Nrsc.files(path)
        index = NrscIndex.new(path)
        return Nrsc(index, NrscData(files))

    def get_by_idx(self, idx: int) -> Tuple[str, bytes]:
        id_str, rec = self.index.get_by_idx(idx)
        data = self.data.get_by_nidx_rec(rec)
        return id_str, data

    def get(self, id_: str) -> bytes:
        rec = self.index.get_by_id(id_)
        return self.data.get_by_nidx_rec(rec)

    def len(self) -> int:
        return len(self.index.idx)
    

if __name__ == "__main__":
    import zipfile
    nrsc = Nrsc.new(".\\NHKACCENT2\\Contents\\NHK_ACCENT\\audio")
    print(f"Resource count: {nrsc.len()}")
    with zipfile.ZipFile('audio_data.zip', 'w') as zf:
        for i in range(nrsc.len()):
            
            id_, data = nrsc.get_by_idx(i)
            path = f"{id_}.aac"
            zf.writestr(path, data)
        print("ZIP 包已生成")