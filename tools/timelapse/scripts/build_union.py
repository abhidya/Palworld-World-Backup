import json,os,sys,glob,subprocess,time
sys.path.insert(0,'pst/src'); sys.path.insert(0,'pst/src/palsav'); sys.path.insert(0,'pst/src/i18n')
os.environ['QT_QPA_PLATFORM']='offscreen'
import ooz
from palworld_save_tools.gvas import GvasFile
from palworld_save_tools.paltypes import PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES
from palworld_save_tools.palsav import decompress_sav_to_gvas
from palworld_aio.managers.base_manager import export_base_json
SP=sys.argv[1]; REPO="/Users/mannybhidya/Palworld"
def enc(o):
    if isinstance(o,(bytes,bytearray)): return list(o)
    return str(o)
def load_level(raw):
    gvas,_=decompress_sav_to_gvas(raw)
    return GvasFile.read(gvas,PALWORLD_TYPE_HINTS,PALWORLD_CUSTOM_PROPERTIES).dump()
sources=[]
for p in sorted(glob.glob(f"{SP}/nas/*/backup/world/*/Level.sav")+glob.glob(f"{SP}/nas/*/Level.sav"),
                key=os.path.getmtime):
    sources.append(('file',p,int(os.path.getmtime(p))))
commits=[l.split() for l in open(f"{SP}/commits.txt")][::-1]
step=max(1,len(commits)//40)
for c,t in commits[::step]: sources.append(('git',c,int(t)))
sources.append(('file',"/Users/mannybhidya/PalworldServer/palworld/Pal/Saved/SaveGames/0/64EE4B2C4C81F4912BF109850820D9BA/Level.sav",int(time.time())))
print(f"sources: {len(sources)}",flush=True)
union={}   # base8 -> {'base_camp':..,'objs':{id:obj},'level':int}
for k,(kind,ref,ts) in enumerate(sources):
    try:
        if kind=='git':
            raw=subprocess.run(["git","-C",REPO,"lfs","smudge"],
                input=subprocess.run(["git","-C",REPO,"show",f"{ref}:world/current/Level.sav"],
                capture_output=True).stdout,capture_output=True).stdout
        else:
            raw=open(ref,'rb').read()
        lvl=load_level(raw)
        wsd=lvl['properties']['worldSaveData']['value']
        for e in wsd['BaseCampSaveData']['value']:
            bid=str(e['key']); b8=bid[:8]
            try: d=export_base_json(lvl,bid)
            except Exception: continue
            u=union.setdefault(b8,{'base_camp':d['base_camp'],'objs':{},'level':d.get('base_camp_level',1),
                                   'item_containers':{},'works':{},'char_containers':[],'characters':[],'dynamic_items':[]})
            u['base_camp']=d['base_camp']; u['level']=max(u['level'],d.get('base_camp_level',1))
            for m in d['map_objects']:
                iid=str(m['Model']['value']['RawData']['value'].get('instance_id'))
                if iid not in u['objs']: u['objs'][iid]=m
        if k%10==0: print(f"  {k}/{len(sources)} bases={ {b:len(v['objs']) for b,v in union.items()} }",flush=True)
    except Exception as ex:
        print(f"  skip {ref[:12]}: {ex}",flush=True)
os.makedirs(f"{SP}/mappal/public/union",exist_ok=True)
for b8,u in union.items():
    out={'base_camp':u['base_camp'],'base_camp_level':u['level'],
         'map_objects':list(u['objs'].values()),'characters':[],'item_containers':[],
         'char_containers':[],'works':[],'dynamic_items':[]}
    fp=f"{SP}/mappal/public/union/union_{b8}.json"
    json.dump(out,open(fp,'w'),default=enc)
    print(f"{b8}: {len(out['map_objects'])} objects -> {os.path.getsize(fp)//1024} KB",flush=True)
