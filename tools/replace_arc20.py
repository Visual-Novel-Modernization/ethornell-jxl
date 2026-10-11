"""Replace exactly one ARC20 payload while preserving record bytes/order/attributes."""
from __future__ import annotations
import argparse, pathlib, struct, hashlib, json, shutil, sys, os
ROOT = pathlib.Path(os.environ.get("BGI_GAME_DIR", "."))
MAGIC=b'BURIKO ARC20'
RECORD=128

def read_arc(path):
    data=path.read_bytes()
    if len(data)<16 or data[:12]!=MAGIC: raise ValueError('not ARC20')
    count=struct.unpack_from('<I',data,12)[0]
    ib=16+count*RECORD
    if ib>len(data): raise ValueError('index outside file')
    records=[data[16+i*RECORD:16+(i+1)*RECORD] for i in range(count)]
    payloads=[]
    for i,r in enumerate(records):
        off,length=struct.unpack_from('<II',r,96)
        a=ib+off; b=a+length
        if b>len(data): raise ValueError(f'entry {i} outside file')
        payloads.append(data[a:b])
    return data[:16],records,payloads

def replace(src,dst,index,replacement):
    header,records,payloads=read_arc(src)
    if index<0 or index>=len(records): raise ValueError('index out of range')
    old=payloads[index]
    payloads[index]=pathlib.Path(replacement).read_bytes()
    base=16+len(records)*RECORD
    newrecords=[]; cursor=0
    for i,r in enumerate(records):
        nr=bytearray(r)
        struct.pack_into('<II',nr,96,cursor,len(payloads[i]))
        newrecords.append(bytes(nr)); cursor+=len(payloads[i])
    out=bytearray(header+b''.join(newrecords))
    for p in payloads: out.extend(p)
    pathlib.Path(dst).parent.mkdir(parents=True,exist_ok=True)
    pathlib.Path(dst).write_bytes(out)
    # Re-read and assert all records/name/order/opaque attributes and payload hashes.
    h2,r2,p2=read_arc(dst)
    assert h2==header and len(r2)==len(records)
    for a,b in zip(records,r2):
        assert a[:96]==b[:96] and a[104:]==b[104:]
    assert p2[index]==payloads[index] and p2[index]!=old
    for i,(a,b) in enumerate(zip(payloads,p2)):
        assert a==b, f'roundtrip payload mismatch {i}'
    return {'source':str(src),'output':str(dst),'index':index,'name':records[index][:96].split(b'\0')[0].decode('cp932'),'old_bytes':len(old),'new_bytes':len(payloads[index]),'archive_old_bytes':src.stat().st_size,'archive_new_bytes':pathlib.Path(dst).stat().st_size,'old_sha256':hashlib.sha256(old).hexdigest(),'new_sha256':hashlib.sha256(payloads[index]).hexdigest(),'records_preserved':True,'payloads_verified':True}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('source',type=pathlib.Path);ap.add_argument('output',type=pathlib.Path);ap.add_argument('--index',type=int,required=True);ap.add_argument('--replacement',type=pathlib.Path,required=True);ap.add_argument('--report',type=pathlib.Path);a=ap.parse_args()
    row=replace(a.source,a.output,a.index,a.replacement); text=json.dumps(row,ensure_ascii=False,indent=2); print(text)
    if a.report:a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(text,encoding='utf-8')
if __name__=='__main__':main()
