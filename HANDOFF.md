# Handover: building the studio on the owner's gaming PC

Read this and `CLAUDE.md` first. The owner is NOT a developer: do every technical step yourself,
explain in plain words, ask before installing anything big and before spending any money or credits.

## What the owner wants
A complete studio app (a "production house") of her own, for any show (Endo Warriors / Petunia, "Roo",
future cartoons):
1. Upload a character picture/sheet -> a **3D character** (Pixar/Disney style, never 2D cut-outs).
2. Characters keep their exact look in every scene and episode (no drift).
3. Write an episode script together, approve it, then voice it ONCE with her ElevenLabs voices.
4. The app animates the characters acting out the script in 3D sets, with lip-sync, camera and lighting.
5. Output: finished 10-15 minute episodes with commercial-break markers, captions and artwork,
   ready for YouTube and for distributors that place shows on Amazon Prime Video, Tubi, Roku.

## Decisions already made (don't reopen them)
- 3D CGI like Pixar. **No 2D cut-out puppets** (the `studio/puppet.py` renderer is the rejected cut-out
  approach; keep its script parsing, voice and approval pieces, drop the cut-out rendering).
- **No video-generation services.** No ElevenLabs video, no paid image generators. She approved
  ElevenLabs **only for voices**, on her own plan: she upgraded to Creator ($22/month, ~121,000 credits; Episode 1 needs ~7,700).
- Voices are made once per approved script; only new or changed lines are re-voiced. Never spend
  credits without showing the estimate and getting her yes (`studio.py approve`, `estimate`, `voices --yes`).
- Character sculpting runs **locally on her GPU** with a free, open-source, already-trained
  image-to-3D model. Nothing per-character to pay. The app downloads the model itself; she never visits
  GitHub or Hugging Face.

## Her machine
- Windows gaming PC, **NVIDIA GeForce RTX 4060 (8 GB VRAM)**. The Claude desktop app is installed.
- 8 GB VRAM: use the small shape model (e.g. Hunyuan3D-2mini, or Hunyuan3D-2 with low-VRAM/offload),
  then **texture the mesh by projecting her own character sheet views** (front/side/back) in Blender
  rather than a large texture model. Check each model's licence (Hunyuan3D's licence excludes some
  regions) and tell her in one plain sentence before using it.
- Rendering: Blender (free) with Cycles/EEVEE on the RTX 4060.

## What exists in this repo (branch `ccr-a00be2d4-hyttud`)
- `bible/sheets/` her character sheets: PETUNIA (+ 12 story expressions), MOM, DAD, DR-SALEM, family lineup,
  ENDO-MONSTER (3D-style sheet). `bible/cast.json`: characters, heights, roles, ElevenLabs voice IDs
  (PETUNIA YnXziUJmY9NsJ5JNgOR6, MOM 1hJOA1O9s18kQsqo8ZL8, DR-SALEM SxLhzlOplCG52gMdYD7g,
  ENDO-MONSTER PMOAYggvZmjW1kAs7Fbw; read from a screenshot, so verify each against her account;
  DAD, NARRATOR, NURSE-MINDY still needed).
- `episodes/ep01/script.txt`: full ~15 min draft of Episode 1 (4 parts, 3 `BREAK`s), awaiting her notes.
- `studio/studio.py`: new / check / write / approve / estimate / voices (ElevenLabs, timestamps for lip-sync).
- `studio/server.py`: local web app (episodes, script editor, approve, voice with cost confirm, build).
- `studio3d/`: Blender helpers + a code-built Petunia. **She rejected that look ("Robot Chicken")**:
  only the lighting/render helpers are worth keeping; characters must come from the sculpting step.

## Next steps on the PC
1. Check the GPU (`nvidia-smi`), install Git, Python 3.11, and Blender; clone this repo/branch.
2. Install the local image-to-3D model; sculpt the **ENDO-MONSTER first** (closest to 3D already)
   from `bible/sheets/ENDO-MONSTER_3d_sheet.jpg`, then show her a turnaround render for approval.
3. Auto-rig (skeleton + face controls for mouth shapes and blinks), test a walk, a jump and a lip-synced line.
4. Build the 3D episode engine: sets, staging from the script, Disney's 12 animation principles,
   camera (wide / close-up on speaker), lighting, render, edit with voices + music, captions, break markers.
5. Wrap it all in the studio app with a one-click start on Windows.
