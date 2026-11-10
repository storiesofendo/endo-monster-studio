# Endo-Monster Studio

Cartoon episode maker for **"There's an Endo-Monster in My Tummy!"**

Characters must never drift, so the studio uses **puppet mode**: each character is a fixed
pack of images (body, 6 expressions, 4 mouth shapes) drawn **once** from the reference picture
in `bible/characters/<NAME>/reference.png`. Every episode reuses the same pack. Voices come from
ElevenLabs, scripts can be drafted by Claude, and the video is assembled locally with ffmpeg.

## Setup

```bash
pip install -r requirements.txt      # plus ffmpeg on your PATH
cp .env.example .env                 # then fill in the keys
```

| Key | Used for |
|---|---|
| `ELEVENLABS_API_KEY` | character voices + lip-sync timing |
| `ANTHROPIC_API_KEY` | "Write script" |
| `OPENAI_API_KEY` | drawing puppet pieces and backgrounds (gpt-image-1, works from a reference picture) |

## Folder map

```
bible/cast.json                    characters: role, on-screen height, voice
bible/style.md                     the book's look and story rules (used in every prompt)
bible/style_reference.png          optional: a page from the book, so backgrounds match its painting
bible/characters/<NAME>/reference.png   YOUR reference drawing (the source of truth)
bible/characters/<NAME>/prompts.md      prompts for each piece
bible/characters/<NAME>/raw/            drawn pieces (body, face_*, mouth_*)
bible/characters/<NAME>/pack/           cleaned, lined-up pieces + pack.json (what episodes use)
bible/backgrounds/<scene>.png           puddle.png, kitchen.png, doctor.png ...
episodes/<ep>/script.txt                the episode
```

## Making an episode

```bash
python3 studio/studio.py voices ep01          # ElevenLabs audio for every line
python3 studio/studio.py samples ep01         # one line per character, to approve voices
python3 studio/puppet.py build ep01           # -> episodes/ep01/ep01.mp4 (+ contact.png)
python3 studio/server.py                      # the web app: http://localhost:8765
```

## Making the puppets (once per character)

```bash
python3 studio/puppet.py prompts all          # writes prompts.md per character
python3 studio/puppet.py generate PETUNIA     # draws body from reference.png, then edits body for faces/mouths
python3 studio/puppet.py pack PETUNIA         # removes background, lines pieces up, writes pack.json + sheet.png
python3 studio/puppet.py lineup               # bible/lineup.png: everyone side by side, all faces
python3 studio/puppet.py background puddle    # also kitchen, doctor, or --prompt "..."
```

Redraw one bad piece with `generate PETUNIA --only face_sad --force`, then `pack PETUNIA` again.
If a face or mouth patch lands in the wrong place, adjust `head_box` in `pack/pack.json`
(or `head_fraction` in `cast.json` for tall/odd shapes like the Endo-Monster) and re-run `pack`.

**Testing without art or keys:** `puppet.py placeholder all` makes simple stand-in puppets and
`studio.py voices ep01 --offline` makes silent stand-in audio, so the whole pipeline can run.

## Script format

```
TITLE: There's an Endo-Monster in My Tummy!
SCENE puddle: PETUNIA left, FRIEND right
NARRATOR: This is Petunia.
PETUNIA [happy]: Splash!
@FRIEND [worried]                 <- change a face without speaking
```

Positions: `farleft left center right farright` (or a number 0-1).
Expressions: `neutral happy sad worried surprised hurting`. Mouths are automatic from the voice timing.

## Voices

`voices` picks a voice from your ElevenLabs account for any character without one (by gender and
the `hint` in `cast.json`; the narrator is male) and saves it in `cast.json`, so it never changes
after that. To swap: `studio.py cast` lists voices, `studio.py setvoice NARRATOR <voice_id>`, then
`studio.py voices ep01 --only NARRATOR --force`.
