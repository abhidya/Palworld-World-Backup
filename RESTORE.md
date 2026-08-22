# Restore & Portable Editing

Every command below is run from the **rig** (`D:\rig`) unless stated otherwise.

```powershell
cd D:\rig
.\rig.ps1 palworld backup-status
```

---

## 1. What actually constitutes the world

A complete restore needs **all** of `world/current/`:

| File | Why it is required |
|---|---|
| `Level.sav` | The world: terrain deltas, base camps, every placed object, all pals in bases, guild ownership. |
| `LevelMeta.sav` | World metadata the server reads before loading `Level.sav`. |
| `WorldOption.sav` | The world's baked-in option set. Present since world creation. |
| `Players/<playerid>.sav` | One per character: inventory, tech, stats, pal party. **Losing these loses the players even if `Level.sav` survives.** |
| `Players/00000000000000000000000000000001.sav` | The host/local player slot for this dedicated server. |
| `backup/world/<timestamp>/` | Palworld's own rotating engine backups, captured here by choice. Not required for a restore. |

Guild membership and base ownership live **inside `Level.sav`**, not in a separate
file — which is why a partial restore (e.g. only player saves) corrupts guilds.

`server-config/PalWorldSettings.ini` is required to reproduce the *environment*
(rates, guild limits, ports) but is **sanitised** — see Secrets below.

---

## 2. Restore to the live server

The restore refuses to run against a live server unless you explicitly ask for
maintenance mode. It always snapshots the current world first.

```powershell
# Restore the most recent snapshot, stopping and restarting the server around it
.\rig.ps1 palworld restore HEAD --maintenance

# Restore a specific snapshot by tag
.\rig.ps1 palworld restore snapshot/20260816-070500 --maintenance

# Restore without bringing the server back up
.\rig.ps1 palworld restore <sha> --maintenance --no-restart
```

What it does, in order:

1. Refuses outright if players are connected.
2. Takes a **safety snapshot** of the world that is live *right now*, and pushes it.
   If that fails, the restore aborts — you can never lose the current world to a restore.
3. Materialises the target commit into `restore-staging/` via a scratch git
   worktree. `git checkout` is **never** run inside the PalServer save directory.
4. Verifies the staged tree: critical files present, player saves present, every
   file hashed against the snapshot manifest, and no unresolved LFS pointers.
5. Refuses to cross-restore a different world id than the one installed.
6. Stops PalServer (flushing first).
7. Moves the live world aside to `<world>.replaced-<timestamp>` — never deletes it.
8. Copies the verified tree into place and **re-verifies it in the live location**.
   Any failure rolls the original back automatically.
9. Restarts PalServer only after all of the above succeeded.
10. Reports the exact commit restored.

### List available snapshots

```powershell
git -C D:\Palworld-World-Backup log --oneline --decorate
git -C D:\Palworld-World-Backup tag --list "snapshot/*"
```

---

## 3. Portable editing (save editors / MCP) — never point tools at the live save

**Rule: no editor, script or MCP server is ever pointed at
`C:\Program Files (x86)\Steam\...\SaveGames\0\<world>`.**

The sanctioned loop:

```powershell
# 1. Materialise a snapshot into a DISPOSABLE working directory
.\rig.ps1 palworld edit-workspace HEAD
#    -> D:\Palworld-World-Backup\work\edit-20260816-071500

# 2. Point your save editor / MCP server at THAT directory. Edit freely.

# 3. See exactly what the tool changed
.\rig.ps1 palworld edit-diff D:\Palworld-World-Backup\work\edit-20260816-071500

# 4. Commit the result as a candidate branch (pushed, never live)
.\rig.ps1 palworld edit-promote D:\Palworld-World-Backup\work\edit-20260816-071500 --message "rebalanced pal stats"

# 5. ONLY when you are satisfied, promote the candidate to the live server
.\rig.ps1 palworld restore <candidate-sha> --maintenance
```

`work/` is gitignored, so a botched edit can simply be deleted. Step 4 refuses to
promote a tree missing critical files, and re-runs the secret scanner.

### On another computer

```bash
git lfs install                      # REQUIRED before cloning
git clone https://github.com/abhidya/Palworld-World-Backup.git
cd Palworld-World-Backup
python scripts/verify_snapshot.py .  # prove the clone is real save data
```

Then edit `world/current/` in a copy, commit to a `candidate/*` branch, push, and
run the `restore` on the rig. The rig is the only machine that writes to the live
server.

---

## 4. Disaster recovery from nothing but GitHub

```bash
git lfs install
git clone https://github.com/abhidya/Palworld-World-Backup.git
```

1. Install a Palworld dedicated server (Steam app **2394010**).
2. Start it once and stop it, so it creates `Pal\Saved\SaveGames\0\<newid>`.
3. Delete that generated world dir and copy `world/current/` into
   `Pal\Saved\SaveGames\0\64EE4B2C4C81F4912BF109850820D9BA`.
4. Copy `server-config/PalWorldSettings.ini` to
   `Pal\Saved\Config\WindowsServer\PalWorldSettings.ini` and re-enter the
   redacted secrets.
5. Ensure `GameUserSettings.ini` has
   `DedicatedServerName=64EE4B2C4C81F4912BF109850820D9BA` so the server loads
   this world rather than creating a new one.
6. Start the server and confirm `/v1/api/info` reports
   `worldguid = 64EE4B2C4C81F4912BF109850820D9BA`.

---

## 5. Secrets

These keys are stripped from the committed config and replaced with
`<REDACTED:supplied-locally>`:

`AdminPassword`, `ServerPassword`, `RCONPassword`, `PublicIP`, `BanListURL`

They are supplied only by the live host's own
`PalWorldSettings.ini`. The backup engine reads `AdminPassword` from that file at
runtime to authenticate to the REST API — it is never stored in this repo, in rig
state, or in any log. A snapshot **aborts** rather than commit if any known
secret value is found in the staged tree, `.sav` files included.

### WorldOption.sav carries credentials too — and it wins

`WorldOption.sav` keeps its own copy of `AdminPassword` / `ServerPassword`, and on
this dedicated server **it overrides `PalWorldSettings.ini`**. Verified: after
rotating both values in `.env` and recreating the container, the regenerated
`.ini` held the new pair while the server still authenticated the *old* one,
because the save had not been touched.

Two consequences for restores:

1. The committed `world/current/WorldOption.sav` is **sanitized** — its three
   credential fields hold `<REDACTED:supplied-locally>`. Restoring it verbatim
   would set the live server's password to that literal string.
2. So after any restore, **delete `WorldOption.sav` from the restored world
   directory before starting the server**. The server recreates it from
   `PalWorldSettings.ini`, which is where your real credentials live:

```bash
rm "$SAVE_ROOT/$WORLD_ID/WorldOption.sav"   # then start the container
```

Changing a password means changing it in `WorldOption.sav` as well (or deleting
the file and letting the server regenerate it). Editing `.env` or the `.ini`
alone has **no effect** while that save exists.

---

## 6. Verifying a backup you have not touched in months

```powershell
.\rig.ps1 palworld backup-verify          # clones the REMOTE cold, verifies hashes + LFS
.\rig.ps1 palworld backup-verify --local  # verifies the local working tree only
```

`backup-verify` with no flags is the meaningful one: it clones from GitHub into a
throwaway directory, pulls LFS objects fresh, and hashes every file against the
manifest. That is the only check that proves *the remote* is restorable.
