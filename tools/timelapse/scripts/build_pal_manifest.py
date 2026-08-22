import os
import sys,os,json,struct
os.chdir(os.environ['PALTL_WORK']); sys.path.insert(0,'.')
from pkg import parse_names
from dtparse import R, skip_props
import pakx

def fname(names,i,num):
    if not (0<=i<len(names)): return None
    return names[i]+("_%d"%(num-1) if num>0 else "")

PKGS=set()
for line in open('pakfiles.txt'):
    p=line.strip()
    if p.endswith('.uasset') and p.startswith('Pal/Content/'):
        PKGS.add('/Game/'+p[len('Pal/Content/'):-7])

def load_bpclass(path):
    ua=pakx.extract(path+".uasset"); ux=pakx.extract(path+".uexp")
    names=parse_names(ua)['names']
    r=R(ux,names); skip_props(r); r.seek(r.tell()+4)
    out={}
    for _ in range(r.i32()):
        rn=r.name()
        for p in skip_props(r):
            if p['name']=='BPClass' and p['type']=='SoftObjectProperty':
                pi,pn=struct.unpack('<ii',ux[p['off']:p['off']+8])
                out[rn]=fname(names,pi,pn)
    return out

master={}
for t in ["Pal/Content/Pal/DataTable/Character/DT_PalBPClass",
          "Pal/Content/Pal/DataTable/Character/DT_PalBPClass_Common"]:
    try:
        for k,v in load_bpclass(t).items(): master.setdefault(k,v)
    except Exception as e: print("table fail",t,repr(e),file=sys.stderr)
print("BPClass rows:",len(master),file=sys.stderr)

CACHE={}
def refs(gp):
    if gp in CACHE: return CACHE[gp]
    try:
        nm=parse_names(pakx.extract("Pal/Content/"+gp[len("/Game/"):]+".uasset"))['names']
        v=[n for n in nm if n.startswith('/Game/') and n!=gp]
    except Exception: v=None
    CACHE[gp]=v; return v

def leaf(p): return p.rsplit('/',1)[-1]
def find_sk(bp,depth=0,seen=None):
    seen=seen or set()
    if not bp or bp in seen: return [],"cycle"
    seen.add(bp)
    rf=refs(bp)
    if rf is None: return [],"blueprint package '%s' not in server pak"%leaf(bp)
    sk=[x for x in rf if leaf(x).startswith('SK_') and '/Model/' in x]
    if sk: return sk,""
    if depth<1:
        for c in [x for x in rf if '/Blueprint/' in x and leaf(x).startswith('BP_')]:
            m,_=find_sk(c,depth+1,seen)
            if m: return m,"via referenced blueprint %s"%leaf(c)
    return [],"no SK_ reference in blueprint"

man={}; st={'resolved':0,'no_mesh':0,'no_bp':0}
for cid,bp in sorted(master.items()):
    e={"blueprint":bp,"meshPath":None,"meshName":None,"resolved":False,"note":""}
    if not bp:
        e['note']="empty BPClass"; st['no_bp']+=1; man[cid]=e; continue
    sk,note=find_sk(bp); notes=[note] if note else []
    if not sk:
        e['note']="; ".join(notes); st['no_mesh']+=1; man[cid]=e; continue
    e['meshPath']=sk[0]; e['meshName']=leaf(sk[0]); e['resolved']=True; st['resolved']+=1
    if len(sk)>1: e['meshPaths']=sk; notes.append("multi-mesh: %d SK_ refs, primary=first"%len(sk))
    if sk[0] not in PKGS: notes.append("WARN: not in server pak index")
    e['note']="; ".join(notes)
    man[cid]=e
json.dump(man,open('pal_manifest.json','w'),indent=2,sort_keys=True)
print(json.dumps(st))
