import puppeteer from 'puppeteer-core';
import fs from 'fs';
const SP=process.env.PALTL_WORK||process.cwd();
const APP=process.env.MAPPAL_ROOT||`${SP}/mappal`;
const TOOL_DIR=new URL('.',import.meta.url).pathname.replace(/\/$/,'');
const CHROME='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const BASE=process.argv[2];
const MINFRAMES=parseInt(process.argv[3]||'240');
const TURNS=parseFloat(process.argv[4]||'1.5');
const HEADLESS=process.env.HEADLESS==='1';
// Both new behaviours are opt-out so a plain run stays comparable to the old one.
const DAYNIGHT=process.env.DAYNIGHT!=='0';       // Change 3: in-game day/night lighting
const GAMEPACE=process.env.GAMEPACE!=='0';       // Change 2: pace replay by in-game time
// Pals + player avatars. On when the per-base files exist; ACTORS=0 opts out so
// a run stays comparable with the pre-Pal renders. Neither layer touches camera
// framing (see __bounds) — a Pal wandering to the edge of the base must not
// yank the camera the way a new building should.
const ACTORS=process.env.ACTORS!=='0';
// Which BAKED STANCE the avatar is drawn in. Both are real frames of the game's
// own build animations, extracted the same way (palxtex --extract <dir>
// <manifest> <AnimSequence> <frame>):
//   kneel    AS_Player_Female_Architecture2 frame 31   — mid-swing, kneeling
//   standing AS_Player_Female_Architecture2_Start frame 0 — upright, tool up
// The player meshes and the equipment meshes MUST come from the same stance or
// the armour will not line up with the head and hair, so one switch moves both.
const STANCE=process.env.STANCE==='standing'?'standing':'kneel';
// WILDPVP=1 keeps the PvP-arena spawn points in the wild-Pal draw. Off by
// default — see the PVP note in the wild-Pal block.
const WILDPVP=process.env.WILDPVP==='1';
// UNION_DIR is the union data this run reads FROM DISK: the manifest, the base
// export that gets uploaded into the page, and endoflife_<b>.json. It defaults
// to the shared tree so unset behaviour is unchanged.
//
// It exists because of a trap that silently splits a render in half. The page
// fetches times_/builders_/demolitions_/terrain_/pals_ over HTTP from whatever
// the DEV SERVER's publicDir is, while these three come off the local
// filesystem. In a worktree those are two different directories, so a render can
// upload the SHARED union and then fetch the WORKTREE's terrain and lifetimes,
// and the two halves disagree with nothing on screen to say so. Point UNION_DIR
// at the same public/union the server is serving and the two halves are provably
// the same bytes.
const UNION_DIR=process.env.UNION_DIR||`${APP}/public/union`;
const man=JSON.parse(fs.readFileSync(`${UNION_DIR}/manifest.json`));
const info=man[BASE];
const OUT=process.env.OUTDIR||`${SP}/frames/${BASE}`; fs.mkdirSync(OUT,{recursive:true});
// objects.json is MapPal's own type registry; we use its `category` field as the
// authority for what a piece IS, rather than inventing a taxonomy here.
const REG=JSON.parse(fs.readFileSync(`${APP}/src/data/objects.json`)).types;
// ts -> [RealDateTimeTicks, GameDateTimeTicks] read straight out of
// worldSaveData.GameTimeSaveData in each snapshot's Level.sav (extract_gametime.py).
const GT=JSON.parse(fs.readFileSync(`${SP}/gametime_index.json`));
// GameDateTimeTicks are .NET-style 100 ns ticks, so 864e9 ticks = 1 in-game day.
const GSEC_RAW=t=>{const g=GT[String(t)]; return g?g[1]/1e7:null;};  // ts -> in-game seconds
// A handful of snapshots have no entry in the index. Previously ANY such gap
// switched in-game pacing off for the whole base, which is what left Lost Camp
// and Wooden Camp with no pacing at all (3 and 6 of their snapshots
// respectively). That is far too blunt: the world clock runs at a measured,
// constant 45.000 in-game seconds per real second, so a snapshot's in-game time
// can be recovered from its real timestamp and its two nearest measured
// neighbours by straight linear interpolation. That is arithmetic on real data,
// not an invented reading — and it is marked as interpolated in the audit trail.
// (The underlying cause of the gap is separate and fixed at source:
// extract_gametime.py globbed nas/nasbk/nasbk2 but not nasbk3, so six saves that
// exist on disk were never read. Re-running it removes the need for any
// interpolation here; this stays as the safety net.)
function fillGameTime(tsList){
  const g=tsList.map(GSEC_RAW);
  const known=[]; for(let i=0;i<g.length;i++) if(g[i]!==null) known.push(i);
  if(!known.length) return {g,filled:0,ok:false};
  let filled=0;
  for(let i=0;i<g.length;i++){
    if(g[i]!==null) continue;
    // nearest measured neighbour on each side, in REAL time
    let lo=null,hi=null;
    for(const k of known){ if(k<i) lo=k; else { hi=k; break; } }
    if(lo!==null&&hi!==null){
      const f=(tsList[i]-tsList[lo])/(tsList[hi]-tsList[lo]);
      g[i]=g[lo]+(g[hi]-g[lo])*f;
    } else {
      // outside the measured range: extend at the world's own verified rate
      const k=(lo!==null?lo:hi);
      g[i]=g[k]+(tsList[i]-tsList[k])*45;
    }
    filled++;
  }
  return {g,filled,ok:true};
}
const browser=await puppeteer.launch({executablePath:CHROME,headless:HEADLESS?'new':false,
  // Several agents render out of this one workspace, and two Chromes cannot
  // share a user-data dir: the second either fails to launch or attaches to the
  // first. CHROME_PROFILE=<tag> gives a concurrent run its own profile.
  userDataDir:SP+'/chrome-profile'+(process.env.CHROME_PROFILE?'-'+process.env.CHROME_PROFILE:''),
  args:['--enable-unsafe-swiftshader','--use-angle=metal','--window-size=1600,1000','--no-first-run']});
const page=await browser.newPage();
await page.setViewport({width:1600,height:1000,deviceScaleFactor:1});
page.on('pageerror',e=>console.log('PAGEERR',e.message));
// PORT=<n> renders against a different dev server. This matters for more than
// convenience: vite builds its public/ file listing at STARTUP, so a server that
// was started before public/pal_meshes/tex was populated serves every Pal
// texture as the SPA fallback (200 text/html) instead of the image. The GLB
// still loads, its baseColorTexture silently resolves to nothing, and every Pal
// renders as the flat #cbd5e1 fallback colour — i.e. an unidentifiable grey
// blob. If Pals come out grey, restart the dev server before suspecting the
// material code.
await page.goto(`http://127.0.0.1:${process.env.PORT||5174}/`,{
  waitUntil:'networkidle2',
  timeout:120000,
});
const inp=await page.waitForSelector('input[type=file]',{timeout:20000});
await inp.uploadFile(`${UNION_DIR}/union_${BASE}.json`);
await page.waitForFunction(()=>!!window.__mappalCam&&!!window.__mappalCam.setObjects,{timeout:40000});
await new Promise(r=>setTimeout(r,3000));
await page.evaluate(()=>{const cv=document.querySelector('canvas');
  document.querySelectorAll('body *').forEach(el=>{if(el!==cv&&!el.contains(cv))el.style.display='none';});
  const st=document.createElement('style');
  st.textContent='canvas{position:fixed!important;inset:0!important;width:100vw!important;height:100vh!important}'+
   'body,html{overflow:hidden!important;margin:0!important;background:#0b0f14!important}';
  document.head.appendChild(st);window.dispatchEvent(new Event('resize'));});

// defines window.__reconstructBuildOrder — see buildorder.js for what is real
// data and what is reconstructed.
// BUILDORDER=<path> swaps in an older revision, so two renders can be compared
// with the ordering as the only variable.
await page.evaluate(fs.readFileSync(process.env.BUILDORDER||`${TOOL_DIR}/buildorder.js`,'utf8'));

const setup=await page.evaluate(async(b,reg,actors,stance,wildPvp)=>{
  const times=await (await fetch(`/union/times_${b}.json`)).json();
  // ---- Pals and players: real recorded per-snapshot data ------------------
  // pals_<b>.json  : each Pal's own LastJumpedLocation, sampled at every
  //                  snapshot where it changed, filtered to the samples inside
  //                  this base's own recorded area_range (build_actor_scenes.py).
  // players_<b>.json: each player's recorded LastTransform + the appearance
  //                  their Players/<uid>.sav had at that time, as runs.
  const grab=async u=>{try{const r=await fetch(u); return r.ok?await r.json():null;}catch(e){return null;}};
  const palData=actors?await grab(`/union/pals_${b}.json`):null;
  // terrain_<b>.json: the game's own placed cliffs/rocks/ruins around this base,
  // read out of the client pak's cooked World Partition cells. Static for the
  // whole render — the pak is one build of the game, not a per-snapshot record —
  // so it is set once here rather than per frame.
  const terrData=actors?await grab(`/union/terrain_${b}.json`):null;
  window.__terrain=(terrData&&terrData.props)||[];
  const playerData=actors?await grab(`/union/players_${b}.json`):null;
  window.__pals=(palData&&palData.pals)||[];
  window.__players=(playerData&&playerData.players)||[];
  // ---- WILD PALS ------------------------------------------------------------
  // The game's OWN spawn tables for the ground around this base, read out of the
  // cooked World Partition cells: where the wild-Pal spawners stand and which
  // species each one may roll. NOT recorded positions of individual Pals — the
  // save has none for wild Pals — and the file says so in its own `note`.
  //
  // WHY THE _draw_ FILE AND NOT THE RAW `wild` ARRAY. Entries sharing a
  // spawnerId are the mutually exclusive weighted ALTERNATIVES of one spawner:
  // the game rolls ONE group per spawn. Drawing the raw array stacks every
  // alternative on the same point — about 8-13 Pals standing inside each other
  // at every spawn point. wildpals_draw_<b>.json is the resolved list: one group
  // per spawner, chosen weight-proportionally by a documented deterministic hash
  // of the spawner id (no RNG, no wall clock, byte-identical on re-run), already
  // expanded to one record per Pal instance.
  //
  // DAY AND NIGHT ARE SEPARATE LISTS because the tables themselves are: a group
  // whose OnlyTime is Night is eligible only at night, Undefined always. The
  // renderer already knows the world's own in-game hour, so it picks the list
  // that matches instead of showing one of them all the time. Threshold is
  // DayNightLights' own SUNRISE/SUNSET (5 and 19) so the Pals change over at the
  // same moment the light does.
  //
  // PVP. 56 of Glass Tower's 87 spawn points belong to a PvP arena 328-596 m
  // out — a fixed piece of world content that has nothing to do with this camp,
  // and at Glass Tower it is 82 of the 139 instances, i.e. more than half of
  // everything drawn. They are filtered out by default; WILDPVP=1 puts them
  // back. The file lists the spawner ids so the decision is reversible and
  // auditable rather than baked into the data.
  const wdDoc=actors?await grab(`/union/wildpals_draw_${b}.json`):null;
  {
    const pvp=new Set((wdDoc&&wdDoc.pvpSpawnerIds)||[]);
    const keep=e=>wildPvp||!pvp.has(e.spawnerId);
    // drawX/drawY/drawZ, not x/y/z: where a spawner rolls more than one Pal the
    // producing pass spread the instances with a deterministic offset rather
    // than stacking them on the point. For a single-instance group the two are
    // identical (dx=dy=0).
    const mk=e=>({id:e.id,char:e.char,url:e.url,x:e.drawX,y:e.drawY,z:e.drawZ});
    window.__wildDay=((wdDoc&&wdDoc.day)||[]).filter(keep).map(mk);
    window.__wildNight=((wdDoc&&wdDoc.night)||[]).filter(keep).map(mk);
    window.__wildStats={spawners:(wdDoc&&wdDoc.spawners)||0,
      dayAll:((wdDoc&&wdDoc.day)||[]).length,nightAll:((wdDoc&&wdDoc.night)||[]).length,
      day:window.__wildDay.length,night:window.__wildNight.length,
      pvpSpawners:pvp.size,pvpIncluded:wildPvp,
      species:new Set(window.__wildDay.concat(window.__wildNight).map(p=>p.char)).size};
  }
  // hour===null means day/night lighting is off for this run; show the day list
  // rather than inventing a time of day.
  window.__wildAt=(hour)=>{
    if(hour===null||hour===undefined) return window.__wildDay;
    const h=((hour%24)+24)%24;
    return (h<5||h>=19)?window.__wildNight:window.__wildDay;
  };
  // A Pal is drawn at time t at its LATEST RECORDED sample at or before t, and
  // only while t lies inside the window the save actually held a record for it.
  // Nothing is interpolated between samples and nothing is invented outside the
  // window — a Pal with no sample yet is simply absent.
  window.__palsAt=(t)=>{const out=[];
    for(const p of window.__pals){
      if(t<p.first||t>p.last)continue;
      let s=null; for(const k of p.track){ if(k[0]<=t)s=k; else break; }
      if(!s)continue;
      out.push({id:p.id,char:p.char,url:p.url,x:s[1],y:s[2],z:s[3]});
    }
    return out;};
  window.__playersAt=(t)=>{const out=[];
    for(const pl of window.__players){
      const r=pl.runs.find(r=>t>=r.from&&t<=r.to);
      if(!r||!r.parts.length)continue;
      out.push({uid:pl.uid,parts:window.__dress?window.__dress(pl.uid,t,r.parts):r.parts,
                x:r.x,y:r.y,z:r.z,qx:r.qx,qy:r.qy,qz:r.qz,qw:r.qw});
    }
    return out;};
  // ---- TEMPORAL PAINT ------------------------------------------------------
  // The union blueprint carries ONE snapshot's Paint blob per object (the
  // snapshot the object was first seen in), so using it directly would show a
  // piece already painted from the instant it is placed — inventing history the
  // same way dumping every pre-existing piece at frame 0 would. paint_index.json
  // (paint_index.py) walks all ~1,200 surviving snapshots and records, per
  // object, the timestamps at which its paint actually CHANGED. Resolution is
  // the snapshot cadence, and a null colour means "observed unpainted".
  let PAINT={};
  try { PAINT=(await (await fetch('/union/paint_index.json')).json()).changes||{}; }
  catch(e){ console.log('no paint_index.json — paint will not be shown'); }
  window.__paint=PAINT;
  const sameP=(p,c)=>!!p&&!!c&&p.r===c[0]&&p.g===c[1]&&p.b===c[2]&&p.a===c[3];
  // Object AS OF time t: the last recorded paint change at or before t wins.
  // Returns the object itself (same reference) when nothing changes, so React
  // sees a stable identity for the overwhelming majority that are never painted.
  window.__withPaint=(o,t)=>{
    const seq=window.__paint[o.id]; if(!seq) return o;
    let col=null;
    for(let k=0;k<seq.length&&seq[k][0]<=t;k++) col=seq[k][1];
    if(!col) return o.paint?{...o,paint:undefined}:o;
    if(sameP(o.paint,col)) return o;
    return {...o,paint:{r:col[0],g:col[1],b:col[2],a:col[3]}};
  };
  const all=window.__mappalCam.getObjects();
  const S=0.01, P=all.map(o=>o.position), n=P.length;
  const cx=P.reduce((a,p)=>a+p.x,0)/n, cy=P.reduce((a,p)=>a+p.y,0)/n, cz=P.reduce((a,p)=>a+p.z,0)/n;
  // CommonDropItem3D is ground loot on a 1-hour despawn timer, not construction.
  // It IS rendered (the base should look lived-in) but is excluded from step
  // pacing and from camera framing, because its churn swamps the build signal.
  // Save-space (cm, z-up) -> scene units, the same recentring and 0.01 scale
  // every object above gets. Only the CLOSEUP diagnostic uses it: it lets a
  // check frame be aimed at the exact avatar the frame says it drew, instead of
  // at whichever __avatarPart the scene graph happens to yield first (which is
  // usually one of the recorded PLAYER-layer figures, not the builder — that is
  // precisely how an earlier check sheet ended up photographing the wrong
  // person).
  window.__toScene=(x,y,z)=>[(x-cx)*S,(z-cz)*S,(y-cy)*S];
  const LOOT='CommonDropItem3D';
  window.__all=all.map(o=>({o,t:times[o.id]||null,loot:o.typeId===LOOT,
    sx:(o.position.x-cx)*S, sy:(o.position.z-cz)*S, sz:(o.position.y-cy)*S}));
  // Objects already standing in the FIRST snapshot were built before the record
  // starts, so they carry no individual timestamps. Dumping them all at frame 0
  // makes a finished base appear out of nothing. Instead we reveal them in a
  // RECONSTRUCTED structural order: a real topological sort of the support
  // relation implied by the pieces' own positions on Palworld's 400/325 cm
  // lattice — a wall only after something it can stand on, a roof only after
  // the walls or pillars under it, decoration last. Pieces, types and positions
  // are all real; only the ORDER is reconstructed, because the real order of
  // this first snapshot was never recorded anywhere. See buildorder.js.
  const t0all=Math.min(...window.__all.filter(x=>x.t&&!x.loot).map(x=>x.t[0]));
  // A piece belongs to the reconstruction if it was already standing when the
  // record starts — INCLUDING one with no lifetime row at all. That second case
  // used to fall through the net: `__frame` draws an object with no `t` on every
  // frame unconditionally, and requiring `x.t` here kept those objects out of
  // the build order, so 53 pieces of Glass Tower were simply present from frame
  // 0 onward. That is what "it doesn't render from zero" looked like — the
  // opening frame showed a ring of glass panels and two wooden pillar stacks
  // that no avatar was ever shown placing. An object with no recorded lifetime
  // is precisely an object we cannot date, which is the same situation as every
  // other pre-existing piece, so it gets reconstructed with them rather than
  // being granted an exemption from the build-out.
  const pre=window.__all.filter(x=>!x.loot&&(!x.t||x.t[0]<=t0all+300));
  const stats={};
  const order=window.__reconstructBuildOrder(pre.map(x=>({
    id:x.o.id, x:x.o.position.x, y:x.o.position.y, z:x.o.position.z,
    typeId:x.o.typeId, category:(reg[x.o.typeId]||{}).category
  })),{stats});
  const rank=new Map(order.map((id,k)=>[id,k]));
  pre.forEach(x=>{x.pre=rank.get(x.o.id);});
  window.__orderStats=stats;
  // ---- BUILDER AVATAR -------------------------------------------------------
  // buildorder.js is documented as a pure function of its input, so index k of
  // `order` means the same piece on every run. That contract is what lets the
  // avatar follow the build: the piece revealed at a given preShown is
  // order[preShown-1], no matter how many pieces a frame reveals.
  //
  // PROVENANCE, because these two halves are NOT equally real:
  //   WHO   real. builders_<b>.json is each object's own `build_player_uid`,
  //         with the all-zero "no owner" sentinel already resolved to null on
  //         the FULL uid. A piece whose builder is null gets NO avatar rather
  //         than an attributed guess. (See __builderAt for why the short key
  //         "00000000" is a real player and not that sentinel.)
  //   WHERE a rendering choice. Palworld records no per-piece build position.
  //         (It does record LastTransform per snapshot — that is real, and the
  //         separate player layer above draws it verbatim — but it is a
  //         last-known/logout position, not where anyone stood when a given
  //         piece went down.) The rule here is "stand on what you just built,
  //         face what you are placing next": at piece k the avatar stands ON
  //         piece k-1 and yaws toward piece k. Deterministic and derived
  //         entirely from the emitted order, with no smoothing and no
  //         path-finding — consecutive pieces are usually adjacent, so the walk
  //         falls out of the real order. It is a well-motivated presentation of
  //         real attribution, NOT a claim about where anyone stood.
  window.__buildOrder=order;
  window.__posById=new Map(window.__all.map(x=>[x.o.id,x.o.position]));
  const bl=actors?await grab(`/union/builders_${b}.json`):null;
  window.__builders=(bl&&bl.builders)||{};
  // ---- CARRIED-FORWARD ATTRIBUTION -----------------------------------------
  // `build_player_uid` IS RECORDED DATA: builders_<b>.json is that field copied
  // out of each object's own save row, and where it is present the avatar drawn
  // is the player the save names. A piece where it is ABSENT used to draw
  // nobody, so a decoration or a workbench with no recorded owner simply
  // appeared out of thin air in the middle of a stretch someone was visibly
  // building.
  //
  // CARRYING THE LAST KNOWN BUILDER FORWARD IS A RENDERING CHOICE, not data. It
  // says: the person who was last seen building is still the one working. That
  // is a plausible reading of a continuous build session and it is the same kind
  // of inference as the standing rule and the demolition attribution — and like
  // those it is labelled, counted in the audit line, and separable from the
  // recorded half. The save does not say who placed these pieces.
  //
  // Two limits, both deliberate:
  //   * A piece that PRECEDES every known builder in the order carries null and
  //     still draws nobody. Guessing backwards from a later builder would be
  //     inventing history in the wrong direction.
  //   * "00000000" is a REAL player (mannbhai83, host slot), already resolved
  //     from the all-zero sentinel upstream — see __builderAt. Only null means
  //     "no recorded builder".
  window.__carriedBuilder=(()=>{
    const out=new Array(order.length).fill(null); let last=null;
    for(let k=0;k<order.length;k++){ const u=window.__builders[order[k]]; if(u) last=u; out[k]=last; }
    return out;
  })();
  const av=actors?await grab('/union/avatars.json'):null;
  window.__avatars=(av&&av.players)||{};
  // ---- WHAT THEY WERE ACTUALLY WEARING --------------------------------------
  // The complaint this answers is "wearing default outfit no equipment", and the
  // cause was narrow and specific: avatars.json resolves a look from
  // PlayerCharacterMakeData's BodyMeshName, which is the player's naked BODY
  // TYPE, not their armour. For Kat that happened to come out right, because her
  // save also carries a mirror APPEARANCE OVERRIDE and the resolver honours it.
  // For Mann and TALLYODA it did not: their override is null, so the body mesh
  // fell back to SK_Player_Male_Outfit_OldCloth001 — which is literally the
  // TypeB default. They were rendering as the default because the equipped
  // armour was never consulted.
  //
  // equipment_<b>.json is the equipped armour, read verbatim out of Level.sav's
  // ItemContainerSaveData for the container Guids Players/<UID>.sav names. This
  // block joins the two.
  //
  // PRECEDENCE. It is not "armour wins"; it is the game's own rule, and getting
  // it backwards would put Kat in the wrong outfit for the last five days of the
  // history:
  //   1. OverrideEquipmentInfo.Body (the mirror appearance swap) if a snapshot
  //      RECORDS one — the game shows that armour instead of what is worn. Kat's
  //      override reads CopperArmorCold from 16 Aug on while she is actually
  //      wearing PlasticArmorWeight, then PlasticArmorCold, then SFArmor; the
  //      game shows Bronze001_v03 for all of it, and so do we.
  //   2. otherwise the armour actually EQUIPPED in PlayerEquipArmorContainerId.
  //   3. otherwise the base body mesh from avatars.json — which is the right
  //      answer for mannbhai83, whose containers are empty in all 1,171
  //      snapshots. That is a recorded fact, not a lookup failure, so he is
  //      drawn plainly and no gear is invented for him.
  // OverrideEquipmentInfo.Head is the same rule one slot over, and `Naked_Head`
  // is a real value meaning "show no headgear" — it has no row in the mesh
  // preset table, which is why Kat wears no helmet on screen despite carrying
  // PlasticHelmet.
  //
  // WHEN THE OVERRIDE IS NOT RECORDED. Some snapshots predate the surviving
  // Players/<uid>.sav window and state nothing about the mirror at all
  // (appearanceOverrideSource: "unknown"). We then draw the EQUIPPED armour,
  // which is contemporaneously recorded, and count those frames separately in
  // the audit line rather than pretending the override state is known.
  //
  // THE TRAP, and it is the reason this block exists at all rather than just
  // swapping a URL: public/equipment_meshes/ is BIND POSE. Dropping one of those
  // in place of a posed body part would put the avatar straight back in a T-pose
  // — the exact bug the pose bake fixed. Every mesh referenced here comes from
  // public/equipment_meshes_posed/, re-exported by palxtex with the SAME
  // animation frame the player meshes were baked with
  // (AS_Player_Female_Architecture2 frame 31), which works because all
  // SK_Player_*_Outfit_* share SK_PalHuman_Skeleton: the extraction report shows
  // 73-99 of 73-99 bones matched and 100% of vertices skinned on every one.
  //
  // NOT DRAWN, deliberately: gliders and weapons. Both come out of the save with
  // attach "socketUnknown" — no cooked asset names the socket a held weapon or a
  // stowed glider uses (it is set in native C++), and the save records the
  // 6-slot loadout but never which slot was in the player's hands. Drawing one
  // would assert two things the record does not contain. See the WEAPONS note in
  // the audit output.
  const eqDoc=actors?await grab(`/union/equipment_${b}.json`):null;
  window.__equip={};
  for(const p of ((eqDoc&&eqDoc.players)||[])) window.__equip[p.uid]={name:p.name,runs:p.runs||[]};
  // Meshes for the ids that only ever appear as a mirror OVERRIDE, resolved
  // through the same shipped preset table (build_override_meshes.py). An id with
  // no row resolves to nothing and is never substituted.
  const ovDoc=actors?await grab('/union/equipment_override_meshes.json'):null;
  window.__ovItems=(ovDoc&&ovDoc.items)||{};
  // Attach sockets composed onto the BAKED animation frame, not the bind pose —
  // see build_posed_sockets.py. Only the rigid props need these.
  const skDoc=actors?await grab('/union/equipment_sockets_posed.json'):null;
  window.__eqSockets=(skDoc&&skDoc.sockets)||{};
  window.__eqStance=stance;
  const EQDIR='/equipment_meshes_posed'+(stance==='standing'?'_standing':'')+'/';
  // vite builds its public/ listing at STARTUP, so a server started before this
  // directory existed serves every GLB in it as the SPA fallback (200
  // text/html). three would then fail to parse it and the armour would silently
  // vanish. Check once, loudly — a run that cannot serve posed armour must say
  // so rather than quietly falling back to a bind-pose T-pose.
  window.__eqDirOk=await (async()=>{
    try{ const r=await fetch(EQDIR+'SK_Player_Male_Outfit_Plastic001_v03.glb',{method:'HEAD'});
         return r.ok&&(r.headers.get('content-type')||'').includes('gltf'); }catch(e){ return false; }
  })();
  const eqUrl=m=>EQDIR+m+'.glb';
  window.__eqStats={calls:0,noRun:0,inexactRun:0,overrideBody:0,equippedBody:0,baseBody:0,
                    overrideUnrecorded:0,headSkeleton:0,headSocket:0,headHiddenByOverride:0,
                    headSocketUnplaceable:0,noGlb:{}};
  /** The equipment run covering t, or the nearest one flagged inexact. */
  window.__eqRunAt=(uid,t)=>{
    const p=window.__equip[uid]; if(!p||!p.runs.length) return null;
    const hit=p.runs.find(r=>t>=r.from&&t<=r.to);
    if(hit) return {run:hit,exact:true};
    const near=t<p.runs[0].from?p.runs[0]:p.runs[p.runs.length-1];
    return {run:near,exact:false};
  };
  /**
   * Body mesh + head equipment for one player at one moment, as URLs into the
   * POSED equipment directory. Returns null for a slot the record leaves empty —
   * never a stand-in.
   */
  window.__eqLook=(uid,t)=>{
    const hit=window.__eqRunAt(uid,t); if(!hit) return null;
    const r=hit.run, ov=r.appearanceOverride||{}, bt=r.bodyType||'TypeA';
    const out={exact:hit.exact,overrideSource:r.appearanceOverrideSource,
               bodyType:bt,bodyTypeSource:r.bodyTypeSource,body:null,bodySource:null,head:null};
    const ovMesh=(id)=>{
      const it=window.__ovItems[id]; const m=it&&it.meshes&&it.meshes[bt];
      if(m&&m.haveGlb) return {url:EQDIR+m.mesh+'.glb',mesh:m.mesh};
      if(!it||it.unresolved) return null;                 // e.g. Naked_Head: no row, show nothing
      const k=id+'/'+bt; window.__eqStats.noGlb[k]=(window.__eqStats.noGlb[k]||0)+1;
      return null;
    };
    // --- body: override beats equipped beats the base body mesh ---
    if(ov.body&&ov.body!=='None'){
      const m=ovMesh(ov.body);
      if(m){out.body=m.url; out.bodySource='override:'+ov.body;}
    }
    if(!out.body){
      const e=(r.equipped||[]).find(x=>x.attach==='bodyReplace');
      if(e){out.body=eqUrl(e.mesh); out.bodySource='equipped:'+e.itemId
              +(r.appearanceOverrideSource==='unknown'?' (mirror override not recorded here)':'');}
    }
    // --- head equipment: same precedence, one slot over ---
    if(ov.head&&ov.head!=='None'){
      const m=ovMesh(ov.head);
      if(m) out.head={url:m.url,source:'override:'+ov.head};
      else out.head=null;                                  // Naked_Head -> bare head
    } else {
      const e=(r.equipped||[]).find(x=>x.slot==='head');
      if(e&&e.attach==='skeleton'){
        // Authored in character space and skinned to the player skeleton (82
        // matched bones), so the pose bake already moved it: draw at the
        // character origin with no socket transform.
        out.head={url:eqUrl(e.mesh),source:'equipped:'+e.itemId};
      } else if(e&&e.attach==='socket'&&e.socket){
        // A rigid prop in socket-local space. Needs the POSED socket transform.
        const s=window.__eqSockets[e.socket], st=s&&s[window.__eqStance];
        if(st) out.head={url:eqUrl(e.mesh),source:'equipped:'+e.itemId,
                         attach:{t:st.t,q:st.q,socket:e.socket}};
        else out.head={unplaceable:e.itemId};
      }
    }
    return out;
  };
  // Part lists are rebuilt every frame; memoising per (uid, run, base look)
  // keeps object identity stable inside a run so React is not handed a fresh
  // array for a character that has not changed.
  const dressCache=new Map();
  /**
   * avatars.json parts -> the same parts wearing what the save says they wore.
   *
   * The counters are tallied from the cached CLASSIFICATION on every call, not
   * only on a cache miss, so `equipment provenance over N avatar draws` really
   * is per draw. (Incrementing inside the miss branch is an easy mistake and
   * makes the audit line read 1 where it should read thousands.)
   */
  window.__dress=(uid,t,parts)=>{
    if(!parts||!parts.length) return parts;
    const hit=window.__eqRunAt(uid,t);
    const S=window.__eqStats;
    S.calls++;
    if(!hit){S.noRun++; return parts;}
    if(!hit.exact) S.inexactRun++;
    const ck=uid+'|'+hit.run.from+'|'+parts.map(p=>p.url).join(',');
    let c=dressCache.get(ck);
    if(!c){
      const L=window.__eqLook(uid,t);
      const out=[]; const cls={body:'base',head:'none'};
      let swapped=false;
      for(const p of parts){
        // The outfit slot is the only one armour replaces; heads and hair stay.
        if(L&&L.body&&/_Outfit_/.test(p.url)&&!swapped){out.push({url:L.body,color:p.color});swapped=true;}
        else out.push(p);
      }
      if(swapped) cls.body=L.bodySource.startsWith('override')?'override'
                    :(L.overrideSource==='unknown'?'equippedOverrideUnknown':'equipped');
      if(L&&L.head){
        if(L.head.unplaceable) cls.head='unplaceable';
        else { out.push({url:L.head.url,attach:L.head.attach});
               cls.head=L.head.attach?'socket':'skeleton'; }
      } else if(L){
        const ov=hit.run.appearanceOverride||{};
        if(ov.head&&ov.head!=='None') cls.head='hiddenByOverride';
      }
      c={parts:out,cls};
      dressCache.set(ck,c);
    }
    const k=c.cls;
    if(k.body==='override') S.overrideBody++;
    else if(k.body==='equipped') S.equippedBody++;
    else if(k.body==='equippedOverrideUnknown'){S.equippedBody++;S.overrideUnrecorded++;}
    else S.baseBody++;
    if(k.head==='socket') S.headSocket++;
    else if(k.head==='skeleton') S.headSkeleton++;
    else if(k.head==='hiddenByOverride') S.headHiddenByOverride++;
    else if(k.head==='unplaceable') S.headSocketUnplaceable++;
    return c.parts;
  };
  // ---- POSED PLAYER MESHES --------------------------------------------------
  // public/player_meshes/ is the character in BIND POSE — arms straight out,
  // because the exporter that produced it wrote a flat single-node mesh. A
  // T-posed builder is honest but reads as a mannequin, so a separate pass
  // re-exported the same 11 meshes with ONE frame sampled from the game's own
  // build animation baked into the vertices, into public/player_meshes_posed/.
  // Same assets, same vertex counts, a real recorded pose rather than an
  // authored one — nothing here is hand-posed.
  //
  // The swap is per-URL and gated on the file actually being there, so a mesh
  // the posed pass has not reached yet quietly keeps its bind-pose version
  // instead of 404ing into a suspended layer that draws nobody.
  //
  // It fixes a second thing by accident, which is worth recording because it
  // looked like a renderer bug: SK_Player_Female_Outfit_OldCloth001 in the
  // ORIGINAL directory has zero materials and zero images, so mannbhai83's
  // avatar rendered as a flat white figure (the no-texture fallback colour).
  // The posed re-export of that same mesh carries all four textured materials.
  {
    const posed=new Map();
    const tryPosed=async u=>{
      if(!u.startsWith('/player_meshes/')) return u;
      const alt=u.replace('/player_meshes/',stance==='standing'?'/player_meshes_posed_standing/':'/player_meshes_posed/');
      if(!posed.has(alt)){
        let ok=false;
        try{ const r=await fetch(alt,{method:'HEAD'});
             ok=r.ok&&(r.headers.get('content-type')||'').includes('gltf'); }catch(e){ ok=false; }
        posed.set(alt,ok);
      }
      return posed.get(alt)?alt:u;
    };
    let swapped=0,kept=0;
    for(const e of Object.values(window.__avatars))
      for(const r of (Array.isArray(e)?e:(e.runs||[])))
        for(const p of r.parts){ const u=await tryPosed(p.url);
          if(u!==p.url){p.url=u;swapped++;} else kept++; }
    window.__posedStats={swapped,kept};
  }
  // demolitions_<b>.json: pieces that vanished and were superseded in place, each
  // paired with the replacement that took the spot and the uid to credit.
  // Produced by a separate pass; read verbatim here, never recomputed. It covers
  // only the removals it could PAIR with a replacement — 15 of the 112 across the
  // four bases — so it is used for ATTRIBUTION only; the removal events
  // themselves come from the lifetime rows. See __changePose for the provenance
  // limit, which is severe and must not be smoothed over: the save records that a
  // piece was there and later was not. It records NO destruction event, no
  // demolisher, and no time of demolition.
  const dm=actors?await grab(`/union/demolitions_${b}.json`):null;
  window.__demolitions=(dm&&dm.demolitions)||[];
  // Removal attribution lookup used by __changeUid below.  This was formerly
  // read as __demoPairs without ever constructing the Map, which crashed every
  // actor-enabled render as soon as it audited a removal.
  window.__demoPairs=new Map(window.__demolitions
    .filter(d=>d.removedId&&d.attributedUid)
    .map(d=>[d.removedId,d.attributedUid]));
  // Parse every avatar mesh before frame 0. An un-cached useGLTF suspends, and
  // a suspension blanks the whole canvas for that frame — three build-out
  // frames came out as flat background PNGs before this was added.
  // ---- WARDROBE SUMMARY -----------------------------------------------------
  // The effective outfit per player over time, collapsed to the moments it
  // actually CHANGES, computed from the same __eqLook the frames use. This is
  // the audit trail for the precedence rule: it shows, per span, whether the
  // body on screen came from the mirror override, from the equipped armour, or
  // from the base body mesh, and names the item either way.
  window.__wardrobe=(()=>{
    const out=[];
    for(const [uid,p] of Object.entries(window.__equip||{})){
      let prev=null;
      for(const r of p.runs){
        const L=window.__eqLook(uid,r.from); if(!L) continue;
        const key=(L.bodySource||'base')+'|'+(L.head?(L.head.source||'?'):'-');
        if(key!==prev){
          out.push({uid,name:p.name,from:r.from,to:r.to,
                    body:L.bodySource||'base body mesh (no armour recorded)',
                    bodyMesh:L.body?L.body.split('/').pop():null,
                    head:L.head?(L.head.unplaceable?('UNPLACEABLE '+L.head.unplaceable):L.head.source):'none',
                    headMesh:L.head&&L.head.url?L.head.url.split('/').pop():null,
                    overrideSource:L.overrideSource,bodyType:L.bodyType});
          prev=key;
        } else if(out.length) out[out.length-1].to=r.to;
      }
    }
    return out;
  })();
  if(window.__mappalCam.preloadPlayers){
    const urls=new Set();
    for(const e of Object.values(window.__avatars))
      for(const r of (Array.isArray(e)?e:(e.runs||[])))
        for(const p of r.parts) urls.add(p.url);
    // Every armour / head-equip GLB any run of this base can resolve to. An
    // un-cached useGLTF suspends and a suspension blanks the whole canvas for a
    // frame, and the armour changes far more often than the base look does — 16
    // gear changes for Kat alone — so these matter more here than the base parts.
    let eqUrls=0;
    for(const w of window.__wardrobe){
      const L=window.__eqLook(w.uid,w.from); if(!L) continue;
      if(L.body){urls.add(L.body);eqUrls++;}
      if(L.head&&L.head.url){urls.add(L.head.url);eqUrls++;}
    }
    window.__equipMeshCount=eqUrls;
    window.__mappalCam.preloadPlayers([...urls]);
    window.__avatarMeshCount=urls.size;
  }
  const EYE=90;         // player capsule half-height, so the mesh's feet land on standZ
  // Where the avatar's FEET go when it stands on a given piece, from that
  // piece's own registered extents (objects.json `size` + `originAtTop`), not a
  // fixed offset. A broad slab (foundation/floor/roof) is stood ON — feet on its
  // upper surface. A thin vertical piece (wall, pillar) has no walkable top, so
  // the avatar stands at its FOOT, i.e. the level that piece rises from.
  //
  // CLIMBING A VERTICAL RUN. The plain rule above puts the avatar at the FOOT of
  // a thin piece, which is right for a wall you reach up to place from the floor
  // and wrong for a column: a tower goes up one 325 cm segment at a time, and
  // parking the builder on the ground while segments appear overhead reads as
  // nobody building them. So when the piece being placed next sits on
  // essentially the same footprint as the one just finished but a storey higher,
  // the avatar stands on TOP of the finished segment and climbs with the work.
  //
  // Both thresholds are the base's own measured lattice, not taste:
  // docs/CALIBRATION.md measures the horizontal snap at 400 cm and the vertical
  // segment at 325 cm (a pillar stack at z = 7139.69 -> 7464.69 -> 7789.69).
  // "Same footprint" is therefore within half a grid tile, and "a storey higher"
  // is at least half a segment above the base of the piece being stood on.
  //
  // It is still a RENDERING CHOICE, on exactly the same footing as the standing
  // rule it extends. The save records that these pieces exist and who is
  // credited with them; it records nothing about anyone climbing. And note the
  // separate limit the coordinator's own investigation established: the
  // extracted GLBs carry NO skin weights (skins=0 on all 11 player meshes,
  // because the exporter drops Influences and writes one flat node), so there is
  // no skeleton to pose. The avatar therefore ASCENDS — its position tracks the
  // top of the column as the column grows — and never pretends to a climbing
  // animation. An invented pose would be a fabrication; an honest position is
  // not.
  const GRID_PITCH=400, VPITCH=325;   // docs/CALIBRATION.md, measured
  window.__standZ=(o,next)=>{
    const r=reg[o.typeId]||{}, s=r.size;
    if(!s) return o.position.z;
    const top=r.originAtTop?o.position.z:o.position.z+s[2];
    const bot=r.originAtTop?o.position.z-s[2]:o.position.z;
    if(s[0]>=200&&s[1]>=200) return top;          // broad slab: stand on it
    if(next){
      const dxy=Math.hypot(next.position.x-o.position.x,next.position.y-o.position.y);
      if(dxy<=GRID_PITCH/2 && next.position.z>bot+VPITCH/2) return top;
    }
    return bot;                                    // thin piece: stand at its foot
  };
  // avatars.json entries are {name, runs} — build_names.py folds each player's
  // roster name in beside the appearance runs build_avatars.py emits. Older
  // revisions of that file stored the run ARRAY directly, so both shapes are
  // accepted; reading the object shape as an array is exactly the bug that kept
  // every builder avatar invisible (`{}.length` is undefined, so every lookup
  // returned null and no mesh was ever resolved).
  window.__avatarEntry=(uid)=>{
    const e=window.__avatars[uid];
    if(!e) return null;
    return Array.isArray(e)?{name:null,runs:e}:{name:e.name??null,runs:e.runs||[]};
  };
  window.__avatarLook=(uid,t)=>{
    const e=window.__avatarEntry(uid); if(!e||!e.runs.length) return null;
    const runs=e.runs;
    const hit=runs.find(r=>t>=r.from&&t<=r.to);
    // __dress replaces the OUTFIT part with the armour the save says was worn
    // (or the mirror override that beats it) and appends any head equipment.
    // The base head/hair parts are untouched.
    if(hit) return {parts:window.__dress(uid,t,hit.parts),name:e.name,exact:true};
    // Outside the surviving Players/<uid>.sav window. Every one of these players
    // has a SINGLE appearance run across all 1,137 snapshots (d87864f3 has two),
    // i.e. the save bytes never changed, so the nearest recorded look is used
    // and flagged inexact rather than invented or skipped.
    const near=t<runs[0].from?runs[0]:runs[runs.length-1];
    return {parts:window.__dress(uid,t,near.parts),name:e.name,exact:false};
  };
  window.__objById=new Map(window.__all.map(x=>[x.o.id,x.o]));
  window.__builderAt=(preShown,t)=>{
    const O=window.__buildOrder;
    if(!(preShown>0&&preShown<=O.length)) return null;
    const id=O[preShown-1];                       // the piece being placed NOW
    if(!id) return null;
    const rec=window.__builders[id]||null;
    // Credit follows the piece being placed where the save records one. Where it
    // does not, the LAST KNOWN builder in the order is carried forward and shown
    // placing this piece — an inference, flagged as `carried` on the returned
    // object and counted separately in the audit line. See __carriedBuilder.
    // Before any known builder there is nothing to carry, so uid stays null and
    // nobody is drawn.
    //
    // The short key "00000000" is NOT that sentinel. Counted straight off the
    // union map_objects, two different full uids share those eight characters:
    //   00000000-0000-0000-0000-000000000000  x3163  the real "no owner" value
    //   00000000-0000-0000-0000-000000000001   x304  a real player, 'mannbhai83'
    // build_actor_scenes.py tests the FULL uid and emits null for the sentinel
    // before truncating, so by the time a uid reaches this file "00000000" can
    // only be that real player. Skipping it here (as an earlier revision did)
    // silently dropped 293 genuinely attributed pieces.
    const uid=rec||window.__carriedBuilder[preShown-1]||null;
    if(!uid) return null;
    const look=window.__avatarLook(uid,t); if(!look) return null;
    const target=window.__objById.get(id); if(!target) return null;
    // Stand on the piece just finished (k-1). At k=1 nothing has been built yet,
    // so the avatar stands on the piece it is placing rather than being given an
    // invented starting position elsewhere.
    const prev=preShown>1?window.__objById.get(O[preShown-2]):null;
    const stand=prev||target;
    // `target` is passed so __standZ can spot a vertical run and put the avatar
    // on top of the segment just finished rather than back down at its foot.
    const sx=stand.position.x, sy=stand.position.y, sz=window.__standZ(stand,target);
    // Yaw only — face the piece being placed, never tilted up or down at it.
    let dx=target.position.x-sx, dy=target.position.y-sy;
    let yaw;
    if(Math.hypot(dx,dy)<1e-3){
      // Standing on the very piece being placed (k=1, or a piece stacked exactly
      // on the previous one): face the piece AFTER this one if there is one.
      const nxt=preShown<O.length?window.__objById.get(O[preShown]):null;
      yaw=nxt?Math.atan2(nxt.position.y-sy,nxt.position.x-sx):0;
    } else yaw=Math.atan2(dy,dx);
    // name is the player's own roster name (GroupSaveDataMap) or null — a player
    // the rosters do not name renders with no tag rather than an invented one.
    return {uid,name:look.name,parts:look.parts,appearanceExact:look.exact,
      attribution:rec?'recorded':'carried',
      x:sx, y:sy, z:sz+EYE,
      qx:0,qy:0,qz:Math.sin(yaw/2),qw:Math.cos(yaw/2)};
  };
  /**
   * The DEMOLITION avatar: the player credited with the piece that replaced a
   * vanished one, shown beside the old piece across the window in which it
   * disappeared.
   *
   * PROVENANCE, and it is weaker than the build attribution — do not let the two
   * blur together. `build_player_uid` on a piece that EXISTS is recorded fact.
   * A demolition is not recorded at all. What the history actually contains is:
   * a piece is present in one snapshot and absent in a later one, and some other
   * piece now occupies substantially the same volume. From that, the pairing
   * pass infers "this was torn down to make room for that" and credits the
   * REPLACEMENT's recorded builder. Every link in that is inference: that the
   * piece was demolished rather than despawned, that the two events are related,
   * that the person credited with the new piece is the person who removed the
   * old one. None of it is read out of the save.
   *
   * The TIME is inferred too. This save carries no placement timestamps
   * anywhere; removedAt is the last snapshot the piece was seen in and
   * replacedAt the first the replacement was seen in, so the removal happened at
   * an unknown moment somewhere between them. The avatar is therefore shown
   * across that whole window rather than at a pretended instant — and
   * `gapSeconds` in the file says how wide the window is (24 minutes at Glass
   * Tower, up to days at the quieter bases, because the snapshot cadence is
   * wildly irregular).
   *
   * There is likewise no destruction POSE: the extracted player GLBs carry no
   * skin weights, so there is no skeleton to animate and inventing one is out of
   * the question. The avatar stands at the piece and faces it. That is the
   * honest reading of "who is credited with this spot changing hands".
   */
  window.__demoAt=(preShown,t)=>{
    // Build-out is a reconstruction at a single held instant, not a walk through
    // real time, so a window expressed in real timestamps only means anything
    // once the replay is running on the actual snapshot clock.
    if(preShown<window.__preCount) return null;
    for(const d of window.__demolitions){
      if(!d.attributedUid) continue;               // no credited player -> no avatar
      if(!(t>=d.removedAt&&t<=d.replacedAt)) continue;
      const gone=window.__objById.get(d.removedId);
      if(!gone) continue;
      const look=window.__avatarLook(d.attributedUid,t); if(!look) continue;
      // Stand where the standing rule would put anyone working at this spot,
      // half a grid tile clear of the piece so it is not inside them, facing it.
      const rep=window.__objById.get(d.replacementId);
      let ox=rep?gone.position.x-rep.position.x:0, oy=rep?gone.position.y-rep.position.y:0;
      const m=Math.hypot(ox,oy);
      if(m<1e-3){ox=1;oy=0;} else {ox/=m;oy/=m;}
      const sx=gone.position.x+ox*GRID_PITCH/2, sy=gone.position.y+oy*GRID_PITCH/2;
      const sz=window.__standZ(gone);
      const yaw=Math.atan2(gone.position.y-sy,gone.position.x-sx);
      return {uid:d.attributedUid,name:look.name,parts:look.parts,appearanceExact:look.exact,
        x:sx,y:sy,z:sz+EYE,
        qx:0,qy:0,qz:Math.sin(yaw/2),qw:Math.cos(yaw/2)};
    }
    return null;
  };
  /**
   * The REPLAY-PHASE builder: someone placing a piece that appears after the
   * opening snapshot.
   *
   * WHY THIS EXISTS. The build-out reconstruction covers only the pieces that
   * were already standing when the record starts. Everything added afterwards
   * was revealed by __frame's ordinary lifetime test with NOBODY placing it,
   * because __builderAt is frozen at the end of the build order for the whole
   * replay — it kept re-drawing the last build-out piece's builder standing
   * still while new pieces popped into existence elsewhere. At Wooden Camp that
   * is 128 player-placed pieces including 25 decorations; measured per base by
   * the `replay-phase placements` audit line.
   *
   * PROVENANCE, and this half is STRONGER than the build-out's, not weaker:
   *   WHEN  real. The piece's own first-seen timestamp in times_<b>.json, i.e.
   *         the snapshot it appears in. Resolution is the snapshot cadence: the
   *         save records no placement instant, so this says "between the previous
   *         snapshot and this one", exactly as the reveal itself already does.
   *   WHO   real. The piece's own recorded build_player_uid. NOTHING IS CARRIED
   *         FORWARD HERE, deliberately: unlike the build order this is not one
   *         continuous session, and the things that appear during the replay
   *         include breeding eggs and world rocks that no player placed at all
   *         (74 PalEgg_Dark + 19 HatchingPalEgg at Wooden Camp). A piece with no
   *         recorded builder draws nobody, which is the right answer for those.
   *   WHERE a rendering choice, the same one __demoAt makes: half a grid tile
   *         clear of the piece, on the level the standing rule puts it, facing
   *         it. The save records no build position for anything.
   *
   * One avatar per snapshot. The save records no ORDER within a snapshot, so
   * where several pieces appear together the representative is picked by sorted
   * id — deterministic, and it makes no claim that this piece went down first.
   */
  window.__replayNew=(()=>{
    const m=new Map();
    for(const x of window.__all){
      if(x.loot||x.pre!==undefined||!x.t) continue;
      if(!window.__builders[x.o.id]) continue;      // no recorded builder -> nobody
      const k=x.t[0];
      if(!m.has(k)) m.set(k,[]);
      m.get(k).push(x.o.id);
    }
    for(const v of m.values()) v.sort();
    return m;
  })();
  window.__replayBuilderAt=(preShown,t)=>{
    if(preShown<window.__preCount) return null;     // build-out still running
    const ids=window.__replayNew.get(t); if(!ids||!ids.length) return null;
    const id=ids[0];
    const uid=window.__builders[id]; if(!uid) return null;
    const o=window.__objById.get(id); if(!o) return null;
    const look=window.__avatarLook(uid,t); if(!look) return null;
    const sz=window.__standZ(o);
    // Offset direction is derived from the piece's own position rather than a
    // fixed axis, so the avatar is not always on the same side of everything.
    const a=Math.atan2(o.position.y,o.position.x);
    const sx=o.position.x+Math.cos(a)*GRID_PITCH/2, sy=o.position.y+Math.sin(a)*GRID_PITCH/2;
    const yaw=Math.atan2(o.position.y-sy,o.position.x-sx);
    return {uid,name:look.name,parts:look.parts,appearanceExact:look.exact,
      attribution:'recorded',
      x:sx,y:sy,z:sz+EYE,
      qx:0,qy:0,qz:Math.sin(yaw/2),qw:Math.cos(yaw/2)};
  };
  // Resolve the credited actor and staging pose for one replay change. The
  // actor identity comes from recorded build ownership or the explicitly
  // labelled fallback chain in __changeUid; only the staging position is a
  // rendering choice.
  window.__changePose=(j,k,t)=>{
    const ch=window.__changes[j];
    if(!ch||k<1||k>ch.length) return null;
    const c=ch[k-1];
    const ent=window.__changeUid[j][k-1]; if(!ent) return null;
    const o=window.__objById.get(c.id); if(!o) return null;
    // Eggs and mineable rocks change on the world's own terms. Animate their
    // arrival/departure, but never fabricate a player responsible for it.
    if(/^(Pal[Ee]gg|DamagableRock)/.test(o.typeId))
      return {uid:null,name:null,parts:null,appearanceExact:false,
              attribution:'world-no-actor',kind:c.kind};
    const look=window.__avatarLook(ent.uid,t); if(!look) return null;
    let sx,sy,sz,yaw;
    if(c.kind==='add'){
      const prev=k>1?window.__objById.get(ch[k-2].id):null;
      const stand=prev||o;
      sx=stand.position.x; sy=stand.position.y; sz=window.__standZ(stand,o);
      const dx=o.position.x-sx,dy=o.position.y-sy;
      if(Math.hypot(dx,dy)<1e-3){
        const nxt=k<ch.length?window.__objById.get(ch[k].id):null;
        yaw=nxt?Math.atan2(nxt.position.y-sy,nxt.position.x-sx):0;
      }else yaw=Math.atan2(dy,dx);
    }else{
      const rep=window.__demoRepl.get(c.id);
      const r=rep?window.__objById.get(rep):null;
      let ox=r?o.position.x-r.position.x:o.position.x;
      let oy=r?o.position.y-r.position.y:o.position.y;
      const m=Math.hypot(ox,oy);
      if(m<1e-3){ox=1;oy=0;}else{ox/=m;oy/=m;}
      sx=o.position.x+ox*GRID_PITCH/2; sy=o.position.y+oy*GRID_PITCH/2;
      sz=window.__standZ(o);
      yaw=Math.atan2(o.position.y-sy,o.position.x-sx);
    }
    return {uid:ent.uid,name:look.name,parts:look.parts,appearanceExact:look.exact,
      attribution:ent.prov,kind:c.kind,x:sx,y:sy,z:sz+EYE,
      qx:0,qy:0,qz:Math.sin(yaw/2),qw:Math.cos(yaw/2)};
  };
  // Hold the previous snapshot on screen and apply this snapshot's changes one
  // at a time, so no build or removal silently appears in a batch.
  window.__frameStep=(j,k)=>{
    const S=window.__steps,t=S[j],prevT=j>0?S[j-1]:t;
    const ch=window.__changes[j]||[],rm=new Set(),ad=new Set();
    for(let i=0;i<k&&i<ch.length;i++) (ch[i].kind==='remove'?rm:ad).add(ch[i].id);
    const L=[];
    for(const x of window.__all){
      const id=x.o.id;
      if(x.loot){if(!x.t||(x.t[0]<=t&&x.t[1]>=t))L.push(window.__withPaint(x.o,t));continue;}
      if(ad.has(id)){L.push(window.__withPaint(x.o,t));continue;}
      if(rm.has(id))continue;
      if(x.pre!==undefined){
        if(x.pre<window.__preCount&&(!x.t||x.t[1]>=prevT))L.push(window.__withPaint(x.o,t));
        continue;
      }
      if(!x.t||(x.t[0]<=prevT&&x.t[1]>=prevT))L.push(window.__withPaint(x.o,t));
    }
    window.__mappalCam.setObjects(L);return L.length;
  };
  window.__firstT=window.__all.length?Math.min(...window.__all.filter(x=>x.t).map(x=>x.t[0])):0;
  window.__preCount=pre.length; window.__preShown=pre.length; window.__preRecent=0;
  window.__frame=(t,preShown)=>{const L=[];
    for(const x of window.__all){
      // `x.t` may be null for a reconstructed piece (see the `pre` filter): no
      // recorded lifetime means nothing says it was ever removed, so it stays
      // once placed.
      if(x.pre!==undefined){ if(x.pre<preShown && (!x.t||x.t[1]>=t)) L.push(window.__withPaint(x.o,t)); continue; }
      if(!x.t||(x.t[0]<=t&&x.t[1]>=t))L.push(window.__withPaint(x.o,t));
    }
    window.__mappalCam.setObjects(L);return L.length;};
  // bounds of what is ALIVE at t. The recent-work box STEERS THE LOOK-AT POINT
  // and nothing else.
  //
  // It used to shrink the framed VOLUME (w,h) toward the recent cluster as
  // well, and that is what made the shot pump: at one piece per frame the
  // recent box grows and shrinks continuously as the work moves from a wide
  // scatter of foundations to a tight pocket of furniture and back, so the
  // framing distance derived from it went up and down every few frames — 377 of
  // 878 frames of Wooden Camp moved the camera IN, by up to 6.3% in a single
  // frame. Measured, not eyeballed: see the `camera stability` line the render
  // prints and the camDist column in plan.json.
  //
  // So w/h are now the WHOLE-BASE extents, always. The recent box still decides
  // WHERE the camera looks (the user wants to see the active area) — it just no
  // longer decides how far away it stands.
  window.__bounds=(t,recentFrom)=>{
    let live=[],recent=[];
    for(const x of window.__all){
      if(x.loot)continue;                       // loot never drives the camera
      if(x.pre!==undefined){
        if(x.pre<window.__preShown && (!x.t||x.t[1]>=t)){ live.push(x);
          if(x.pre>=window.__preShown-window.__preRecent)recent.push(x); }
        continue;
      }
      if(!x.t){live.push(x);continue;}
      if(x.t[0]<=t&&x.t[1]>=t){live.push(x); if(x.t[0]>=recentFrom)recent.push(x);}
    }
    if(!live.length)return null;
    // percentile bounds: one stray piece far from the camp must not blow up
    // the framing (min/max did exactly that and zoomed to a horizon speck).
    const ext=a=>{const v=a.slice().sort((p,q)=>p-q);
      const lo=v[Math.floor(v.length*0.005)], hi=v[Math.min(v.length-1,Math.ceil(v.length*0.995))];
      return [lo,hi];};
    const X=ext(live.map(p=>p.sx)),Y=ext(live.map(p=>p.sy)),Z=ext(live.map(p=>p.sz));
    let tx=(X[0]+X[1])/2, ty=(Y[0]+Y[1])/2, tz=(Z[0]+Z[1])/2;
    let w=Math.max(X[1]-X[0],Z[1]-Z[0]), h=Y[1]-Y[0];
    // lw/lh are the extents the OLD camera would have framed, i.e. whole-base
    // blended toward the recent-work box. Nothing in the live camera reads them;
    // they exist so the stability block can solve the pre-fix camera over the
    // SAME frame plan and print a real before/after instead of a claim. Delete
    // them and the A/B in the log goes with them.
    let lw=w, lh=h;
    if(recent.length>=3){
      const rX=ext(recent.map(p=>p.sx)),rY=ext(recent.map(p=>p.sy)),rZ=ext(recent.map(p=>p.sz));
      const rw=Math.max(rX[1]-rX[0],rZ[1]-rZ[0]), rh=rY[1]-rY[0];
      const rcx=(rX[0]+rX[1])/2, rcy=(rY[0]+rY[1])/2, rcz=(rZ[0]+rZ[1])/2;
      // How tightly clustered is this step's work relative to the whole base?
      // Tight cluster (e.g. one interior room) -> aim at it. Spread-out work ->
      // stay near the middle of the base. This biases the LOOK-AT only; the
      // framing distance below is derived from w/h, which stay whole-base.
      const tight=Math.min(1,Math.max(rw,rh)/Math.max(Math.max(w,h),1));
      const k=1-Math.min(1,tight/0.45);          // 1 = very tight, 0 = base-wide
      const blend=0.15+0.65*k;
      tx=tx*(1-blend)+rcx*blend; ty=ty*(1-blend)+rcy*blend; tz=tz*(1-blend)+rcz*blend;
      // The pre-fix zoom, restored verbatim for measurement only. This is the
      // line that caused the pumping: the recent box grows and shrinks every few
      // frames, so the framed volume — and with it the camera distance — did too.
      lw=w*(1-k)+Math.max(rw,12)*k; lh=h*(1-k)+Math.max(rh,10)*k;
    }
    return {tx,ty,tz,w,h,lw,lh,n:live.length};
  };
  window.__orbit=(az,dist,el,tx,ty,tz)=>{const c=window.__mappalCam,a=az*Math.PI/180,e=el*Math.PI/180;
    c.setView([tx+Math.cos(a)*Math.cos(e)*dist, ty+Math.sin(e)*dist, tz+Math.sin(a)*Math.cos(e)*dist],[tx,ty,tz]);};
  const ts=[...new Set(window.__all.filter(x=>x.t&&!x.loot).map(x=>x.t[0]))].sort((a,b)=>a-b);
  window.__steps=ts;
  // ---- the change list itself ------------------------------------------------
  // One entry per piece that arrives or departs at each step. See the long note
  // on __changePose for what is recorded here and what is reconstructed.
  //
  // ORDER WITHIN A STEP. Removals first, then arrivals, so a replacement reads
  // as a replacement: the old piece comes down and the new one goes up in the
  // same beat. Arrivals are ordered by the SAME buildorder.js reconstruction the
  // build-out uses — a real topological sort of the support relation implied by
  // the pieces' own positions, so a step lays its foundations before its roofs
  // and finishes with decoration, and consecutive pieces are usually adjacent so
  // the avatar's walk falls out of the order. The save records no order within a
  // snapshot; removals therefore fall back to sorted id, which is deterministic
  // and claims nothing.
  window.__demoRepl=new Map();
  for(const d of window.__demolitions) if(d.replacementId) window.__demoRepl.set(d.removedId,d.replacementId);
  window.__changes=(()=>{
    const idx=new Map(ts.map((t,i)=>[t,i]));
    const adds=ts.map(()=>[]), rems=ts.map(()=>[]);
    const lastStep=ts[ts.length-1];
    for(const x of window.__all){
      if(x.loot||!x.t) continue;
      // ARRIVAL: not part of the build-out, and first seen at a later step.
      if(x.pre===undefined){ const i=idx.get(x.t[0]); if(i!==undefined&&i>0) adds[i].push(x.o.id); }
      // DEPARTURE: last seen at t[1], so gone from the first step after it. This
      // is the same test __frame already applied silently; the only new thing is
      // that it now costs a frame and draws somebody.
      if(x.t[1]<lastStep){
        let lo=0,hi=ts.length-1,i=-1;
        while(lo<=hi){const m=(lo+hi)>>1; if(ts[m]<=x.t[1]){i=m;lo=m+1;} else hi=m-1;}
        if(i>=0&&i+1<ts.length) rems[i+1].push(x.o.id);
      }
    }
    return ts.map((_,i)=>{
      rems[i].sort();
      let ordered=adds[i];
      if(ordered.length>1) ordered=window.__reconstructBuildOrder(ordered.map(id=>{
        const o=window.__objById.get(id);
        return {id,x:o.position.x,y:o.position.y,z:o.position.z,typeId:o.typeId,
                category:(reg[o.typeId]||{}).category};}),{stats:{}});
      return rems[i].map(id=>({kind:'remove',id})).concat(ordered.map(id=>({kind:'add',id})));
    });
  })();
  // Who each change is attributed to, and how. The carry-forward chain runs in
  // ANIMATION order — build-out first, then step by step, change by change — and
  // is seeded from the last known builder of the build order, so a replay change
  // with no attribution of its own inherits the person last seen working.
  window.__changeUid=(()=>{
    let last=window.__carriedBuilder.length?window.__carriedBuilder[window.__carriedBuilder.length-1]:null;
    return window.__changes.map(list=>list.map(c=>{
      const own=window.__builders[c.id]||null;
      if(c.kind==='add'){
        if(own){last=own; return {uid:own,prov:'recorded'};}
        return last?{uid:last,prov:'carried'}:null;
      }
      const paired=window.__demoPairs.get(c.id);
      if(paired){last=paired; return {uid:paired,prov:'pairedReplacement'};}
      if(own){last=own; return {uid:own,prov:'ownBuilder'};}
      return last?{uid:last,prov:'carried'}:null;
    }));
  })();
  // Attribution coverage over the reconstructed build-out, so a render states
  // how much of what it shows is actually credited to a player.
  const bstat={attributed:0,recorded:0,carried:0,unrecorded:0,inexactLook:0,noMesh:0,
               byName:{},carriedByCat:{},unrecordedByCat:{}};
  const catOf=id=>{const o=window.__objById.get(id); return (o&&(reg[o.typeId]||{}).category)||'uncategorised';};
  for(let k=1;k<=window.__buildOrder.length;k++){
    const id=window.__buildOrder[k-1];
    const uid=window.__builders[id];
    const a=window.__builderAt(k,t0all);
    // Nobody at all: the piece has no recorded builder AND no earlier known
    // builder to carry forward, so it precedes the first attribution.
    if(!a){ if(!uid&&!window.__carriedBuilder[k-1]){bstat.unrecorded++;
              bstat.unrecordedByCat[catOf(id)]=(bstat.unrecordedByCat[catOf(id)]||0)+1;}
            // Attributed but unresolvable to real extracted meshes: counted, not faked.
            else bstat.noMesh++;
            continue; }
    bstat.attributed++;
    if(a.attribution==='recorded') bstat.recorded++;
    else { bstat.carried++; bstat.carriedByCat[catOf(id)]=(bstat.carriedByCat[catOf(id)]||0)+1; }
    const who=a.name?`${a.name} (${a.uid})`:`${a.uid} (unnamed)`;
    bstat.byName[who]=(bstat.byName[who]||0)+1;
    if(!a.appearanceExact)bstat.inexactLook++;
  }
  // ---- REPLAY-PHASE COVERAGE -------------------------------------------------
  // The same accounting for the change list: every arrival and every departure
  // after the opening snapshot, whether it draws somebody, and on what basis.
  // This is the line that has to make a gap of this class impossible to miss —
  // `unanimated` must be zero, and if it is not it says how many and of what.
  //
  // The change list is deliberately WIDER than demolitions_<b>.json's removal
  // census, and the difference has to be visible or it looks like a bug. That
  // pass excludes Pal eggs and DamagableRocks by type and ignores anything that
  // vanishes within 600 s of the last snapshot, because it is hunting for
  // player DEMOLITIONS. This list is hunting for anything that CHANGES ON
  // SCREEN, which is a superset: an egg that hatches and a rock that is mined
  // both disappear in front of the viewer and both used to do it silently.
  // They are animated for that reason and counted apart from built pieces here,
  // because "somebody demolished this wall" and "this ore node was mined" are
  // different claims and the actor shown on a world object is the weakest
  // inference in the file.
  const WORLDOBJ=/^(Pal[Ee]gg|DamagableRock)/;
  const cstat={adds:0,removes:0,animated:0,unanimated:0,byProv:{},byName:{},
               unanimatedByCat:{},carriedByCat:{},
               builtAdds:0,builtRemoves:0,worldAdds:0,worldRemoves:0,worldByType:{}};
  for(let j=0;j<window.__changes.length;j++){
    for(let k=1;k<=window.__changes[j].length;k++){
      const c=window.__changes[j][k-1];
      const co=window.__objById.get(c.id);
      const isWorld=!!co&&WORLDOBJ.test(co.typeId);
      if(isWorld){ if(c.kind==='add')cstat.worldAdds++; else cstat.worldRemoves++;
                   cstat.worldByType[co.typeId]=(cstat.worldByType[co.typeId]||0)+1; }
      else { if(c.kind==='add')cstat.builtAdds++; else cstat.builtRemoves++; }
      if(c.kind==='add')cstat.adds++; else cstat.removes++;
      const a=window.__changePose(j,k,ts[j]);
      if(!a){cstat.unanimated++;
             cstat.unanimatedByCat[catOf(c.id)]=(cstat.unanimatedByCat[catOf(c.id)]||0)+1; continue;}
      cstat.animated++;
      const key=c.kind+':'+a.attribution;
      cstat.byProv[key]=(cstat.byProv[key]||0)+1;
      if(a.attribution==='carried')
        cstat.carriedByCat[catOf(c.id)]=(cstat.carriedByCat[catOf(c.id)]||0)+1;
      const who=a.name?`${a.name} (${a.uid})`:`${a.uid} (unnamed)`;
      cstat.byName[who]=(cstat.byName[who]||0)+1;
    }
  }
  // ---- THE ONE THING STILL ALLOWED TO POP -----------------------------------
  // Ground loot (CommonDropItem3D) keeps its own recorded lifetime and gets no
  // frame and no actor: it is on the game's 1 h despawn timer, no player places
  // or removes it, and there are thousands of rows, so putting it in the change
  // list would bury every real placement under litter. That is a judgement, and
  // a judgement that contradicts "nothing appears on its own" has to be STATED
  // WITH ITS COUNT rather than left for someone to find in a frame diff. These
  // are the appearances and disappearances this render still does unattended.
  const lstat={rows:0,popIn:0,popOut:0,tailBuilt:0,tailWorld:0,tailByType:{},tailGapSec:0};
  for(const x of window.__all){
    if(!x.loot) continue;
    lstat.rows++;
    if(x.t){ if(x.t[0]>ts[0]) lstat.popIn++; if(x.t[1]<ts[ts.length-1]) lstat.popOut++; }
  }
  // ---- THE OTHER RESIDUAL: removals PAST THE LAST BUILD STEP -----------------
  // The steps are the distinct FIRST-SEEN timestamps, so the last step is the
  // last time anything was BUILT — which can be well before the last snapshot we
  // hold (220,508 s before it at Wooden Camp). A piece whose last sighting falls
  // in that tail window has no later step to hang a removal frame on, so it is
  // never shown coming down and stays on screen to the end.
  //
  // This is the OPPOSITE failure to a pop: nothing vanishes unattended, a few
  // pieces are simply still standing in the final frame when the final save says
  // they are gone. Small (single digits per base) but real, and the difference
  // between this count and demolitions_<b>.json's removal census is exactly it —
  // so it is stated rather than left looking like an arithmetic error.
  {
    const lastStep=ts[ts.length-1];
    let t1=lastStep;
    for(const x of window.__all) if(x.t&&x.t[1]>t1) t1=x.t[1];
    lstat.tailGapSec=t1-lastStep;
    for(const x of window.__all){
      if(x.loot||!x.t) continue;
      if(x.t[1]<t1&&x.t[1]>=lastStep){
        // Split exactly as the change list does. Calling a hatched Pal egg a
        // "built piece that over-stays" would be the same category error the
        // departure count itself had.
        if(WORLDOBJ.test(x.o.typeId)) lstat.tailWorld++; else lstat.tailBuilt++;
        lstat.tailByType[x.o.typeId]=(lstat.tailByType[x.o.typeId]||0)+1;
      }
    }
  }
  return {total:all.length,steps:ts.length,tsList:ts,preCount:window.__preCount,stats,
          changeCounts:window.__changes.map(c=>c.length),changes:cstat,loot:lstat,
          palCount:window.__pals.length,playerCount:window.__players.length,
          terrainCount:window.__terrain.length,builders:bstat,
          wardrobe:window.__wardrobe,eqStats:window.__eqStats,eqDirOk:window.__eqDirOk,
          wild:window.__wildStats,
          equipMeshCount:window.__equipMeshCount||0,stance};
},BASE,REG,ACTORS,STANCE,WILDPVP);
if(ACTORS) console.log(`  actors: ${setup.palCount} Pals with recorded positions in this base,`+
  ` ${setup.playerCount} players with a recorded in-base transform,`+
  ` ${setup.terrainCount} surrounding props from the game's own level data`);
if(ACTORS&&setup.wild){const W=setup.wild;
  console.log(`  wild Pals: ${W.day} day / ${W.night} night instances across ${W.spawners} spawn points`+
    ` (${W.species} species), drawn from the game's own spawn TABLES — authored world content, not`+
    ` recorded positions. One weighted group per spawner, chosen by a documented deterministic hash,`+
    ` so alternatives are never stacked on the same point.`+
    (W.pvpSpawners?` ${W.pvpSpawners} spawn points belong to a PvP arena and are`+
      ` ${W.pvpIncluded?'INCLUDED (WILDPVP=1)':`filtered out (${W.dayAll-W.day} day instances dropped; WILDPVP=1 to keep them)`}.`:'')+
    ` The list swaps at SUNRISE/SUNSET (05/19) because the tables themselves mark groups Night-only.`);}
if(ACTORS&&setup.builders){const B=setup.builders;
  console.log(`  builder avatars: ${B.attributed}/${B.attributed+B.noMesh+B.unrecorded} build-out pieces`+
    ` draw a builder ${JSON.stringify(B.byName)}.`+
    // Same shape as the equipment-provenance line: what is recorded, what is
    // inferred, stated separately and never merged into one number.
    ` Attribution provenance: ${B.recorded} use the piece's OWN recorded build_player_uid;`+
    ` ${B.carried} have none recorded and CARRY FORWARD the last known builder in the order`+
    ` — a rendering choice, not data, so that a decoration or a workbench the save leaves`+
    ` unattributed is still shown being placed rather than appearing out of nothing`+
    (B.carried?` (by category: ${JSON.stringify(B.carriedByCat)})`:'')+`.`+
    ` ${B.unrecorded} precede any known builder in the order, so there is nothing to carry`+
    ` and they still draw NO avatar`+
    (B.unrecorded?` (${JSON.stringify(B.unrecordedByCat)})`:'')+
    (B.noMesh?`; ${B.noMesh} are attributed but have no resolvable extracted mesh (left undrawn, never substituted)`:'')+
    `. Appearance from Players/<uid>.sav; ${B.inexactLook} of the attributed pieces predate the`+
    ` surviving player-save window and use that player's earliest recorded look.`+
    ` Names come from the save's own guild rosters. Avatar POSITION is a rendering choice`+
    ` (stand on the piece just built, face the next) — the save records no per-piece build position.`);}
if(ACTORS&&setup.changes){const C=setup.changes;
  console.log(`  replay changes: ${C.adds} arrivals + ${C.removes} departures after the opening`+
    ` snapshot; ${C.animated} draw an actor placing or removing the piece, ${C.unanimated} do not`+
    (C.unanimated?` and STILL POP IN OR OUT SILENTLY ${JSON.stringify(C.unanimatedByCat)}`:'')+
    `. Who: ${JSON.stringify(C.byName)}. Provenance ${JSON.stringify(C.byProv)} —`+
    ` add:recorded is the piece's own build_player_uid; add:carried inherits the last known builder;`+
    ` remove:pairedReplacement credits the builder of the piece that took the spot`+
    ` (demolitions_${BASE}.json); remove:ownBuilder credits whoever BUILT the piece; remove:carried`+
    ` inherits. Every removal attribution is INFERENCE — the save records no destruction event, no`+
    ` demolisher and no time inside the gap between the last snapshot that has the piece and the`+
    ` first that does not.`+
    (C.carriedByCat&&Object.keys(C.carriedByCat).length?` Carried-forward by category:`+
      ` ${JSON.stringify(C.carriedByCat)}.`:'')+
    // The built/world split, so this count can be reconciled against
    // demolitions_<b>.json's narrower removal census instead of looking inflated.
    ` Of those, ${C.builtAdds} arrivals + ${C.builtRemoves} departures are BUILT PIECES;`+
    ` ${C.worldAdds} + ${C.worldRemoves} are world objects that change on their own terms`+
    ` (${JSON.stringify(C.worldByType)}) — a Pal egg that hatches, an ore node that was mined`+
    ` and respawns. Those changes are animated too, but draw NO ACTOR (world-no-actor):`+
    ` the save records no mining or hatching event and no responsible player.`);}
if(ACTORS&&setup.loot){const L=setup.loot;
  console.log(`  still unattended: ${L.rows} ground-loot rows (CommonDropItem3D) keep their own`+
    ` recorded lifetime and get NO frame and NO actor — ${L.popIn} of them appear and ${L.popOut}`+
    ` despawn during the render, unattended. Deliberate: loot is on the game's own 1 h despawn`+
    ` timer, no player places or removes it, and giving each row a frame would bury every real`+
    ` placement under litter. It is the ONLY class of thing this render still lets appear or`+
    ` vanish on its own, and it is stated here rather than left to be discovered.`+
    (L.tailBuilt+L.tailWorld
      ?`  RESIDUAL, opposite sign: ${L.tailBuilt} built piece(s) and ${L.tailWorld} world object(s)`+
       ` ${JSON.stringify(L.tailByType)} are last seen inside the ${L.tailGapSec}s between the last`+
       ` BUILD step and the last snapshot we hold. There is no later step to hang a removal frame`+
       ` on, so they are never shown going and remain on screen in the final frame although the`+
       ` final save no longer has them. Nothing pops — they OVER-STAY, which is the opposite`+
       ` failure and a much smaller one.`
      :`  No removal of any kind falls past the last build step.`));}
// The editor's grid is a flat 1m/10m lattice at y=0. With TerrainLayer drawing
// the game's own ground it is redundant, it cuts visibly through real hills, and
// its high-contrast lines sit right on top of the builder avatar — see
// sceneChromeStore.ts. Render-time only; interactive MapPal keeps its grid.
await page.evaluate(()=>{ if(window.__mappalCam.setChrome) window.__mappalCam.setChrome({grid:false}); });
if(ACTORS) await page.evaluate(()=>{ if(window.__mappalCam.setTerrain&&window.__terrain.length)
  window.__mappalCam.setTerrain(window.__terrain); });
// The opening frame claims to be the Palbox. buildorder.js pins it first by
// construction, but "by construction" is worth checking rather than believing.
const _first=await page.evaluate(()=>{const id=window.__buildOrder[0];
  const o=window.__objById.get(id); return o?{id,typeId:o.typeId}:null;});
console.log(`  opening piece (build order index 0): ${_first?_first.typeId:'NONE'}`);
// ---- EQUIPMENT AUDIT --------------------------------------------------------
// What each player is shown wearing, where the answer came from, and what is
// deliberately not drawn. Printed rather than assumed, because "the avatar has
// clothes on now" and "the avatar has the RIGHT clothes on" are different
// claims and only the second one is worth making.
if(ACTORS&&setup.wardrobe){
  const D=t=>new Date(t*1000).toISOString().replace('T',' ').slice(0,16);
  if(!setup.eqDirOk)
    console.log('  !! EQUIPMENT MESHES ARE NOT BEING SERVED from public/equipment_meshes_posed'+
      `${setup.stance==='standing'?'_standing':''}. vite builds its public/ listing at STARTUP, so restart the dev`+
      ' server. Armour will be missing from this render.');
  const eqdir='public/equipment_meshes_posed'+(setup.stance==='standing'?'_standing':'');
  console.log(`  equipment: ${setup.equipMeshCount} armour/head-equip GLB references preloaded`+
    ` from ${eqdir} (stance=${setup.stance}, same baked animation frame as the player meshes —`+
    ' bind-pose equipment_meshes/ is never used, it would re-T-pose the avatar)');
  for(const w of setup.wardrobe)
    console.log(`    ${(w.name||w.uid).padEnd(11)} ${D(w.from)} .. ${D(w.to)}  body=${w.body}`+
      `${w.bodyMesh?' ['+w.bodyMesh+']':''}  head=${w.head}${w.headMesh?' ['+w.headMesh+']':''}`);
  const E=setup.eqStats;
  console.log(`  equipment provenance over ${E.calls} avatar draws:`+
    ` ${E.overrideBody} used the RECORDED mirror override (OverrideEquipmentInfo.Body, which the`+
    ` game shows in place of the equipped armour), ${E.equippedBody} the equipped armour`+
    ` (${E.overrideUnrecorded} of those at a moment where the override state is not recorded at all),`+
    ` ${E.baseBody} drew the base body mesh because the save records NO armour for that player`+
    ` (mannbhai83's containers are empty in every snapshot — a recorded fact, not a lookup failure).`+
    ` Head equipment: ${E.headSkeleton} skeleton-attached, ${E.headSocket} socket-attached at the`+
    ` POSED socket transform, ${E.headHiddenByOverride} hidden because the override says Naked_Head.`+
    (E.headSocketUnplaceable?` ${E.headSocketUnplaceable} head equips name a socket the skeleton does not declare and are NOT drawn.`:'')+
    (Object.keys(E.noGlb).length?` Unextracted: ${JSON.stringify(E.noGlb)} (left undrawn, never substituted).`:''));
  console.log('  WEAPONS: not drawn, on purpose. The save persists the 6-slot weapon loadout but'+
    ' never which slot was in the player\'s hands, and no cooked asset names the socket a held'+
    ' weapon attaches to (it is set in native C++). Drawing one would assert two things the record'+
    ' does not contain, on an avatar that is mid-swing in a BUILD animation. Gliders are out for the'+
    ' same reason (attach "socketUnknown"). Both are still resolved and carried in'+
    ` equipment_${BASE}.json if that judgement is ever revisited.`);
}
if(ACTORS){const ps=await page.evaluate(()=>window.__posedStats||null);
  if(ps) console.log(`  player meshes: ${ps.swapped} part URLs use the BAKED BUILD POSE`+
    ` (public/player_meshes_posed), ${ps.kept} fell back to bind pose`);}
console.log(`  build-out order: ${JSON.stringify(setup.stats.roles)} levels=${setup.stats.levels}`+
            ` supported=${setup.stats.withSupport}/${setup.preCount} elevation-sort fallbacks=${setup.stats.fallbacks}`);

// ---- frame budget: build-out is driven by PIECE COUNT, not by a fixed share --
// The point of the build-out is watching the base go up piece by piece, so the
// frame count is derived from how many pieces there are: PPF pieces per frame.
// At the default PPF=1 you see every single placement land on its own frame.
// Raising PPF trades that away for a shorter video and a shorter render (each
// frame costs ~0.3 s to render, and 30 frames = 1 s of video).
// MAXFRAMES caps the whole thing for a quick smoke test; without it the
// build-out length is whatever the piece count says, so argv[3] no longer
// controls it (it is now only the replay floor).
const MIN_PRE_FRAMES=48;               // a 25-piece camp still deserves a beat
// The build-out now starts from NOTHING BUT THE PALBOX. This was 8 for a real
// reason that has since expired: when it was written the scene had no ground at
// all, so a lone 2 m palbox was literally the only thing in frame, the camera
// closed to ~9 m onto it, and the opening shot held nothing an eye could read.
// The scene it opens on now is completely different — the game's own terrain out
// to 600 m, thousands of foliage instances, the real ocean and its rivers, wild
// Pal spawns — so one Palbox standing on real ground with the sea behind it IS a
// readable establishing shot. The framing problem is fixed where it belongs, by
// pulling the CAMERA back (see the HEAD blend below), not by pre-revealing seven
// pieces the builder is never shown placing.
const OPENING_PIECES=1;                // see the build-out plan loop below
const steps=setup.tsList;
const preCount=setup.preCount||0;
// ---- replay frame budget: driven by CHANGE COUNT, exactly as the build-out is
// driven by piece count. Nothing may appear or disappear without a frame of its
// own showing somebody doing it, so the floor is one frame per arrival and one
// per departure, plus at least one frame per step so that a snapshot in which
// nothing changed still lets the clock run.
//
// Anything left over after that floor goes to the steps with the largest IN-GAME
// time gaps (the same log-compressed weighting the pacing has always used), so a
// fortnight of nothing still costs a handful of frames and the sun visibly
// travels across it.
//
// MAXFRAMES is the one thing that can force changes to share a frame. It is a
// smoke-test cap, and when it bites the log says so explicitly rather than
// quietly reintroducing the batch pop-in this whole mechanism exists to remove.
const changeCounts=setup.changeCounts||steps.map(()=>0);
const CHANGES=changeCounts.reduce((a,b)=>a+b,0);
const REPLAY_NEED=changeCounts.reduce((a,c)=>a+Math.max(1,c),0);
const REPLAY_WANT=Math.max(Math.round(MINFRAMES*0.5),Math.min(steps.length,720));
const MAXFRAMES=process.env.MAXFRAMES?parseInt(process.env.MAXFRAMES):0;
let REPLAY_FRAMES=Math.max(REPLAY_WANT,REPLAY_NEED);
let PPF=parseFloat(process.env.PPF||'1');
if(MAXFRAMES){
  REPLAY_FRAMES=Math.max(steps.length,Math.min(REPLAY_FRAMES,MAXFRAMES-MIN_PRE_FRAMES));
  if(Math.ceil(preCount/PPF)+REPLAY_FRAMES>MAXFRAMES)
    PPF=preCount/Math.max(MIN_PRE_FRAMES,MAXFRAMES-REPLAY_FRAMES);
}
const PRE_FRAMES=Math.max(MIN_PRE_FRAMES,Math.ceil(preCount/PPF));
const N=PRE_FRAMES+REPLAY_FRAMES;
console.log(`${BASE} ${info.name}: ${preCount} pre-existing + ${steps.length} steps`+
            ` (${CHANGES} arrivals+departures) -> ${N} frames`+
            ` (${PRE_FRAMES} build-out @ ${PPF.toFixed(2)} pieces/frame, ${REPLAY_FRAMES} replay)`+
            `  ~${(N/30).toFixed(0)}s of video, ~${(N*0.3/60).toFixed(1)} min to render.`+
            `  [PPF=n pieces per frame, MAXFRAMES=n for a quick smoke test]`);
if(REPLAY_FRAMES<REPLAY_NEED)
  console.log(`  !! MAXFRAMES capped the replay at ${REPLAY_FRAMES} frames against ${REPLAY_NEED}`+
    ` needed for one change per frame, so ${CHANGES} changes will SHARE frames and pieces will`+
    ` appear in batches. Smoke test only — a publishable render must not be capped.`);

// ---- replay pacing: by the WORLD'S OWN CLOCK, not by wall clock ----------
// Every snapshot's Level.sav carries worldSaveData.GameTimeSaveData
// .GameDateTimeTicks, the in-game clock in .NET-style 100 ns ticks. Measured
// across 606 snapshots of this world it advances 45.000 in-game seconds per
// real second, so one in-game day = 864e9 ticks = 32 real minutes.
//
// Pacing rule: each build step earns one baseline frame (so a busy stretch —
// many steps close together — automatically gets many frames), plus a
// log-compressed allowance for however much in-game time passes before the
// next step. The log is what makes a fortnight of nothing cost a handful of
// frames instead of most of the video, while still letting the sun visibly
// travel across it.
const DAY=86400;                              // in-game seconds per in-game day
const GTFILL=fillGameTime(steps);
const stepGame=GTFILL.g;
const missingGame=GTFILL.ok?0:steps.length;
if(GTFILL.filled) console.log(`  game clock: ${GTFILL.filled}/${steps.length} snapshots had no`+
  ` GameTimeSaveData entry; interpolated from measured neighbours (45 in-game s / real s)`);
if(!GTFILL.ok) console.log(`  game clock: NO snapshot of this base has GameTimeSaveData`+
  ` -> uniform pacing, lighting held static`);
// Frames are allocated to steps in two passes. First the FLOOR: every step gets
// max(1, its change count), which is what guarantees each arrival and departure
// its own frame. Then the SURPLUS is shared out on the log-compressed in-game
// time weight by largest remainder, so the extra frames land where real time
// actually passed rather than being spread evenly over snapshots that are
// seconds apart. Both passes are pure functions of the data, so the allocation
// is identical on a re-run.
//
// Every step is visited: unlike the old inverse-CDF sampling, no step can be
// skipped now, because skipping one would skip its changes.
function allocReplay(total){
  const W_STEP=1, K_TIME=2.2;
  const w=steps.map((_,j)=>{
    const a=stepGame[j], b=stepGame[j+1];
    const dg=(a!==null&&b!==null&&b>a)?b-a:0;
    return W_STEP + K_TIME*Math.log1p(dg/DAY);   // log = quiet stretches compress
  });
  const floor=changeCounts.map(c=>Math.max(1,c));
  const need=floor.reduce((a,b)=>a+b,0);
  if(total<need){
    // MAXFRAMES bit. Share what there is in proportion to the floor, so the
    // busiest steps still get the most frames and changes batch as evenly as
    // possible. Announced above; never reached in an uncapped render.
    const out=floor.map(f=>Math.max(1,Math.floor(total*f/need)));
    let s=out.reduce((a,b)=>a+b,0);
    for(let j=0;s>total&&j<out.length;j++) while(out[j]>1&&s>total){out[j]--;s--;}
    for(let j=0;s<total;j=(j+1)%out.length){out[j]++;s++;}
    return out;
  }
  const rest=total-need, W=w.reduce((a,b)=>a+b,0);
  const share=w.map(x=>rest*x/W), add=share.map(Math.floor);
  let used=add.reduce((a,b)=>a+b,0);
  const order=share.map((x,j)=>[x-add[j],j]).sort((a,b)=>b[0]-a[0]);
  for(let i=0;used<rest;i++,used++) add[order[i%order.length][1]]++;
  return floor.map((f,j)=>f+add[j]);
}
// {j, k, gameSec} per replay frame. k is 1-based into that step's change list:
// frame m of the M frames allotted to step j shows change ceil at m*C/M, so with
// M >= C every change lands on a frame of its own and any surplus frames simply
// hold the last change while the clock runs.
const replayPlan=(()=>{
  const alloc=allocReplay(N-PRE_FRAMES);
  const out=[];
  for(let j=0;j<steps.length;j++){
    const M=alloc[j], C=changeCounts[j];
    const g0=stepGame[j], g1=(j+1<steps.length?stepGame[j+1]:g0);
    for(let m=0;m<M;m++){
      const k=C?Math.min(C,Math.floor(m*C/M)+1):0;
      const f=M===1?0:m/M;
      out.push({j,k,gameSec:(GAMEPACE&&missingGame===0&&g0!==null&&g1!==null)?g0+(g1-g0)*f:g0});
    }
  }
  return out;
})();
{
  const per=replayPlan.reduce((m,p)=>m.set(p.j,(m.get(p.j)||0)+1),new Map());
  const solo=changeCounts.reduce((a,c,j)=>a+(c<=(per.get(j)||0)?c:0),0);
  if(stepGame[steps.length-1]!==null&&stepGame[0]!==null)
    console.log(`  in-game span ${((stepGame[steps.length-1]-stepGame[0])/DAY).toFixed(1)} days;`+
      ` ${per.size}/${steps.length} steps shown, frames/step max=${Math.max(...per.values())};`+
      ` ${solo}/${CHANGES} changes get a frame to themselves`);
  if(!GAMEPACE||missingGame)
    console.log(`  in-game pacing OFF: ${missingGame}/${steps.length} steps have no GameTimeSaveData`);
}

// ---- pass 1: per-frame bounds of what is alive (cheap, no screenshots) ----
const FOV=50*Math.PI/180, ASPECT=1600/1000;
const raw=[];
// Frame plan: {t, recentFrom, preShown, preRecent, gameSec}
const plan=[];
// The build-out clock is HELD, by default at the first snapshot's real in-game
// time. Those pieces have no recorded build times anywhere in the data, so
// there is no in-game DURATION to run: the reconstruction claims an order, and
// running a clock across it would additionally claim elapsed time — and with
// it days of sunlight that never happened. So the whole build-out reads as one
// in-game moment and hands over to the replay at the same hour, with no
// lighting jump.
//
// BUILDOUT_HOUR=<0..24> overrides which held hour that is. It is a declared
// STYLISTIC choice, not data: bases whose first snapshot happens to land at
// night (Glass Tower's is 22:24, Stone Works' 01:11) otherwise open on a long
// night-lit stretch purely by accident of when the backup ran. Setting e.g.
// BUILDOUT_HOUR=8 says "show the reconstruction in daylight" — it still makes
// no claim about when anything was built.
const buildoutGame=(process.env.BUILDOUT_HOUR!==undefined&&stepGame[0]!==null)
  ? Math.floor(stepGame[0]/DAY)*DAY + parseFloat(process.env.BUILDOUT_HOUR)*3600
  : stepGame[0];
for(let i=0;i<PRE_FRAMES;i++){
  const f=(i+1)/PRE_FRAMES;
  // OPENING_PIECES is the floor on what frame 0 shows, and it is 1: frame 0 is
  // the Palbox alone, which buildorder.js guarantees is piece 0 (its PRE table
  // pins {natural:0, palbox:0} ahead of everything else). Every subsequent
  // placement then gets its own frame at the default PPF=1.
  //
  // preRecent is the window the camera treats as "what is being worked on now".
  // It has to be a ROOM, not a piece: at one piece per frame, a 3-piece window
  // made the camera re-aim every single frame, and measured out at 5-17/255
  // luminance lurches reversing every frame or two — flicker from camera
  // motion rather than from the sun, but flicker. Sizing it to ~2% of the base
  // (floor 12) gives the follow-cam something room-sized to sit on, which with
  // the spatially local build order is exactly the pocket being worked.
  plan.push({t:steps[0],recentFrom:steps[0],
             preShown:Math.min(preCount,Math.max(OPENING_PIECES,Math.round(preCount*f))),
             preRecent:Math.max(12,Math.round(preCount*0.02)),
             gameSec:buildoutGame});
}
// j/k carry the replay frame's position in the change list; a build-out frame
// has j===null and is driven by preShown as before.
for(const r of replayPlan){
  plan.push({t:steps[r.j],recentFrom:steps[Math.max(0,r.j-2)],preShown:preCount,preRecent:0,
             j:r.j,k:r.k,gameSec:r.gameSec});
}
while(plan.length<N) plan.push(plan[plan.length-1]);
plan.length=N;
{
  const hs=plan.map(p=>p.gameSec).filter(g=>g!==null);
  const jumps=hs.slice(1).map((g,i)=>Math.abs(g-hs[i])/DAY).filter(x=>x>0);
  if(jumps.length){
    jumps.sort((a,b)=>a-b);
    // This is the RAW data rate, not what gets rendered: the lighting clamp
    // below is what the eye actually sees. Kept because it says how much real
    // in-game time each frame covers, which is a fact about the world.
    console.log(`  raw in-game time per frame: median ${jumps[jumps.length>>1].toFixed(2)} days,`+
      ` p90 ${jumps[Math.floor(jumps.length*0.9)].toFixed(2)} days (lighting is clamped below)`);
  }
}
// Resolve the exact on-screen actor through the same path the render uses, so
// plan.json can prove every changing frame's identity and attribution.
for(const p of plan){
  const r=await page.evaluate((tt,rf,ps,pr,sj,sk)=>{
    window.__preShown=ps;window.__preRecent=pr;
    const bb=window.__bounds(tt,rf);
    const a=(sj===null||sj===undefined)?window.__builderAt(ps,tt):window.__changePose(sj,sk,tt);
    return {b:bb,who:a?(a.name||a.uid):null,kind:a?(a.kind||'build'):null,
            prov:a?a.attribution:null};
  },p.t,p.recentFrom,p.preShown,p.preRecent,p.j===undefined?null:p.j,p.k===undefined?0:p.k);
  raw.push({t:p.t,b:r.b,preShown:p.preShown,preRecent:p.preRecent,j:p.j,k:p.k,
            gameSec:p.gameSec,who:r.who,kind:r.kind,prov:r.prov});
}
// ---- the ending must show the FINISHED BASE, not a detail ----------------
// __bounds deliberately moves the camera in when a step's new pieces form a
// tight cluster, which is right in the middle of the animation and wrong at the
// end: Glass Tower's last steps are small ground-level additions, so the final
// frame was a close-up of the ground deck with the tower entirely out of shot.
// Over the last TAIL of the run we blend the framing back out to the whole
// finished base, so the delta-zoom still works mid-animation but the reveal
// always lands. Passing recentFrom=Infinity makes __bounds see no "recent"
// pieces at all, which is exactly its whole-base framing.
const TAIL=0.14;
// ...and the OPENING must show the SITE, not a 2 m box filling the screen.
// Symmetric to TAIL and for the same reason. __bounds frames what is alive
// right now, which at piece 1 is one Palbox, so the raw framing puts the camera
// ~9 m from it and the shot reads as a close-up of a crate. Blending the first
// HEAD of the run out to the whole-base framing opens on the footprint the base
// will occupy — real ground, trees, the sea — and lets the camera close in as
// the base actually grows into it. This is a CAMERA fix; it reveals no piece
// early, so the build-out still starts from nothing but the Palbox.
const HEAD=0.10;
// recentFrom is a timestamp and must be LARGER than any real one so that
// nothing counts as "recent" and __bounds gives plain whole-base framing.
// Infinity cannot be used: page.evaluate serialises its arguments as JSON and
// Infinity arrives in the page as null, which compares >= true against every
// timestamp — the exact opposite of what is wanted.
const wideB=await page.evaluate((tt,pc)=>{window.__preShown=pc;window.__preRecent=0;
  return window.__bounds(tt,8e15);},steps[steps.length-1],preCount);

// ---- smooth camera target + RATCHETED distance ------------------------------
// The user's complaint was "the camera rotating above keeps zooming in and out",
// and the two halves of the shot are now separated so that only one of them can
// cause it:
//
//   LOOK-AT  follows the recent-work box (damped by A). The active area is the
//            thing worth watching and this is what puts it on screen.
//   DISTANCE never follows the recent-work box, and never follows the live box
//            downward either. It is a RUNNING MAX of the whole-base framing
//            distance: a base only ever grows during the build-out, so the
//            distance that frames it can only ever grow too. Once the camera has
//            pulled out far enough to hold everything, it stays there.
//
// Why the running max and not just the live whole-base extents. Even with the
// recent box out of the distance term, the live box still shrinks: the 0.5/99.5
// percentile edges move as the piece count changes, and during replay pieces are
// removed and the box contracts behind them. Each contraction is a small pull-in
// followed by a push-out on the next frame, which is exactly the pumping the
// complaint is about, just smaller. Measured on Wooden Camp: recent-box zoom
// moved the camera IN on 377/878 frames by up to 6.34%; whole-base-but-live
// still did it on 273/878; the ratchet leaves only the deliberate opening move.
//
// THE ONE REMAINING INWARD MOVE IS THE OPENING, and it is deliberate: HEAD
// blends frame 0 out to the finished base's framing so the video opens on the
// site rather than on a 2 m crate, then closes to the working distance over the
// first HEAD of the run. That is one continuous smoothstep push-in, not an
// oscillation, and the stability line below reports it separately so it cannot
// be mistaken for a regression.
const sm=[]; let cur=null;
// The PRE-FIX camera, solved over the same frames, same damping, same ends
// blend — the camera math is the only variable. Nothing is rendered from it; it
// exists so `camera stability` below can print before/after side by side and the
// claim "the pumping is gone" is a measurement rather than an opinion.
const smOld=[]; let curOld=null;
const A=0.12;
// The distance that frames a w x h box at this FOV/aspect, with the same 1.45
// margin and the same 6 m floor the old inline expression used.
const distFor=(w,h)=>Math.max((Math.max(h,6)/2)/Math.tan(FOV/2),
                              (Math.max(w,6)/2)/Math.tan(Math.atan(Math.tan(FOV/2)*ASPECT)))*1.45;
// The finished base, i.e. the widest the shot is ever asked to be. HEAD and TAIL
// blend toward THIS, and because the ratchet below can never exceed it they can
// only ever push the camera OUT.
const dWide=wideB?distFor(wideB.w,wideB.h):null;
let dPeak=0;                       // running max of the whole-base framing distance
const headFrames=Math.max(1,Math.round(raw.length*HEAD));
for(let i=0;i<raw.length;i++){
  const r=raw[i];
  let b=r.b||(cur?{tx:cur.tx,ty:cur.ty,tz:cur.tz,w:cur.w,h:cur.h,n:0}:{tx:0,ty:0,tz:0,w:10,h:10,n:0});
  // The LOOK-AT still blends to the whole-base centre at both ends of the run:
  // the opening establishes the site and the ending must land on the finished
  // base, not on whichever detail was worked on last.
  let kEnds=0;
  if(wideB){
    const p0=raw.length<2?1:i/(raw.length-1);
    const u=Math.max(0,(p0-(1-TAIL))/TAIL);            // 0 -> 1 across the ending
    const k=u*u*(3-2*u);                       // smoothstep: no kink where it starts
    const v=Math.max(0,1-p0/HEAD);             // 1 at frame 0 -> 0 at p0=HEAD
    const kh=v*v*(3-2*v);                      // same smoothstep, mirrored
    kEnds=Math.max(k,kh);
    if(kEnds>0) b={tx:b.tx+(wideB.tx-b.tx)*kEnds, ty:b.ty+(wideB.ty-b.ty)*kEnds,
                   tz:b.tz+(wideB.tz-b.tz)*kEnds, w:b.w, h:b.h, n:b.n};
  }
  // Distance is derived from the UNBLENDED whole-base extents and then ratcheted,
  // so neither a blend nor a contraction of the live box can pull it in. The
  // ends' blend is applied only where it would push OUT (max(0,...)), which is
  // the normal case since dWide frames the finished base — the guard matters
  // only where a mid-history base was momentarily wider than the final one.
  dPeak=Math.max(dPeak,distFor(b.w,b.h));
  const dEnds=dWide!==null?dPeak+Math.max(0,dWide-dPeak)*kEnds:dPeak;
  const want={tx:b.tx,ty:b.ty,tz:b.tz,w:b.w,h:b.h,d:dEnds};
  cur = cur ? {tx:cur.tx+(want.tx-cur.tx)*A, ty:cur.ty+(want.ty-cur.ty)*A,
               tz:cur.tz+(want.tz-cur.tz)*A, w:want.w, h:want.h,
               d:cur.d+(want.d-cur.d)*A} : want;
  sm.push({...cur});
  // ---- the same frame, solved the OLD way (measurement only) ----------------
  // Verbatim pre-fix behaviour: the ends blend moves the framed EXTENTS as well
  // as the look-at, the distance is read straight off those extents with no
  // ratchet, and the recent-work box is inside those extents (lw/lh).
  {
    let ob=r.b?{tx:r.b.tx,ty:r.b.ty,tz:r.b.tz,w:r.b.lw??r.b.w,h:r.b.lh??r.b.h}
              :(curOld?{tx:curOld.tx,ty:curOld.ty,tz:curOld.tz,w:curOld.w,h:curOld.h}
                      :{tx:0,ty:0,tz:0,w:10,h:10});
    if(wideB){
      const p0=raw.length<2?1:i/(raw.length-1);
      const u=Math.max(0,(p0-(1-TAIL))/TAIL), k=u*u*(3-2*u);
      const v=Math.max(0,1-p0/HEAD), kh=v*v*(3-2*v);
      for(const kk of [k,kh]) if(kk>0)
        ob={tx:ob.tx+(wideB.tx-ob.tx)*kk, ty:ob.ty+(wideB.ty-ob.ty)*kk,
            tz:ob.tz+(wideB.tz-ob.tz)*kk, w:ob.w+(wideB.w-ob.w)*kk, h:ob.h+(wideB.h-ob.h)*kk};
    }
    const wantOld={tx:ob.tx,ty:ob.ty,tz:ob.tz,w:ob.w,h:ob.h,d:distFor(ob.w,ob.h)};
    curOld = curOld ? {tx:curOld.tx+(wantOld.tx-curOld.tx)*A, ty:curOld.ty+(wantOld.ty-curOld.ty)*A,
                       tz:curOld.tz+(wantOld.tz-curOld.tz)*A, w:wantOld.w, h:wantOld.h,
                       d:curOld.d+(wantOld.d-curOld.d)*A} : wantOld;
    smOld.push({...curOld});
  }
}

// ---- LIGHTING: skip whole days, and CLAMP how fast the sun may move ------
// The sun's position is a function of the TIME OF DAY only, so the day COUNT in
// a gap is not something the lighting has to travel through: day 3 14:00 ->
// day 238 15:00 is a one-hour move of the sun, not 235 sunrises. Taking the
// shortest way round the 24 h clock drops the whole-day part automatically —
// this is the "skip days when there's multiple" rule. The elapsed day count is
// still true and still recorded per frame in plan.json; it just is not
// something the lighting animates through.
//
// On top of that, the per-frame movement is CLAMPED, hard. Unclamped, Wooden
// Camp's snapshots imply a MEDIAN of 1.52 in-game days per frame — one and a
// half sunrises every 33 ms. That is a photosensitive-seizure pattern, not a
// timelapse. The clamp is applied HERE, to the final rendered hour, after every
// piece of pacing logic has run, precisely so that no future change to pacing
// can reintroduce strobing: it is a floor on how slow the sun is, not advice.
const CYCLE_FRAMES=240;                  // one full day/night takes >= 240 frames (8 s @30fps)
const MAXSTEP=24/CYCLE_FRAMES;           // => at most 0.1 h of sun movement per frame
const HARD_MAXSTEP=24/120;               // absolute ceiling: never faster than 1 cycle / 4 s
const wrap24=h=>((h%24)+24)%24;
const shortestH=(to,from)=>{let d=wrap24(to-from); if(d>12)d-=24; return d;};
// FORWARD ONLY. Chasing the true hour by the shortest route makes the sun run
// backwards whenever the next snapshot happens to sit slightly earlier in the
// day, and when the data is jumping around by hundreds of days that flips
// almost every frame: the first cut of this dithered the sun back and forth
// across dawn 13 times a second. Real time only goes one way, so the rendered
// clock only goes one way too. It advances toward the true hour at up to
// MAXSTEP, lands exactly on it when it is within reach (quiet stretches, where
// the lighting IS the real hour), and otherwise just keeps sweeping forward at
// the cap — a clean day/night cycle at exactly CYCLE_FRAMES per cycle.
const lightHour=[]; let maxLag=0;
{
  let curH=null;
  for(let i=0;i<N;i++){
    const g=raw[i].gameSec;
    if(!DAYNIGHT||g===null||!GTFILL.ok){lightHour.push(null);continue;}
    const want=wrap24(g/3600);
    if(curH===null) curH=want;
    else{
      const fwd=wrap24(want-curH);            // distance going forward only
      curH=(fwd<=MAXSTEP)?want:wrap24(curH+MAXSTEP);
    }
    maxLag=Math.max(maxLag,Math.abs(shortestH(want,curH)));
    lightHour.push(curH);
  }
}
{
  const d=[];
  for(let i=1;i<N;i++){ const a=lightHour[i-1],b2=lightHour[i];
    if(a===null||b2===null)continue; d.push(Math.abs(shortestH(b2,a))); }
  if(d.length){
    const s=d.slice().sort((p,q)=>p-q);
    const mx=s[s.length-1];
    console.log(`  sun speed: median ${s[s.length>>1].toFixed(4)} h/frame,`+
      ` p99 ${s[Math.floor(s.length*0.99)].toFixed(4)}, max ${mx.toFixed(4)}`+
      `  (cap ${MAXSTEP.toFixed(4)} = 1 cycle / ${CYCLE_FRAMES} frames;`+
      ` slowest full cycle would take ${mx>0?Math.round(24/mx):'inf'} frames)`);
    console.log(`  lighting lags the raw in-game hour by at most ${maxLag.toFixed(2)} h`+
      ` (the clamp trading fidelity for not strobing)`);
    if(mx>HARD_MAXSTEP+1e-9){ console.error(`FATAL: sun moves ${mx} h/frame, over the hard`+
      ` ceiling ${HARD_MAXSTEP}. Refusing to render a strobing video.`); process.exit(1); }
  }
}
// Audit trail: for each frame, which real snapshot it came from, what in-game
// day/hour the DATA says, and what hour the lighting was actually RENDERED at
// after day-skipping and clamping. The gap between the last two columns is the
// clamp doing its job and is meant to be visible here.
//
// camDist / camT* are the SMOOTHED camera solution for the frame, i.e. exactly
// what __orbit is handed. They are written out because "the camera pumps" is a
// measurable claim and not an aesthetic one: the per-frame |delta camDist| and
// |delta camTarget| distributions printed below (and recomputable from this
// file) are the only honest way to say whether a camera change fixed anything.
fs.writeFileSync(`${OUT}/plan.json`, JSON.stringify(plan.map((p,i)=>({
  frame:i, phase:i<PRE_FRAMES?'build-out':'replay', snapshotTs:p.t,
  step:p.j===undefined?null:p.j, change:p.k===undefined?null:p.k,
  who:raw[i].who??null, kind:raw[i].kind??null, attribution:raw[i].prov??null,
  gameDay:p.gameSec===null?null:Math.floor(p.gameSec/DAY),
  gameHour:p.gameSec===null?null:+(((p.gameSec%DAY)/3600).toFixed(3)),
  litHour:lightHour[i]===null?null:+lightHour[i].toFixed(3),
  camDist:+sm[i].d.toFixed(3),
  camTx:+sm[i].tx.toFixed(3), camTy:+sm[i].ty.toFixed(3), camTz:+sm[i].tz.toFixed(3),
  // The pre-fix camera solved over this same frame. NOT rendered — it is here so
  // the before/after in the log can be recomputed from the file by anyone who
  // doubts it, which is the whole point of reporting a number instead of a claim.
  camDistLegacy:+smOld[i].d.toFixed(3),
  camTxLegacy:+smOld[i].tx.toFixed(3), camTyLegacy:+smOld[i].ty.toFixed(3),
  camTzLegacy:+smOld[i].tz.toFixed(3),
}))));
// ---- camera stability, measured ---------------------------------------------
// Reported every run so a regression is visible in the log rather than only in
// the eye. dd = frame-to-frame change in orbit DISTANCE, as a percentage of the
// distance itself (a 1 m twitch means something different at 20 m than at
// 200 m); dt = frame-to-frame change in the LOOK-AT point, in metres.
{
  const pct=(a,q)=>{const s=a.slice().sort((x,y)=>x-y);return s[Math.min(s.length-1,Math.floor(s.length*q))];};
  // One measurement, run over both camera solutions. `pullsAfterHead` is the
  // number that matters: inward moves before headFrames are the deliberate
  // one-way opening push-in (see the HEAD note above), everything after it is
  // pumping and should be zero now that the distance is ratcheted.
  const measure=(S)=>{
    const dd=[],dt=[];
    for(let i=1;i<S.length;i++){
      dd.push(Math.abs(S[i].d-S[i-1].d)/Math.max(1e-6,S[i-1].d)*100);
      dt.push(Math.hypot(S[i].tx-S[i-1].tx,S[i].ty-S[i-1].ty,S[i].tz-S[i-1].tz));
    }
    let pulls=0,maxPull=0,pullsAfterHead=0,maxPullAfterHead=0;
    for(let i=1;i<S.length;i++){const d=S[i-1].d-S[i].d; if(d>0){
      pulls++;maxPull=Math.max(maxPull,d/S[i-1].d*100);
      if(i>=headFrames){pullsAfterHead++;maxPullAfterHead=Math.max(maxPullAfterHead,d/S[i-1].d*100);}}}
    // PUMPING IS REVERSALS, not inward frames. One long damped push-in registers
    // as hundreds of inward frames and is not a pump; in-out-in-out at the same
    // amplitude is. Counting sign flips of the distance derivative separates the
    // two, and it is what stops the `pullsAfterHead` split from being gamed by
    // where exactly the opening window is drawn (the damped opening move
    // overshoots headFrames by a frame or two on a short base).
    let rev=0,prev=0;
    for(let i=1;i<S.length;i++){
      const dl=S[i].d-S[i-1].d;
      if(Math.abs(dl)<1e-9) continue;
      const s=Math.sign(dl);
      if(prev&&s!==prev) rev++;
      prev=s;
    }
    return {dd,dt,pulls,maxPull,pullsAfterHead,maxPullAfterHead,rev,n:S.length-1};
  };
  const fmt=(m)=>`|d dist| p50 ${pct(m.dd,0.5).toFixed(3)}% p90 ${pct(m.dd,0.9).toFixed(3)}%`+
    ` p99 ${pct(m.dd,0.99).toFixed(3)}% max ${pct(m.dd,1).toFixed(3)}%;`+
    ` |d target| p50 ${pct(m.dt,0.5).toFixed(3)}m p90 ${pct(m.dt,0.9).toFixed(3)}m`+
    ` p99 ${pct(m.dt,0.99).toFixed(3)}m max ${pct(m.dt,1).toFixed(3)}m;`+
    ` moved IN on ${m.pulls}/${m.n} frames (largest ${m.maxPull.toFixed(3)}%),`+
    ` ${m.pullsAfterHead} of them outside the deliberate opening push-in`+
    ` (first ${headFrames} frames) (largest ${m.maxPullAfterHead.toFixed(3)}%);`+
    ` ${m.rev} in/out DIRECTION REVERSALS (this is the pumping metric)`;
  const now=measure(sm), was=measure(smOld);
  if(now.dd.length){
    console.log(`  camera stability AFTER  (rendered): ${fmt(now)}`);
    console.log(`  camera stability BEFORE (pre-fix camera, same frame plan, same damping,`+
      ` recomputed not rendered): ${fmt(was)}`);
    console.log(`  camera pumping removed: in/out reversals ${was.rev} -> ${now.rev};`+
      ` inward moves outside the opening`+
      ` ${was.pullsAfterHead} -> ${now.pullsAfterHead}, largest single pull-in`+
      ` ${was.maxPullAfterHead.toFixed(3)}% -> ${now.maxPullAfterHead.toFixed(3)}%.`+
      ` The distance is now a running max of the WHOLE-BASE framing distance, so it can`+
      ` only ratchet outward; the recent-work box steers the LOOK-AT only.`);
  }
}
// ---- pass 2: render ----
// ---- frame cache: reuse is only valid if the PLAN is unchanged -----------
// Rendering skips any f_NNNN.png that already exists, which saves a lot of time
// on a resumed run and is silently wrong when anything about the plan has
// changed: after the pacing/ordering/lighting fixes, a rerun into a directory
// holding an older render spliced old frames and new frames into one video.
// So the plan is fingerprinted; a mismatch clears the directory rather than
// producing a mixed result.
{
  const sig=JSON.stringify({v:4,N,PRE_FRAMES,PPF,TURNS,DAYNIGHT,GAMEPACE,STANCE,WILDPVP,
    ending:[process.env.NOENDING||'',process.env.ENDING_HOLD||'60',process.env.ENDING_GONE||'90'],
    preCount,steps:steps.length,buildoutGame,
    light:lightHour.map(h=>h===null?null:+h.toFixed(3)),
    shown:plan.map(p=>p.preShown), t:plan.map(p=>p.t),
    cam:sm.map(c=>[+c.tx.toFixed(2),+c.ty.toFixed(2),+c.tz.toFixed(2),+c.d.toFixed(2)])});
  const sigFile=`${OUT}/sig.json`;
  const old=fs.existsSync(sigFile)?fs.readFileSync(sigFile,'utf8'):null;
  if(old!==sig){
    const stale=fs.readdirSync(OUT).filter(f=>/^f_\d+\.png$/.test(f));
    for(const f of stale) fs.unlinkSync(`${OUT}/${f}`);
    if(stale.length) console.log(`  plan changed -> discarded ${stale.length} stale frames in ${OUT}`);
    fs.writeFileSync(sigFile,sig);
  }
}
// ONLY_FRAMES=0,80,160,239 renders just those frames and skips the rest. The
// whole plan is still computed, so the camera and lighting on a listed frame
// are identical to what a full render would give it — which is what makes it
// usable for an A/B contact sheet without paying for two entire renders.
const ONLY=process.env.ONLY_FRAMES
  ? new Set(process.env.ONLY_FRAMES.split(',').map(s=>parseInt(s.trim())))
  : null;
if(ONLY) console.log(`  ONLY_FRAMES: rendering ${ONLY.size} of ${N} frames`);

// ---- warm-up: draw everything once, and THROW AWAY one screenshot -------
// The first screenshot a process takes comes back as the bare background
// (mean luminance exactly 35.0, against ~71 once anything is drawn) no matter
// how long we wait first — it captures a compositor frame from before the
// scene was populated. Waiting longer does not fix it; taking a screenshot
// and discarding it does. Showing every object during the warm-up also gets
// all the meshes resident, so the 55 ms between real frames is genuinely
// enough afterwards.
//
// Caught by an A/B contact sheet whose first sampled column came out
// background-grey in BOTH rows: the defect follows whichever frame the process
// captures first, which is frame 0 in a normal run and the first listed frame
// under ONLY_FRAMES.
// A fixed wait is not enough — it is a race, and it was still losing about
// half the time. So: capture repeatedly until two consecutive captures are
// byte-identical, i.e. the scene has actually stopped changing.
await page.evaluate((tt,pc)=>window.__frame(tt,pc),steps[steps.length-1],preCount);
{
  let prev=null,stable=false;
  for(let k=0;k<20;k++){
    await new Promise(r=>setTimeout(r,400));
    const shot=await page.screenshot();
    if(prev&&Buffer.compare(prev,shot)===0){stable=true;break;}
    prev=shot;
  }
  console.log(`  warm-up: scene ${stable?'settled':'DID NOT settle in 8s'} before timing started`);
}
// The warm-up above draws every OBJECT, which is what makes 55 ms enough for
// the build meshes — but it never mounts a builder, so the player GLBs are
// still unparsed when frame 0 is captured. useGLTF then suspends,
// PlayerLayer's <Suspense fallback={null}> draws nobody, and frame 0 comes out
// with no avatar while every later frame has one. (Exactly what a probe showed:
// zero __avatarPart nodes at frame 0, three at frame 1.) preloadPlayers is
// fire-and-forget and cannot be awaited, so mount a real builder and wait for
// the meshes to actually appear in the scene graph before timing starts.
if(ACTORS) {
  const ready=await page.evaluate(async ()=>{
    const b=window.__builderAt(1,window.__firstT||0)||window.__builderAt(2,window.__firstT||0);
    if(!b||!window.__mappalCam.setBuilder) return 'no builder to warm';
    window.__mappalCam.setBuilder(b);
    for(let k=0;k<60;k++){
      await new Promise(r=>setTimeout(r,100));
      let n=0; window.__mappalCam.scene.traverse(o=>{if(o.name==='__avatarPart')n++;});
      if(n>0) return `avatar meshes resident after ${k*100} ms`;
    }
    return 'AVATAR MESHES DID NOT PARSE IN 6s';
  });
  console.log(`  warm-up: ${ready}`);
}

// ---- WILD PAL SHEET (diagnostic) ------------------------------------------
// PAL_SHEET=<CharacterID> renders one close-up of a Pal selected from this
// base's real day spawn draw. The Pal keeps the exact authored spawner
// coordinate carried by wildpals_draw_<base>.json; clearing the rest of the
// scene only makes its extracted mesh and texture large enough to inspect.
if(process.env.PAL_SHEET){
  const wanted=process.env.PAL_SHEET;
  const info=await page.evaluate(async wanted=>{
    const all=window.__wildAt(8);
    const pal=all.find(p=>p.char===wanted)||all[0];
    if(!pal) return null;
    window.__mappalCam.setObjects([]);
    window.__mappalCam.setTerrain([]);
    window.__mappalCam.setPlayers([]);
    window.__mappalCam.setBuilder(null);
    window.__mappalCam.setPals([pal]);
    window.__mappalCam.setDaylight(11);
    const p=window.__toScene(pal.x,pal.y,pal.z);
    window.__mappalCam.setView([p[0]+2.6,p[1]+1.25,p[2]+2.6],[p[0],p[1]+0.65,p[2]]);
    return {char:pal.char,id:pal.id,url:pal.url,p};
  },wanted);
  if(!info){
    console.log(`PAL_SHEET ${wanted}: no day-eligible wild Pal in this base`);
  }else{
    await new Promise(r=>setTimeout(r,1800));
    const f=`${OUT}/pal_${info.char}.png`;
    await page.screenshot({path:f});
    console.log(`PAL_SHEET ${info.char} ${info.id} at authored scene coordinate `+
      `${info.p.map(v=>+v.toFixed(2)).join(',')} -> ${f}`);
  }
  await browser.close();
  process.exit(info?0:1);
}


// ---- AVATAR SHEET (diagnostic) ---------------------------------------------
// AVATAR_SHEET=<uid>@<ts>,<uid>@<ts>,... renders ONE close-up per (player,
// moment) and exits without rendering the timelapse.
//
// It is in the same family as CLOSEUP and WHO — a way to answer a question that
// is otherwise only checkable by eye across hundreds of wide frames — and it
// exists for one specific reason: "the avatar has clothes on" and "the avatar
// has the RIGHT clothes on, in the right pose, with the head equipment in the
// right place" are different claims. It goes through the REAL __avatarLook (so:
// avatars.json look -> __dress -> override/equipped precedence -> posed
// equipment URLs), not a reimplementation, which is the only way a passing
// picture means anything about the frames the render actually writes.
//
// The uid+ts pairs pick out the moments that decide whether the precedence rule
// is right — an override active, an override absent, a socket-attached helmet,
// a player with genuinely empty containers.
if(process.env.AVATAR_SHEET){
  const specs=process.env.AVATAR_SHEET.split(',').map(x=>x.trim()).filter(Boolean);
  // Clear the base and its Pals: the sheet is about the CHARACTER, and at the
  // avatar's own position it is otherwise half behind the Palbox.
  await page.evaluate(()=>{
    if(window.__mappalCam.setDaylight) window.__mappalCam.setDaylight(11);
    window.__mappalCam.setObjects([]);
    if(window.__mappalCam.setPals) window.__mappalCam.setPals([]);
    if(window.__mappalCam.setPlayers) window.__mappalCam.setPlayers([]);
    if(window.__mappalCam.setTerrain) window.__mappalCam.setTerrain([]);
  });
  for(const spec of specs){
    const [uid,tsRaw]=spec.split('@'); const ts=parseInt(tsRaw,10);
    const info=await page.evaluate((uid,ts)=>{
      const look=window.__avatarLook(uid,ts);
      if(!look) return {err:'no look for '+uid+' at '+ts};
      // Place it wherever __builderAt would put this base's first piece — the
      // position is irrelevant to what is being checked, only the LOOK is.
      const at=window.__builderAt(1,ts)||{x:0,y:0,z:0};
      window.__mappalCam.setBuilder({uid,name:look.name,parts:look.parts,
        appearanceExact:look.exact,x:at.x,y:at.y,z:at.z,qx:0,qy:0,qz:0,qw:1});
      const eq=window.__eqLook(uid,ts);
      return {name:look.name,exact:look.exact,parts:look.parts.map(p=>p.url+(p.attach?' @'+p.attach.socket:'')),
              body:eq&&eq.bodySource,head:eq&&eq.head?(eq.head.source||eq.head.unplaceable):'none',
              bodyType:eq&&eq.bodyType,overrideSource:eq&&eq.overrideSource};
    },uid,ts);
    if(info.err){ console.log('AVATAR_SHEET '+spec+': '+info.err); continue; }
    // Wait for the parts to be parsed and resident, then frame them from their
    // own world bounding box so the shot is the same size whoever it is.
    let box=null;
    for(let k=0;k<60;k++){
      await new Promise(r=>setTimeout(r,120));
      box=await page.evaluate((n)=>{
        const sc=window.__mappalCam.scene; let found=0;
        const lo=[1e9,1e9,1e9], hi=[-1e9,-1e9,-1e9];
        sc.traverse(o=>{ if(o.name!=='__avatarPart') return; found++;
          o.updateWorldMatrix(true,false);
          const g=o.geometry; if(!g.boundingBox) g.computeBoundingBox();
          const b=g.boundingBox.clone().applyMatrix4(o.matrixWorld);
          lo[0]=Math.min(lo[0],b.min.x);lo[1]=Math.min(lo[1],b.min.y);lo[2]=Math.min(lo[2],b.min.z);
          hi[0]=Math.max(hi[0],b.max.x);hi[1]=Math.max(hi[1],b.max.y);hi[2]=Math.max(hi[2],b.max.z);});
        return found===n?{lo,hi,found}:null;
      },info.parts.length);
      if(box) break;
    }
    if(!box){ console.log('AVATAR_SHEET '+spec+': parts did not all become resident'); continue; }
    const c=[(box.lo[0]+box.hi[0])/2,(box.lo[1]+box.hi[1])/2,(box.lo[2]+box.hi[2])/2];
    const h=Math.max(box.hi[1]-box.lo[1],0.5), D=h*2.1;
    await page.evaluate((c,D)=>window.__mappalCam.setView([c[0]+D*0.72,c[1]+D*0.30,c[2]+D*0.72],c),c,D);
    await new Promise(r=>setTimeout(r,650));
    const f=`${OUT}/avatar_${uid}_${ts}.png`;
    await page.screenshot({path:f});
    console.log(`AVATAR_SHEET ${(info.name||uid).padEnd(11)} @${ts}  body=${info.body}  head=${info.head}`+
      `  bodyType=${info.bodyType}/${info.overrideSource}  height=${h.toFixed(2)}m  parts=${JSON.stringify(info.parts)}  -> ${f}`);
  }
  await browser.close();
  process.exit(0);
}

let done=0,skipped=0; const start=Date.now();
for(let i=0;i<N;i++){
  const f=`${OUT}/f_${String(i).padStart(4,'0')}.png`;
  if(ONLY&&!ONLY.has(i)){skipped++;continue;}
  if(fs.existsSync(f)){skipped++;continue;}
  const frac=N===1?0:i/(N-1);
  const c=sm[i];
  const az=frac*360*TURNS+25, el=28+10*Math.sin(frac*Math.PI*2);
  const hour=lightHour[i];        // already day-skipped and speed-clamped above
  const cnt=await page.evaluate((tt,ps,a,d,e,tx,ty,tz,hr,act,sj,sk)=>{
    // Build-out frames are driven by preShown; replay frames by (step, change).
    const n=(sj===null||sj===undefined)?window.__frame(tt,ps):window.__frameStep(sj,sk);
    window.__orbit(a,d,e,tx,ty,tz);
    if(window.__mappalCam.setDaylight) window.__mappalCam.setDaylight(hr);
    if(act&&window.__mappalCam.setPals){
      // The base's OWN recorded Pals, plus the world's wild spawn points for the
      // surrounding ground. Two different kinds of data drawn by one layer:
      // __palsAt is each Pal's recorded LastJumpedLocation at this snapshot,
      // __wildAt is authored spawn-table content that does not change with the
      // save at all (only with the in-game hour). Neither affects camera framing.
      window.__mappalCam.setPals(window.__palsAt(tt).concat(window.__wildAt(hr)));
      window.__mappalCam.setPlayers(window.__playersAt(tt));
      // Whoever the frame says is acting: during the build-out the player
      // credited with the piece revealed at this preShown, during the replay the
      // one placing or removing change k of step j. A frame that changes nothing
      // (a step whose surplus frames only let the clock run) draws NOBODY —
      // which is the honest answer, and better than the old behaviour of
      // re-drawing the last build-out builder standing still for the whole
      // replay. Recorded players keep being drawn by setPlayers either way.
      if(window.__mappalCam.setBuilder){
        const bb=(sj===null||sj===undefined)?window.__builderAt(ps,tt)
                                            :window.__changePose(sj,sk,tt);
        if(bb){ const s=window.__drawStats||(window.__drawStats={});
                const key=(bb.kind||'build')+':'+bb.attribution; s[key]=(s[key]||0)+1; }
        const draw=(bb&&bb.parts)?bb:null;
        window.__whoNow=draw?draw.name:null;
        window.__poseNow=draw||null;    // CLOSEUP aims at THIS, not at a scene scan
        window.__mappalCam.setBuilder(draw);
      }
    }
    return n;},
    raw[i].t,raw[i].preShown,az,c.d,el,c.tx,c.ty,c.tz,hour,ACTORS,
    raw[i].j===undefined?null:raw[i].j, raw[i].k===undefined?0:raw[i].k);
  // 55 ms is enough for an incremental change, but the first frame this process
  // actually draws follows the whole scene being built and needs longer: at
  // 55 ms it screenshotted a half-drawn scene, giving that frame a mean
  // luminance of 35 against 71 for every frame after it — a 36/255 flash, the
  // single largest luminance step in the video. Keyed on `done`, not on i,
  // because with ONLY_FRAMES or a resumed run the first drawn frame is not
  // frame 0 (that is exactly how this was caught: an A/B sheet whose first
  // sampled column came out black in both rows).
  await new Promise(r=>setTimeout(r,done===0?800:55));
  await page.screenshot({path:f,optimizeForSpeed:true});
  // CLOSEUP=<metres> writes a SECOND screenshot per frame, taken from that
  // distance off the builder avatar, then restores the framing on the next
  // frame. Diagnostic only and off by default, but keep it: "is the avatar
  // drawing at all" and "is the avatar big enough to see" are different
  // questions, and confusing them is what made this take three attempts. A
  // closeup answers the first one in one image.
  if(process.env.CLOSEUP){
    // Aim at the pose the frame ACTUALLY DREW (window.__poseNow, straight from
    // __builderAt/__changePose), so the closeup answers "is the credited actor
    // there, at the piece that changed" rather than "is there a human somewhere
    // in this scene". A frame that draws nobody says so and writes no closeup.
    const wp=await page.evaluate(()=>{const b=window.__poseNow;
      return b?{p:window.__toScene(b.x,b.y,b.z),who:b.name||b.uid,kind:b.kind||'build',
                prov:b.attribution}:null;});
    if(wp){
      const D=parseFloat(process.env.CLOSEUP);
      await page.evaluate((t,d)=>{window.__mappalCam.setView([t[0]+d,t[1]+d*0.4,t[2]+d],[t[0],t[1]+0.8,t[2]]);},wp.p,D);
      await new Promise(r=>setTimeout(r,600));
      await page.screenshot({path:`${OUT}/closeup_${String(i).padStart(4,'0')}.png`});
      console.log(`CLOSEUP frame ${i}: ${wp.who} ${wp.kind}:${wp.prov} at ${wp.p.map(v=>+v.toFixed(2)).join(',')}`);
    } else console.log(`CLOSEUP frame ${i}: this frame draws NOBODY`);
  }
  // WHO=1 logs the name on the tag per frame. Diagnostic, off by default, and
  // kept because "does the avatar switch when the recorded builder changes" is
  // otherwise only checkable by eye across hundreds of frames.
  if(process.env.WHO){
    const w=await page.evaluate(()=>window.__whoNow);
    console.log('WHO',i,w);
  }
  done++;
  if(done%60===0){
    const hh=hour===null?'--':`${String(Math.floor(hour)).padStart(2,'0')}:${String(Math.floor(hour%1*60)).padStart(2,'0')}`;
    const dayNo=raw[i].gameSec===null?'?':Math.floor(raw[i].gameSec/DAY);
    console.log(`  ${i+1}/${N} alive=${cnt} dist=${c.d.toFixed(0)} game-day ${dayNo} ${hh} ${((Date.now()-start)/1000).toFixed(0)}s`);
  }
}

// ---- THE ENDING -------------------------------------------------------------
// Three of the four bases are still standing in the last save we hold, and for
// those there is nothing to show past the build: endoflife_<b>.json says so in
// its own words and this block prints that and stops.
//
// Lost Camp is different. It is GONE. The last save that contains it is
// 2026-08-11 16:31:40, where the camp is intact — 63 built pieces and its own
// BaseCampSaveData record. The next save of this world we hold is 2026-08-16
// 04:03:04, and in it the camp record is gone and so is every one of those
// pieces. Until now the render simply STOPPED at the last snapshot, which shows
// a finished camp and says nothing — the single most important fact about this
// base was the one thing the video left out.
//
// WHAT IS DRAWN, and how little of it is ours. The "gone" frames are not a
// special empty scene: they are __frame() called at the real timestamp of the
// first snapshot that lacks the camp. Every piece's recorded last-seen is
// before that instant, so the ordinary lifetime test drops all of them by
// itself. The terrain and the wild spawn points stay because they are world
// content and did not go anywhere. The base's own Pals stay for a recorded
// reason: all 22 that stood in the camp at its last sighting are still in the
// save afterwards, most of them at the same spot on the now camp-less site.
//
// WHAT THE CAPTION MUST SAY, and this is the whole reason it is a caption and
// not just a cut. This save format records NO placement or removal timestamps
// for anything. What the history contains is "present in this snapshot, absent
// in that one", with 4d 11h 31m 24s between them in which we hold no usable
// save of this world at all. So the disappearance is a WINDOW, not a moment,
// and the file it comes from says in as many words: do not render it as an
// instant unless it is labelled as "sometime in this window". It is labelled.
// Nothing here claims to know who, why, or exactly when.
const EOL=(()=>{ try{ return JSON.parse(fs.readFileSync(`${UNION_DIR}/endoflife_${BASE}.json`)); }
                 catch(e){ return null; } })();
if(EOL&&!EOL.endOfLife){
  const summary=EOL.timeline?.[0]?.what||EOL.note||'No end-of-life event is recorded.';
  const finalAt=EOL.finalSnapshot?.at||'no final snapshot';
  console.log(`  end of life: ${EOL.name} has no ending to show (${finalAt}). ${summary}`);
}
if(EOL&&EOL.endOfLife&&!process.env.NOENDING){
  const E=EOL.endOfLife;
  const HOLD=parseInt(process.env.ENDING_HOLD||'60');     // frames on the camp as it stood
  const GONE=parseInt(process.env.ENDING_GONE||'90');     // frames on the site without it
  const cam=sm[N-1];
  const setCaption=(title,lines)=>page.evaluate((t,ls)=>{
    let el=document.getElementById('__eolcap');
    if(!el){ el=document.createElement('div'); el.id='__eolcap';
      el.style.cssText='position:fixed;left:0;right:0;bottom:0;z-index:9;padding:22px 40px 30px;'+
        'font:400 17px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#e8edf4;'+
        'background:linear-gradient(to top,rgba(6,9,14,.92),rgba(6,9,14,.72) 60%,rgba(6,9,14,0));'+
        'text-align:center;letter-spacing:.01em';
      document.body.appendChild(el); }
    el.innerHTML='<div style="font:600 22px/1.35 inherit;letter-spacing:.08em;text-transform:uppercase">'+t+
      '</div>'+ls.map(x=>'<div style="margin-top:8px;opacity:.88">'+x+'</div>').join('');
  },title,lines);
  const shot=async(k)=>{
    const path=`${OUT}/f_${String(N+k).padStart(4,'0')}.png`;
    // The render signature above deletes stale frames whenever the plan or
    // ending settings change.  If the signature still matches, a completed
    // coda frame is safe to resume just like an ordinary replay frame.
    if(fs.existsSync(path)) return;
    await new Promise(r=>setTimeout(r,140));
    await page.screenshot({path,optimizeForSpeed:true});
  };
  // The world clock at each of the two real snapshots, so the light is that
  // snapshot's own in-game hour rather than wherever the replay left the sun.
  const eolHour=(ts)=>{ const g=GSEC_RAW(ts); return g===null?null:((g%DAY)/3600); };
  let k=0;
  // --- the camp as the last save that contains it has it ---
  await page.evaluate((tt,pc,a,d,e,tx,ty,tz,hr,act)=>{
    window.__frame(tt,pc); window.__orbit(a,d,e,tx,ty,tz);
    if(window.__mappalCam.setDaylight) window.__mappalCam.setDaylight(hr);
    if(act&&window.__mappalCam.setPals) window.__mappalCam.setPals(window.__palsAt(tt).concat(window.__wildAt(hr)));
    if(act&&window.__mappalCam.setBuilder) window.__mappalCam.setBuilder(null);
    if(act&&window.__mappalCam.setPlayers) window.__mappalCam.setPlayers([]);
  },E.lastSeen.ts,preCount,(N-1)/(N>1?N-1:1)*360*TURNS+25,cam.d,28,cam.tx,cam.ty,cam.tz,
    eolHour(E.lastSeen.ts),ACTORS);
  await setCaption(`${EOL.name}`,
    [`${E.lastSeen.at} — the last save of this world that contains this camp.`,
     `${E.builtPiecesStandingAtLastSight} built pieces and its own BaseCampSaveData record, all present.`]);
  for(let i=0;i<HOLD;i++) await shot(k++);
  // --- the same ground in the next save we hold ---
  // __frame returns how many objects it drew. It is not always zero, and the
  // caption must not claim otherwise: a handful of this site's rows carry NO
  // recorded lifetime at all (272 of Lost Camp's 278 union rows have one), and
  // an object the save never dates is an object the save never says was
  // removed, so the ordinary rule keeps drawing it. Those are counted and named
  // on screen rather than quietly swept out of the shot.
  const leftDrawn=await page.evaluate((tt,pc,hr,act)=>{
    const n=window.__frame(tt,pc);
    if(window.__mappalCam.setDaylight) window.__mappalCam.setDaylight(hr);
    if(act&&window.__mappalCam.setPals) window.__mappalCam.setPals(window.__palsAt(tt).concat(window.__wildAt(hr)));
    return n;
  },E.firstAbsent.ts,preCount,eolHour(E.firstAbsent.ts),ACTORS);
  await setCaption('Gone',
    [`${E.firstAbsent.at} — the next save of this world we hold.`,
     `The camp record is gone, and so are all ${E.builtPiecesStandingAtLastSight} built pieces that stood here.`+
     ` It never returns.`,
     `<span style="opacity:.72">This save format records no removal time for anything. ${E.gapHuman} separate`+
     ` those two saves, so the disappearance is that WINDOW, not a moment — it could have happened at any`+
     ` point inside it. The Pals did not go with it: all ${EOL.pals.atLastSighting.count} standing here at the`+
     ` camp's last sighting are still in the save afterwards.`+
     (leftDrawn?` ${leftDrawn} row${leftDrawn===1?'':'s'} still drawn on this ground carr${leftDrawn===1?'ies':'y'}`+
       ` no recorded lifetime at all, so nothing in the save says they were ever removed either.`:'')+
     `</span>`]);
  for(let i=0;i<GONE;i++) await shot(k++);
  await page.evaluate(()=>{const el=document.getElementById('__eolcap'); if(el) el.remove();});
  console.log(`  ENDING: wrote ${k} frames — ${HOLD} on ${EOL.name} as it last stood`+
    ` (${leftDrawn} undatable row${leftDrawn===1?'':'s'} still drawn after the camp goes, stated in the caption)`+
    ` (${E.lastSeen.at}, ${E.builtPiecesStandingAtLastSight} pieces) and ${GONE} on the same ground in the`+
    ` next save (${E.firstAbsent.at}), where __frame's ordinary recorded-lifetime test drops every piece`+
    ` by itself. The caption states the ${E.gapHuman} gap as a window, not a moment — the save records`+
    ` no removal time and this render does not invent one.`);
}

// What the frames that were ACTUALLY RENDERED drew, keyed by change kind and
// attribution source. The setup-time `replay changes` line above says what the
// data contains; this one says what this run put on screen, so a run rendered
// with ONLY_FRAMES or a MAXFRAMES cap cannot be mistaken for a full one.
const _dstat=await page.evaluate(()=>({draws:window.__drawStats||{},
  demos:(window.__demolitions||[]).length}));
console.log(`  avatar draws this run, by kind:attribution: ${JSON.stringify(_dstat.draws)}`+
  ` (build:* is the reconstructed build-out, add:* an arrival, remove:* a departure;`+
  ` ${_dstat.demos} of the departures are paired with a replacement in demolitions_${BASE}.json`+
  ` and the rest are attributed to whoever BUILT the piece, or carried forward — all of it inference,`+
  ` since the save records no destruction event at all)`);
console.log(`${BASE} done: rendered ${done}, reused ${skipped}, ${((Date.now()-start)/1000).toFixed(0)}s`);
await browser.close();
