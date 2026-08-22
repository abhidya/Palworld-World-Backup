import subprocess, sys, json, os, time
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
REPO="/Users/mannybhidya/Palworld"
PATH_IN_REPO="world/current/Level.sav"

def frame(arg):
    commit, ts = arg
    try:
        p1=subprocess.run(["git","-C",REPO,"show",f"{commit}:{PATH_IN_REPO}"],capture_output=True)
        p2=subprocess.run(["git","-C",REPO,"lfs","smudge"],input=p1.stdout,capture_output=True)
        raw=p2.stdout
        if len(raw)<100000: return (ts, None)
        import ooz
        from palworld_save_tools.gvas import GvasFile
        from palworld_save_tools.paltypes import PALWORLD_TYPE_HINTS, PALWORLD_CUSTOM_PROPERTIES
        from palworld_save_tools.palsav import decompress_sav_to_gvas
        gvas,_=decompress_sav_to_gvas(raw)
        wsd=GvasFile.read(gvas,PALWORLD_TYPE_HINTS,PALWORLD_CUSTOM_PROPERTIES).dump()['properties']['worldSaveData']['value']
        import basecamp_attrib
        camps=basecamp_attrib.camps_from(wsd)
        out={}
        for m in wsd['MapObjectSaveData']['value']['values']:
            mr=m['Model']['value']['RawData']['value']
            b=str(mr.get('base_camp_id_belong_to'))[:8]
            tr=mr.get('initital_transform_cache',{}).get('translation',{})
            x,y=tr.get('x',0),tr.get('y',0)
            b=basecamp_attrib.attribute(b,x,y,camps)
            if b is None: continue
            out[str(mr.get('instance_id'))]=(b,m['MapObjectId']['value'],
                round(x),round(y),round(tr.get('z',0)))
        return (ts,out)
    except Exception as e:
        return (ts,None)

if __name__=="__main__":
    commits=[l.split() for l in open(sys.argv[1])]
    commits=[(c,int(t)) for c,t in commits][::-1]   # oldest first
    print(f"frames: {len(commits)}", flush=True)
    first={}; last={}; meta={}; done=0; t0=time.time()
    with Pool(5) as pool:
        for ts,objs in pool.imap(frame, commits, chunksize=4):
            done+=1
            if objs is None: continue
            for iid,rec in objs.items():
                if iid not in first: first[iid]=ts; meta[iid]=rec
                last[iid]=ts
            if done%50==0:
                print(f"  {done}/{len(commits)}  objs={len(first)}  {time.time()-t0:.0f}s", flush=True)
    rows=[{"id":i,"base":meta[i][0],"type":meta[i][1],"x":meta[i][2],"y":meta[i][3],"z":meta[i][4],
           "first":first[i],"last":last[i]} for i in first]
    json.dump(rows, open(sys.argv[2],"w"))
    print(f"DONE {len(rows)} objects -> {sys.argv[2]} in {time.time()-t0:.0f}s", flush=True)
