/* buildorder.js — RECONSTRUCTED build order for the timelapse's build-out phase.
 *
 * WHAT IS REAL AND WHAT IS NOT
 * ----------------------------
 * Every piece, its typeId and its world position are real, read out of the save
 * history.  The ORDER below is NOT: pieces that were already standing in the
 * earliest surviving snapshot have no per-piece timestamp anywhere in the data,
 * because nothing recorded them being placed.  Their real order is unrecoverable.
 * This file derives a *plausible* order from the geometry that does survive, and
 * it is used ONLY for those undated pieces.  Pieces that do carry a real
 * first-seen timestamp are replayed on their real timestamps and never touch
 * this code.
 *
 * HOW THE ORDER IS DERIVED
 * ------------------------
 * Palworld snaps building pieces to a per-base lattice: 400 cm horizontally,
 * 325 cm vertically (mappal/src/data/objects.json: gridPitch / verticalPitch;
 * the Glass Tower's z histogram is exactly 325-spaced, which is what calibrated
 * these numbers).  Piece origins follow that lattice, so:
 *   - a foundation's top surface and the walls/pillars standing on it share a z
 *   - a roof or an upper floor sits one 325 step above the walls carrying it
 * That gives a real support relation:
 *   support(P) = { S : horizontal distance(P,S) <= 420 cm  AND
 *                      ( S is a floor on P's own level and P stands on floors,
 *                        OR S is load-bearing one level below P ) }
 * A piece on the lowest level of its cluster stands on the ground and needs no
 * support.  Edges therefore only ever point upward, or sideways from a floor to
 * the things standing on it, so the graph is acyclic.
 *
 * The emitted order is a genuine topological ordering of that DAG under
 * "a piece may appear once ANY ONE of its supports has appeared" (physically
 * correct: a wall needs one foundation under it, not all of them).  That is
 * the HARD constraint and nothing below is allowed to break it.
 *
 * WHICH ELIGIBLE PIECE GOES NEXT: A WALKING BUILDER, NOT A SWEEP
 * -------------------------------------------------------------
 * A person builds LOCALLY.  They finish a corner, a room, a storey of one
 * wing -- furniture and all -- then walk somewhere else.  They do not lay
 * every foundation in the camp, then every wall in the camp.  So among the
 * pieces that are currently eligible we pick the CHEAPEST by
 *
 *     cost(P) = distance(P, piece just placed)          <- walk to the nearest
 *             + LVL_COST * (P's level - local storey floor)   <- damper, below
 *             + TIER_COST * P's role tier               <- shell before fittings
 *
 * which is depth-first: the builder keeps working outward from where they are
 * standing until the local pocket is exhausted, and only then walks.  This is
 * the same greedy nearest-neighbour rule a person's feet follow.
 *
 * THE LEVEL DAMPER.  Pure nearest-neighbour would let the builder climb one
 * tower leg all the way to the roof while the rest of the ground floor is
 * still bare -- correct by the support graph, absurd to watch.  So a piece is
 * penalised for being above the "local storey floor": the lowest level among
 * the pieces still eligible WITHIN WORK_R of where the builder is standing.
 * In plain terms: do not go upstairs while there is still unbuilt work on
 * this floor within reach.  Once the local pocket's storey is finished the
 * floor rises by itself and going up costs nothing extra, so the builder
 * ascends where they stand instead of teleporting.  LVL_COST must stay SMALL
 * relative to the base -- these span 66-102 m corner to corner, so a climb
 * penalty of tens of metres makes walking to the far side of the current
 * storey cheaper than stepping up, and the damper degenerates into the
 * level-major key it was meant to replace.  It is 2.5 m: enough to prefer the
 * work at your feet, far too little to send the builder hiking across the
 * camp before he is allowed to climb.  See the constant for the sweep.
 *
 * Furniture-before-ceiling is a SEPARATE, explicit constraint (see CEILING
 * EDGES).  An earlier version of this file claimed the damper made such a rule
 * unnecessary -- fittings and ceiling are one storey apart, so a builder who
 * will not go upstairs early furnishes the room first as a side effect.  That
 * is true only while LVL_COST is huge, i.e. only while the walk is breadth
 * first, so it was really a description of the bug.  With the rule stated
 * outright the damper is free to come down and the pocket can be carried
 * upward the way a person builds.  (Two versions before that, a build-class
 * sort pushed every non-structural piece behind the entire shell: you watched
 * a sealed building get furnished through its own roof.)
 *
 * TIER_COST is deliberately small (a room's width, not a base's): inside one
 * pocket the shell goes up before the fittings go in, but it never outweighs
 * walking distance, so the builder does not abandon a half-finished room to
 * go place a foundation somewhere else.
 *
 * Only the Palbox and natural rock/ore nodes sit outside this, in a tier of
 * their own that comes first: the Palbox is what brings the camp into
 * existence, and the rocks were landscape before it.  Without that the Glass
 * Tower's Palbox -- which stands on the top deck, 28 levels up -- would be the
 * last thing to appear in its own base.
 *
 * THE RETURN VALUE is an array of piece ids, first-placed first, so index k is
 * "the k-th piece this builder puts down".  It is a pure function of the input
 * (all tie-breaks end in the piece id), so a consumer -- e.g. a player avatar
 * that needs to stand next to piece k at step k -- can rely on the same input
 * yielding the same order.
 *
 * FALLBACK: if the frontier ever empties while pieces remain — a floating
 * piece, a cluster whose support was demolished before the first snapshot, a
 * type we cannot classify — we release the remaining piece with the lowest
 * (level, distance-from-palbox, id).  That is exactly the elevation sort this
 * replaced, so ambiguous geometry degrades to the old behaviour rather than
 * stalling or inventing a relationship.
 */
(function () {
  var GRID = 400;    // objects.json gridPitch
  var VPITCH = 325;  // objects.json verticalPitch
  var HRAD = 420;    // "same or immediately adjacent snap cell", with slack for
                     // the per-base lattice yaw (docs/CALIBRATION.md: the grid
                     // is rotated by an arbitrary yaw, so world-axis cell
                     // indices are not exact — a radius is).

  /* Role from typeId. The COARSE split is objects.json's own `category` field
   * (structure / production / storage / defense / decor / world); we only refine
   * `structure` into the load-bearing roles, by the naming convention the type
   * ids already use (Wooden_foundation, Glass_pillars, Stone_TriangleRoof, ...).
   * Nothing here invents a category the registry does not already assert. */
  function roleOf(typeId, category) {
    var t = String(typeId || "");
    // Wall-MOUNTED fixtures are named "...Wall..." but bear no load. Must be
    // tested before the wall rule or WallTorch would be treated as structure.
    if (/^(WallTorch|WallSignboard)$/.test(t) || /_Wall(_[A-Za-z0-9]+)?$/.test(t)) return "fixture";
    if (/^PalBoxV2$/.test(t)) return "palbox";      // the camp's first piece by definition
    if (category === "structure" || category === undefined || category === null) {
      if (/foundation/i.test(t)) return "foundation";
      if (/pillar/i.test(t)) return "pillar";
      if (/stairs?|ladder/i.test(t)) return "stair";
      if (/roof/i.test(t)) return "roof";
      if (/wall|gate/i.test(t)) return "wall";
    }
    if (category === "structure") return "structure_other";
    if (category) return category;                   // production | storage | defense | decor | world
    // Not in the registry at all. Two families dominate and are unambiguous
    // from the name; everything else falls into decor so it is revealed late
    // rather than being mistaken for load-bearing structure.
    if (/DamagableRock/i.test(t)) return "natural";  // ore/rock node: terrain, not built
    if (/Egg/i.test(t)) return "item";               // eggs in a breeding pen
    return "decor";
  }

  // Does this piece act as a floor for the level it sits on?
  function isFloor(r) { return r === "foundation" || r === "roof"; }
  // Can this piece carry the level above it?
  function bears(r) {
    return r === "foundation" || r === "roof" || r === "wall" ||
           r === "pillar" || r === "stair" || r === "palbox";
  }
  // Things that stand ON a floor rather than spanning up from the level below.
  function standsOnFloor(r) { return !isFloor(r); }
  // Furnishings: they go INSIDE a room rather than forming its shell. These are
  // the pieces the ceiling rule below protects.
  var NONSTRUCT = { production: 1, storage: 1, defense: 1, decor: 1,
                    fixture: 1, world: 1, item: 1 };

  // These two come before everything else, for the reasons in the header.
  var PRE = { natural: 0, palbox: 0 };
  // Tier WITHIN one pocket of work — the order a bit of storey goes up in.
  // A roof shares tier 0 with foundation because at its own level it IS the
  // floor; it reads as "the cap of the storey below" only because that storey
  // is a lower level and the level damper finishes it first.
  var TIER = {
    foundation: 0, roof: 0,          // the floor of this level
    pillar: 1, wall: 2, stair: 3,    // what stands on it
    structure_other: 4,
    production: 5, storage: 6, defense: 7,
    fixture: 8, decor: 9, world: 10, item: 11   // what goes in the room
  };

  /* The three weights that turn "which eligible piece next" into a walk.
   * All are in centimetres so they are directly comparable to walking distance,
   * which is the whole point: every rule below is expressed as "how far would
   * the builder walk to avoid this?".  See the header for the reasoning. */
  var WORK_R    = 1600;   // 16 m — "the part of the base I am standing in"
  // 2.5 m — climbing costs about one pace, not a walk across the base.
  //
  // This was 3000 (30 m). Against bases that span 66-102 m corner to corner,
  // a 30 m climb penalty meant walking to almost anywhere else on the CURRENT
  // storey was cheaper than going up one — so the builder drained each storey
  // across the whole base before ascending. That is a level-major sweep: the
  // exact breadth-first layering this greedy walk was written to replace.
  //
  // Measured over a sweep (sweep_lvlcost.mjs), share of steps where the level
  // changes at all — low means "stuck on one storey", i.e. BFS:
  //             LVL_COST   3000    250
  //   Glass Tower           13%    31%      level index-span 18% -> 68%
  //   Wooden Camp            4%    18%                       24% -> 47%
  //   Stone Works            2%    15%                       26% -> 67%
  // and adjacency (hop <= 6 m) did not regress: 74/89/92% -> 86/92/93%, with
  // base-crossing hops (> 20 m) staying at or under 1.1% of steps.
  //
  // Dropping it this far is only SAFE because the furniture-before-ceiling rule
  // is now an explicit edge in the graph (see CEILING EDGES). Previously that
  // rule held only as a side effect of this constant being huge, and lowering
  // it below ~900 put furniture inside sealed rooms.
  var LVL_COST  =  250;
  var TIER_COST =   60;   // per tier step; 11 tiers = 6.6 m, about a room

  /* items: [{ id, x, y, z, typeId, category }]  (Unreal cm, z = up)
   * returns: array of ids, first-placed first. */
  function reconstructBuildOrder(items, opts) {
    opts = opts || {};
    var n = items.length;
    if (n === 0) return [];
    var N = items.map(function (it, i) {
      var r = roleOf(it.typeId, it.category);
      return { i: i, id: it.id, x: it.x, y: it.y, z: it.z, role: r,
               pre: PRE[r] === undefined ? 1 : PRE[r],
               tier: TIER[r] === undefined ? 9 : TIER[r] };
    });

    // Level index: quantise z onto the 325 cm vertical lattice, relative to the
    // lowest piece in the base. Terrain-following foundations wobble by a few
    // tens of cm within one storey; rounding absorbs that.
    var zmin = Infinity;
    for (var a = 0; a < n; a++) if (N[a].z < zmin) zmin = N[a].z;
    for (a = 0; a < n; a++) N[a].lvl = Math.round((N[a].z - zmin) / VPITCH);

    // Distance origin: the palbox if the base has one (it is the camp's anchor
    // and always its first piece), else the centroid — matching the old sort.
    var ox = 0, oy = 0, pal = null;
    for (a = 0; a < n; a++) if (N[a].role === "palbox") { pal = N[a]; break; }
    if (pal) { ox = pal.x; oy = pal.y; }
    else { for (a = 0; a < n; a++) { ox += N[a].x / n; oy += N[a].y / n; } }
    for (a = 0; a < n; a++) N[a].rad = Math.hypot(N[a].x - ox, N[a].y - oy);

    // Spatial hash on a GRID-sized bucket so support lookup is local, not O(n^2).
    var buckets = new Map();
    function key(cx, cy, l) { return cx + "|" + cy + "|" + l; }
    for (a = 0; a < n; a++) {
      var k = key(Math.round(N[a].x / GRID), Math.round(N[a].y / GRID), N[a].lvl);
      if (!buckets.has(k)) buckets.set(k, []);
      buckets.get(k).push(N[a]);
    }
    function near(node, lvl) {
      var cx = Math.round(node.x / GRID), cy = Math.round(node.y / GRID), out = [];
      for (var dx = -1; dx <= 1; dx++) for (var dy = -1; dy <= 1; dy++) {
        var b = buckets.get(key(cx + dx, cy + dy, lvl));
        if (!b) continue;
        for (var q = 0; q < b.length; q++) {
          if (b[q] === node) continue;
          if (Math.hypot(b[q].x - node.x, b[q].y - node.y) <= HRAD) out.push(b[q]);
        }
      }
      return out;
    }

    // Lowest occupied level of each connected cluster == "on the ground".
    // A base can have disconnected clusters at different terrain heights, so
    // this is per horizontal neighbourhood, approximated by the base minimum
    // per grid column (cheap, and wrong only for overhangs).
    var colMin = new Map();
    for (a = 0; a < n; a++) {
      var ck = Math.round(N[a].x / GRID) + "|" + Math.round(N[a].y / GRID);
      var cur = colMin.get(ck);
      if (cur === undefined || N[a].lvl < cur) colMin.set(ck, N[a].lvl);
    }

    // Support edges.
    var nSupports = new Array(n).fill(0);
    var supported = [];    // node index -> [dependent indices]
    for (a = 0; a < n; a++) supported.push([]);
    for (a = 0; a < n; a++) {
      var node = N[a], sup = [];
      // The Palbox and natural terrain nodes need nothing under them. The
      // Palbox is how a base camp comes into existence at all, so it is always
      // the first piece; rock/ore nodes were part of the landscape before the
      // camp existed. Both are facts about Palworld, not guesses about order.
      if (node.role === "palbox" || node.role === "natural") {
        node.sup = sup; nSupports[a] = 0;
        continue;
      }
      // (1) a floor on the same level carries what stands on it
      if (standsOnFloor(node.role)) {
        var same = near(node, node.lvl);
        for (var s = 0; s < same.length; s++) if (isFloor(same[s].role)) sup.push(same[s]);
      }
      // (2) the storey below carries this storey. Skipped for the lowest level
      //     in this grid column: that piece is standing on the ground.
      var colKey = Math.round(node.x / GRID) + "|" + Math.round(node.y / GRID);
      var onGround = node.lvl <= (colMin.get(colKey) === undefined ? node.lvl : colMin.get(colKey));
      if (!onGround) {
        var below = near(node, node.lvl - 1);
        for (s = 0; s < below.length; s++) if (bears(below[s].role)) sup.push(below[s]);
      }
      node.sup = sup;
      nSupports[a] = sup.length;
      for (s = 0; s < sup.length; s++) supported[sup[s].i].push(a);
    }

    // ---- CEILING EDGES: furniture goes in before the roof above it closes ---
    // Physics says a chair cannot exist before the floor it stands on. Viewing
    // says it must also not appear AFTER the ceiling above it is finished — you
    // would be watching furniture materialise inside a sealed room. The support
    // graph cannot express that: it is an OR ("any one support is enough"),
    // and this is an AND — a ceiling piece must wait for EVERY furnishing under
    // it. So it is a second, separate edge set with its own counter.
    //
    // This rule used to hold only as a side effect of LVL_COST being so large
    // that the builder finished an entire storey before climbing — which is
    // precisely the storey-by-storey sweep that reads as BFS. Making the
    // constraint explicit is what lets LVL_COST come down far enough for the
    // walk to read as one person finishing a pocket before moving on.
    //
    // No cycle is possible: a furnishing's ceiling is strictly above the floor
    // it stands on, and furnishings never `bear` the level above, so a ceiling
    // piece is never (transitively) supported by something it waits for.
    var lvlMin = Infinity, lvlMax = -Infinity;
    for (a = 0; a < n; a++) {
      if (N[a].lvl < lvlMin) lvlMin = N[a].lvl;
      if (N[a].lvl > lvlMax) lvlMax = N[a].lvl;
    }
    function floorsNear(node) {            // floor pieces within HRAD, any level
      var out = [];
      for (var L = lvlMin; L <= lvlMax; L++) {
        var c = near(node, L);
        for (var q = 0; q < c.length; q++) if (isFloor(c[q].role)) out.push(c[q]);
      }
      return out;
    }
    var blockedBy = new Array(n).fill(0);      // ceiling piece -> furnishings owed
    var blocks = []; for (a = 0; a < n; a++) blocks.push([]);
    var ceilEdges = 0;
    for (a = 0; a < n; a++) {
      if (!NONSTRUCT[N[a].role]) continue;
      var nf = floorsNear(N[a]);
      if (!nf.length) continue;
      // the floor it stands on = highest floor at or below it...
      var fl = -Infinity;
      for (s = 0; s < nf.length; s++)
        if (nf[s].lvl <= N[a].lvl && nf[s].lvl > fl) fl = nf[s].lvl;
      if (fl === -Infinity) fl = N[a].lvl - 1;
      // ...and its ceiling = the floor pieces on the lowest level above that.
      var cl = Infinity;
      for (s = 0; s < nf.length; s++)
        if (nf[s].lvl > fl && nf[s].lvl < cl) cl = nf[s].lvl;
      if (cl === Infinity) continue;                    // open sky overhead
      for (s = 0; s < nf.length; s++) if (nf[s].lvl === cl) {
        blocks[a].push(nf[s].i); blockedBy[nf[s].i]++; ceilEdges++;
      }
    }

    // "Any one support is enough": a node is eligible when it has no supports
    // at all (ground / free-standing), or at least one support already placed.
    var placed = new Array(n).fill(false);
    var eligible = new Array(n).fill(false);
    for (a = 0; a < n; a++) if (nSupports[a] === 0) eligible[a] = true;

    // Deterministic fallback == the elevation sort this replaced.
    function fallbackCmp(p, q) {
      var A = N[p], B = N[q];
      return (A.lvl - B.lvl) || (A.rad - B.rad) || (A.id < B.id ? -1 : A.id > B.id ? 1 : 0);
    }

    var out = [], fallbacks = 0, ceilRelax = 0, here = null;   // `here` = the piece just placed
    for (var step = 0; step < n; step++) {
      // Is there still un-placed PRE work (palbox, rocks)? Those precede
      // everything, so while any remains the candidate set is just those.
      var preLeft = false;
      for (a = 0; a < n; a++) if (!placed[a] && eligible[a] && N[a].pre === 0) { preLeft = true; break; }

      // The LOCAL storey floor: the lowest level still eligible within reach of
      // where the builder is standing. Going above it costs LVL_COST a level,
      // which is what stops a single tower leg racing to the sky while the
      // ground floor around it is bare. With nothing eligible nearby the floor
      // falls back to the global minimum, so an isolated pocket is not
      // penalised for being high up when there is no lower work left anywhere.
      var floorLvl = Infinity, globalLvl = Infinity;
      for (a = 0; a < n; a++) {
        if (placed[a] || !eligible[a]) continue;
        if (preLeft && N[a].pre !== 0) continue;
        if (N[a].lvl < globalLvl) globalLvl = N[a].lvl;
        if (here && Math.hypot(N[a].x - here.x, N[a].y - here.y) <= WORK_R && N[a].lvl < floorLvl)
          floorLvl = N[a].lvl;
      }
      if (floorLvl === Infinity) floorLvl = globalLvl;

      // Two passes: the first honours the ceiling rule, the second drops it.
      // The relaxed pass exists so a piece of geometry that boxes itself in can
      // never deadlock the walk; it is counted, so "0" in the stats means the
      // rule genuinely held rather than having been quietly abandoned.
      var pick = -1, best = Infinity, relax = 0;
      for (relax = 0; relax < 2 && pick < 0; relax++)
      for (a = 0; a < n; a++) {
        if (placed[a] || !eligible[a]) continue;
        if (preLeft && N[a].pre !== 0) continue;
        if (!relax && blockedBy[a] > 0) continue;
        var v = N[a];
        // Walking cost from where the builder is standing. On the very first
        // piece there is nowhere to walk from, so fall back to "start at the
        // heart of the camp" — distance from the palbox / centroid.
        var c = here
          ? Math.hypot(v.x - here.x, v.y - here.y, v.z - here.z)
          : v.rad;
        c += LVL_COST * Math.max(0, v.lvl - floorLvl);
        c += TIER_COST * v.tier;
        // Ties are broken by id so the same input always gives the same order:
        // a consumer indexing into the result (a player avatar walking the
        // path, say) can rely on step k meaning the same piece every run.
        // Symmetric pieces around a square building tie constantly, so this
        // branch is load-bearing for reproducibility, not a formality.
        var better = (pick < 0) || (c < best - 1e-9) ||
                     (Math.abs(c - best) <= 1e-9 && v.id < N[pick].id);
        if (better) { best = c; pick = a; }
      }
      if (pick < 0) {
        // Nothing is supported by anything already standing. Ambiguous
        // geometry: fall back to the elevation sort for one piece and carry on.
        fallbacks++;
        for (a = 0; a < n; a++) if (!placed[a] && (pick < 0 || fallbackCmp(a, pick) < 0)) pick = a;
      }
      else if (relax === 2) ceilRelax++;      // ceiling rule had to be dropped
      placed[pick] = true; out.push(N[pick].id); here = N[pick];
      var dep = supported[pick];
      for (var d = 0; d < dep.length; d++) eligible[dep[d]] = true;
      // this furnishing is in; the ceiling above it is one step closer to free
      var bl = blocks[pick];
      for (d = 0; d < bl.length; d++) blockedBy[bl[d]]--;
    }
    if (opts.stats) {
      var byRole = {};
      for (a = 0; a < n; a++) byRole[N[a].role] = (byRole[N[a].role] || 0) + 1;
      opts.stats.roles = byRole;
      opts.stats.fallbacks = fallbacks;
      opts.stats.ceilingEdges = ceilEdges;   // furniture->ceiling constraints found
      opts.stats.ceilingRelax = ceilRelax;   // ...times one had to be dropped
      opts.stats.levels = Math.max.apply(null, N.map(function (v) { return v.lvl; })) + 1;
      opts.stats.withSupport = nSupports.filter(function (v) { return v > 0; }).length;
      // How far the "builder" walks between consecutive pieces. This is the
      // number that says whether the order reads as a person or as a machine
      // sweeping the base; see locality.mjs for the before/after comparison.
      var byId = {}; for (a = 0; a < n; a++) byId[N[a].id] = N[a];
      var hops = [];
      for (a = 1; a < out.length; a++) {
        var p = byId[out[a - 1]], q = byId[out[a]];
        hops.push(Math.hypot(q.x - p.x, q.y - p.y, q.z - p.z));
      }
      if (hops.length) {
        var srt = hops.slice().sort(function (u, v) { return u - v; });
        opts.stats.hopMeanM = +(hops.reduce(function (u, v) { return u + v; }, 0) / hops.length / 100).toFixed(1);
        opts.stats.hopMedianM = +(srt[srt.length >> 1] / 100).toFixed(1);
      }
    }
    return out;
  }

  window.__reconstructBuildOrder = reconstructBuildOrder;
  window.__buildRoleOf = roleOf;
})();
