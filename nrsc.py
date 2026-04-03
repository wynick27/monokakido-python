import os
import struct
import zlib
import argparse
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, Union, Dict

@dataclass
class NamedResourceStoreIndexHeader:
    magic: int
    record_count: int

    @classmethod
    def from_bytes(cls, data: bytes) -> 'NamedResourceStoreIndexHeader':
        if len(data) < 8:
            raise ValueError("Size of index header must be at least 8 bytes")
        magic, count = struct.unpack('<II', data[:8])
        return cls(magic, count)

@dataclass
class NamedResourceStoreIndexRecord:
    format_type: int    # 0 = uncompressed, 1 = zlib
    file_sequence: int  # {seq}.nrsc
    id_offset: int      # absolute offset from start of .nidx
    file_offset: int    # offset within {seq}.nrsc
    length: int         # length of (possibly compressed) data

    @classmethod
    def from_bytes(cls, data: bytes) -> 'NamedResourceStoreIndexRecord':
        if len(data) < 16:
            raise ValueError("Size of index record must be 16 bytes")
        fmt, seq, id_off, f_off, length = struct.unpack('<HHIII', data[:16])
        return cls(fmt, seq, id_off, f_off, length)

class NamedResourceStore:
    def __init__(self, directory: Union[str, Path]):
        self.directory = Path(directory)
        if not self.directory.is_directory():
            raise FileNotFoundError(f"NRSC directory not found: {directory}")
            
        self._load_index()
        self._discover_data_files()

    def _load_index(self):
        # find .nidx file
        nidx_files = list(self.directory.glob("*.nidx"))
        if not nidx_files:
            raise FileNotFoundError(f"No .nidx file found in {self.directory}")
        if len(nidx_files) > 1:
             print(f"Warning: Multiple .nidx files found. Using {nidx_files[0]}")
             
        self.nidx_path = nidx_files[0]
        with open(self.nidx_path, 'rb') as f:
            header_data = f.read(8)
            self.header = NamedResourceStoreIndexHeader.from_bytes(header_data)
            
            # Read records
            self.records = []
            for _ in range(self.header.record_count):
                record_data = f.read(16)
                self.records.append(NamedResourceStoreIndexRecord.from_bytes(record_data))
            
            # Read strings section
            self.strings_base = 8 + self.header.record_count * 16
            self.id_strings = f.read()

    def _discover_data_files(self):
        self.data_files: Dict[int, Path] = {}
        for f in self.directory.glob("*.nrsc"):
            try:
                seq = int(f.stem)
                self.data_files[seq] = f
            except ValueError:
                continue
                
    def get_id_at(self, offset: int) -> str:
        # id_offset in record is absolute, including the 8B header and 16B*count records
        rel_off = offset - self.strings_base
        if rel_off < 0 or rel_off >= len(self.id_strings):
             return f"unknown_{offset:x}"
            
        # Find null terminator
        null_pos = self.id_strings.find(b'\0', rel_off)
        if null_pos == -1:
            return self.id_strings[rel_off:].decode('utf-8', errors='replace')
        return self.id_strings[rel_off:null_pos].decode('utf-8', errors='replace')

    def get_data(self, record: NamedResourceStoreIndexRecord) -> bytes:
        if record.file_sequence not in self.data_files:
            raise FileNotFoundError(f"Data file {record.file_sequence}.nrsc not found")
            
        path = self.data_files[record.file_sequence]
        with open(path, 'rb') as f:
            f.seek(record.file_offset)
            data = f.read(record.length)
            
        if record.format_type == 1: # Zlib
            return zlib.decompress(data)
        return data

    def __len__(self):
        return len(self.records)

    def entries(self) -> List[Tuple[str, NamedResourceStoreIndexRecord]]:
        return [(self.get_id_at(r.id_offset), r) for r in self.records]

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Extract Monokakido Named Resource Store (.nrsc)')
    parser.add_argument('directory', help='Directory containing .nidx and .nrsc files')
    parser.add_argument('--list', action='store_true', help='List all resources')
    parser.add_argument('--output', '-o', help='Output directory or ZIP filename (if ends with .zip)')
    parser.add_argument('--ext', default='', help='Force extension for extracted files (e.g., .aac or .png)')
    
    args = parser.parse_args()
    
    try:
        nrsc = NamedResourceStore(args.directory)
    except Exception as e:
        print(f"Error loading NRSC: {e}")
        exit(1)
        
    print(f"Directory: {args.directory}")
    print(f"Resource count: {len(nrsc)}")
    
    if args.list:
        print("\n%-40s %-10s %-10s %-6s" % ("ID", "FileSeq", "Length", "Format"))
        print("-" * 70)
        for name, r in nrsc.entries():
            fmt = "Zlib" if r.format_type == 1 else "Raw"
            print("%-40s %-10d %-10d %-6s" % (name, r.file_sequence, r.length, fmt))
        exit(0)
        
    if args.output:
        is_zip = args.output.lower().endswith('.zip')
        ext = args.ext if args.ext.startswith('.') or not args.ext else ('.' + args.ext)
        
        if is_zip:
            print(f"Extracting to ZIP: {args.output}")
            with zipfile.ZipFile(args.output, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
                for i, (name, r) in enumerate(nrsc.entries()):
                    try:
                        data = nrsc.get_data(r)
                        fname = f"{name}{ext}" if not name.lower().endswith(ext.lower()) else name
                        zf.writestr(fname, data)
                        if (i + 1) % 100 == 0:
                            print(f"  Processed {i+1}/{len(nrsc)}...")
                    except Exception as e:
                        print(f"  Error extracting {name}: {e}")
        else:
            print(f"Extracting to directory: {args.output}")
            os.makedirs(args.output, exist_ok=True)
            for i, (name, r) in enumerate(nrsc.entries()):
                try:
                    data = nrsc.get_data(r)
                    fname = f"{name}{ext}" if not name.lower().endswith(ext.lower()) else name
                    out_path = Path(args.output) / fname
                    # Support folder structure in IDs if any
                    out_path.parent.makedirs(exist_ok=True)
                    with open(out_path, 'wb') as f:
                        f.write(data)
                    if (i + 1) % 100 == 0:
                        print(f"  Processed {i+1}/{len(nrsc)}...")
                except Exception as e:
                     print(f"  Error extracting {name}: {e}")
                     
        print("\nExtraction complete!")
    else:
        print("\nUse --list to see items or --output <dir|zip> to extract.")