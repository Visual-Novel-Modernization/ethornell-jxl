"""Strict ARC20 inventory. Preserve raw index fields; do not infer semantics."""
import pathlib,struct,json,collections,hashlib,sys,os
ROOT = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else pathlib.Path(os.environ.get("BGI_GAME_DIR", "."))
def entries(path):
    size=path.stat().st_size
    with path.open('rb') as f:
        h=f.read(16)
        if h[:12]!=b'BURIKO ARC20': raise ValueError(f'Unknown archive {path}')
        n=struct.unpack_from('<I',h,12)[0]; base=16+n*128
        if base>size: raise ValueError('Index outside archive')
        records=[f.read(128) for _ in range(n)]
        for i,r in enumerate(records):
            off,length=struct.unpack_from('<II',r,96)
            if base+off+length>size: raise ValueError(f'Entry out of bounds {path}:{i}')
            f.seek(base+off); head=f.read(min(length,64))
            yield {'index':i,'name':r[:96].split(b'\0')[0].decode('cp932'),'offset':off,'length':length,'absolute':base+off,'raw_index':r.hex(),'head_hex':head.hex(),'magic':repr(head[:16])}
def main():
    result=[]
    for p in sorted((ROOT/'original').rglob('*.arc')):
        ent=list(entries(p)); kinds=collections.Counter()
        for e in ent:
            head=bytes.fromhex(e['head_hex'])
            kind='cbg' if head.startswith(b'CompressedBG___') else 'bfmovie' if head.startswith(b'BF_Movie_______') else 'ogg' if head.startswith(b'OggS') else 'riff' if head.startswith(b'RIFF') else 'other'
            e['kind']=kind; kinds[kind]+=e['length']
            if kind=='cbg':e['image_header_u16']=list(struct.unpack_from('<8H',head,16))
        row={'archive':p.relative_to(ROOT/'original').as_posix(),'bytes':p.stat().st_size,'count':len(ent),'kind_bytes':dict(kinds),'entries':ent};result.append(row)
        print(row['archive'],len(ent),dict(kinds))
    (ROOT/'reports'/'archive_inventory.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':main()
