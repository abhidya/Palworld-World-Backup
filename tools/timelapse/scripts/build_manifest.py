import os
import sys,os,json,struct,io
os.chdir(os.environ['PALTL_WORK'])
sys.path.insert(0,'.')
from pkg import parse_names
from dtparse import R, skip_props
import pakx

def fname(names,i,num):
    b=names[i] if 0<=i<len(names) else None
    if b is None: return None
    return b+("_%d"%(num-1) if num>0 else "")

# every asset package present in the pak (for existence checks + sibling lookup)
PKGS=set(); BYDIR={}
for line in open('pakfiles.txt'):
    p=line.strip()
    if p.endswith('.uasset'):
        g=p[:-7]
        if g.startswith('Pal/Content/'):
            gp='/Game/'+g[len('Pal/Content/'):]
            PKGS.add(gp); BYDIR.setdefault(gp.rsplit('/',1)[0],[]).append(gp.rsplit('/',1)[-1])

def load_table(pakpath):
    ua=pakx.extract(pakpath+".uasset"); ux=pakx.extract(pakpath+".uexp")
    names=parse_names(ua)['names']
    r=R(ux,names); skip_props(r); r.seek(r.tell()+4)
    n=r.i32(); out={}
    for _ in range(n):
        rn=r.name(); ps=skip_props(r); rec={}
        for p in ps:
            if p['name']=='BlueprintClassSoft' and p['type']=='SoftObjectProperty':
                pi,pn=struct.unpack('<ii',ux[p['off']:p['off']+8])
                rec['bp']=fname(names,pi,pn)
        out[rn]=rec
    return out

master={}
for t in ["Pal/Content/Pal/DataTable/MapObject/DT_MapObjectMasterDataTable",
          "Pal/Content/Pal/DataTable/MapObject/DT_MapObjectMasterDataTable_Common"]:
    for k,v in load_table(t).items(): master.setdefault(k,v)
lower={k.lower():k for k in master}

def leaf(p): return p.rsplit('/',1)[-1]
CLS={}
def asset_class(p):
    """load referenced asset, classify by its own name table"""
    if p in CLS: return CLS[p]
    try:
        nm=set(parse_names(pakx.extract("Pal/Content/"+p[len("/Game/"):]+".uasset"))['names'])
        c='StaticMesh' if 'StaticMesh' in nm else ('SkeletalMesh' if 'SkeletalMesh' in nm else None)
    except Exception: c=None
    CLS[p]=c; return c
def is_mesh(p):
    if '/Material/' in p or '/material/' in p: return False
    if leaf(p).startswith(('MI_','M_','T_','AS_','ABP_','PA_','MPC_')): return False
    return asset_class(p) is not None

REFC={}
def refs_of(gamepath):
    if gamepath in REFC: return REFC[gamepath]
    try:
        nm=parse_names(pakx.extract("Pal/Content/"+gamepath[len("/Game/"):]+".uasset"))['names']
        v=[n for n in nm if n.startswith('/Game/')and n!=gamepath]
    except Exception: v=None
    REFC[gamepath]=v; return v

def resolve(bp,depth=0,seen=None):
    """-> (meshes, note). Recurses one level into referenced BPs."""
    seen=seen or set()
    if bp in seen or bp is None: return [],"cycle/none"
    seen.add(bp)
    rf=refs_of(bp)
    if rf is None: return [],"package '%s' not in pak"%bp
    meshes=[x for x in rf if is_mesh(x)]
    if meshes: return meshes,""
    # AS_ anim-sequence only -> look for sibling SK_ in same folder (INFERRED)
    anim=[x for x in rf if leaf(x).startswith('AS_') and '/Model/' in x]
    for a in anim:
        d=a.rsplit('/',1)[0]
        sk=[d+'/'+s for s in BYDIR.get(d,[]) if s.startswith('SK_') or s.startswith('SM_')]
        if sk: return sk,"INFERRED: BP referenced only anim-sequence %s; took sibling mesh from same folder"%leaf(a)
    if depth<1:
        for child in [x for x in rf if '/Blueprint/' in x and leaf(x).startswith(('BP_','ABP_'))]:
            m,_=resolve(child,depth+1,seen)
            if m: return m,"via referenced blueprint %s"%leaf(child)
    return [],"no mesh reference found (composite / child-actor blueprint)"

ids=list(json.load(open('mappal/src/data/objects.json'))['types'])
manifest={}; st={'resolved':0,'bp_no_mesh':0,'no_bp':0,'inferred':0,'multi':0,'missing_from_pak':0}
for oid in ids:
    key=oid if oid in master else lower.get(oid.lower())
    e={"blueprint":None,"meshPath":None,"meshName":None,"resolved":False,"note":""}
    notes=[]
    if key is None:
        e['note']="no row in DT_MapObjectMasterDataTable(_Common)"; st['no_bp']+=1; manifest[oid]=e; continue
    if key!=oid: notes.append("matched table row '%s' case-insensitively"%key)
    bp=master[key].get('bp'); e['blueprint']=bp
    if not bp:
        e['note']="; ".join(notes+["row has empty BlueprintClassSoft"]); st['no_bp']+=1; manifest[oid]=e; continue
    meshes,note=resolve(bp)
    if note: notes.append(note)
    if not meshes:
        e['note']="; ".join(notes); st['bp_no_mesh']+=1; manifest[oid]=e; continue
    sm=[x for x in meshes if leaf(x).startswith('SM_')]
    ordered=sm+[x for x in meshes if x not in sm]
    e['meshPath']=ordered[0]; e['meshName']=leaf(ordered[0]); e['resolved']=True; st['resolved']+=1
    if len(ordered)>1: e['meshPaths']=ordered; notes.append("multi-mesh: %d refs, primary=first"%len(ordered)); st['multi']+=1
    if 'INFERRED' in note: st['inferred']+=1
    missing=[x for x in ordered if x not in PKGS]
    if missing: notes.append("WARN: %d ref(s) not found in pak index"%len(missing)); st['missing_from_pak']+=1
    if not sm: notes.append("skeletal mesh (SK_), not static")
    e['note']="; ".join(notes)
    manifest[oid]=e

json.dump(manifest,open('mesh_manifest.json','w'),indent=2,sort_keys=True)
print(json.dumps(st,indent=1))
