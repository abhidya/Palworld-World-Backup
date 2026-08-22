"""Compose SK_PalHuman_Skeleton's attach sockets onto a BAKED ANIMATION FRAME.

WHY. Extract.cs bakes one frame of a real in-game AnimSequence into vertex
positions, which is how the avatar stopped being T-posed. That fixes every mesh
that is SKINNED to the player skeleton — armour, hair, heads, and
SK_HeadEquip052, which the extraction report shows carries 82 matched bones and
whose bounds move from z 123.8..169.8 (bind) to 71.8..120.0 (kneeling frame).
It does NOT move a RIGID prop: SK_HeadEquip034 has ONE bone (`root`), root is
identity in both baked frames, so its vertices come out exactly as they went in,
ten centimetres across, in socket-local space. A prop like that is placed by a
SOCKET transform, and eq_sockets.json / the `sockets` block of
equipment_<base>.json give those in the BIND pose only — which would hang the
helmet where the head is when the arms are straight out, i.e. floating in front
of a kneeling avatar's chest.

WHAT THIS DOES. Takes the socket's own relative (bone, loc, rot) — read data,
straight off the SkeletalMeshSocket exports, carried verbatim in the union file
— and composes it onto the bone's COMPONENT-SPACE transform at the baked frame,
dumped by `palxtex --posebones` from the same Component() the vertex bake uses.
Pure FK. No offset is invented anywhere; the attachment and the body it sits on
are evaluated from the same animation frame, so they cannot disagree.
"""
import json, math, os

SP = os.path.dirname(os.path.abspath(__file__))


def qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def qrot(q, v):
    x, y, z, w = q
    vx, vy, vz = v
    tx, ty, tz = 2 * (y * vz - z * vy), 2 * (z * vx - x * vz), 2 * (x * vy - y * vx)
    return (vx + w * tx + (y * tz - z * ty),
            vy + w * ty + (z * tx - x * tz),
            vz + w * tz + (x * ty - y * tx))


def compose(parent, local):
    pt, pq = parent
    lt, lq = local
    r = qrot(pq, lt)
    return ([pt[0] + r[0], pt[1] + r[1], pt[2] + r[2]], list(qmul(pq, lq)))


SOCKETS = json.load(open(f"{SP}/mappal/public/union/equipment_16fca097.json"))["sockets"]
STANCES = {"kneel": "posebones_kneel.json", "standing": "posebones_stand.json"}

out = {"note": __doc__.strip(), "anim": {}, "sockets": {}}
posed = {k: json.load(open(f"{SP}/{v}")) for k, v in STANCES.items()}
for k, d in posed.items():
    out["anim"][k] = {"animName": d["animName"], "frame": d["frame"],
                      "numFrames": d["numFrames"], "asset": d["anim"]}

missing = []
for name, s in SOCKETS.items():
    bone = s["bone"]
    rel = s["relative"]
    lt = rel["t"]
    lq = rel["q"] or [0.0, 0.0, 0.0, 1.0]
    rec = {"bone": bone}
    for stance, d in posed.items():
        b = d["bones"].get(bone)
        if b is None:
            missing.append((name, bone))
            continue
        t, q = compose((b["t"], b["q"]), (lt, lq))
        rec[stance] = {"t": [round(v, 4) for v in t], "q": [round(v, 6) for v in q]}
    out["sockets"][name] = rec

json.dump(out, open(f"{SP}/mappal/public/union/equipment_sockets_posed.json", "w"), indent=1)
print(f"wrote equipment_sockets_posed.json: {len(out['sockets'])} sockets, "
      f"{len(missing)} with no bone in the animation skeleton {missing}")
for n in ("Socket_HairAttach_HeadEquip_front03", "Socket_Weapon_R", "Socket_BackWeapon_R"):
    r = out["sockets"].get(n)
    if not r:
        continue
    bind = SOCKETS[n]["bindPose"]["TypeA"]["t"]
    print(f"  {n:42s} bone={r['bone']:10s} bind={[round(v,1) for v in bind]} "
          f"kneel={[round(v,1) for v in r['kneel']['t']]} stand={[round(v,1) for v in r['standing']['t']]}")
