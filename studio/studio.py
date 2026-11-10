#!/usr/bin/env python3
"""Episode tool: new episodes, AI-written scripts, and ElevenLabs voices.

    python3 studio/studio.py new ep02 "Petunia Goes to School"
    python3 studio/studio.py write ep02 --premise "Petunia explains endo to her class"
    python3 studio/studio.py check ep01            # validate a script
    python3 studio/studio.py voices ep01           # make every line's audio + lip-sync timing
    python3 studio/studio.py voices ep01 --offline # silent stand-in audio (no key needed)
    python3 studio/studio.py samples ep01          # one line per character, for approval
    python3 studio/studio.py cast                  # list ElevenLabs voices and who uses which
    python3 studio/studio.py setvoice NARRATOR <voice_id>
"""
import argparse
import base64
import json
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from common import (EXPRESSIONS, BIBLE, ScriptError, all_lines, ep_dir, load_cast,  # noqa: E402
                    load_episode, need_key, parse_script, save_cast)

ELEVEN = "https://api.elevenlabs.io/v1"
TTS_MODEL = "eleven_multilingual_v2"
CLAUDE_MODEL = "claude-opus-5-5"


# ---------------------------------------------------------------- new / check

def cmd_new(args):
    d = ep_dir(args.ep)
    script = d / "script.txt"
    if script.exists():
        sys.exit(f"{script} already exists")
    d.mkdir(parents=True, exist_ok=True)
    script.write_text(
        f"TITLE: {args.title or args.ep}\n"
        "# Write the episode here, or run: studio.py write " + args.ep + " --premise \"...\"\n\n"
        "SCENE puddle: PETUNIA left, FRIEND right\n"
        "NARRATOR: Once upon a time...\n")
    print(f"Created {script}")


def cmd_check(args):
    script = load_episode(args.ep)
    lines = all_lines(script)
    print(f"OK: \"{script['title']}\" - {len(script['scenes'])} scenes, {len(lines)} lines")
    for s in script["scenes"]:
        print(f"  [{s['bg']}] " + ", ".join(f"{c}@{x:.2f}" for c, x in s["stage"].items()))


# ---------------------------------------------------------------- write (Claude)

def cmd_write(args):
    import anthropic
    need_key("ANTHROPIC_API_KEY")
    cast = load_cast()
    style = (BIBLE / "style.md").read_text()
    example = (ep_dir("ep01") / "script.txt").read_text() if (ep_dir("ep01") / "script.txt").exists() else ""
    from common import BGS
    bgs = sorted(p.stem for p in BGS.glob("*.png")) or ["puddle", "kitchen", "doctor"]
    who = "\n".join(f"- {n}: {c['role']}" + ("" if c.get("on_screen", True) else " (voice only, never on stage)")
                    for n, c in cast["characters"].items())
    system = (
        "You write scripts for a gentle animated children's series based on the picture book "
        "\"There's an Endo-Monster in My Tummy!\". You output ONLY the script in the exact plain-text format "
        "shown, with no commentary and no markdown fences.\n\n"
        f"STYLE GUIDE:\n{style}\n\nCHARACTERS (use only these names, in capitals):\n{who}\n\n"
        f"BACKGROUNDS available for SCENE lines: {', '.join(bgs)}\n"
        f"EXPRESSIONS available in [brackets]: {', '.join(EXPRESSIONS)}\n"
        "Positions on SCENE lines: farleft, left, center, right, farright. Put every on-screen character who "
        "speaks in a scene on that scene's SCENE line. NARRATOR never goes on a SCENE line.\n"
        "Format rules: first line 'TITLE: ...'; 'SCENE <background>: NAME position, NAME position'; dialogue "
        "'NAME [expression]: words'; '@NAME [expression]' changes a face without speaking. Aim for 16-30 lines.\n\n"
        f"EXAMPLE EPISODE:\n{example}")
    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": f"Episode id {args.ep}. Premise: {args.premise}"}]
    for attempt in range(2):
        with client.beta.messages.stream(
            model=CLAUDE_MODEL, max_tokens=32000, system=system, messages=messages,
            thinking={"type": "adaptive"}, output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        ) as stream:
            msg = stream.get_final_message()
        if msg.stop_reason == "refusal":
            sys.exit("Claude declined to write this premise; try rephrasing it.")
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        text = text.removeprefix("```").removesuffix("```").strip()
        try:
            parse_script(text, cast)
            break
        except ScriptError as e:
            if attempt == 1:
                sys.exit(f"Script still had problems:\n{e}\n\n{text}")
            messages += [{"role": "assistant", "content": msg.content},
                         {"role": "user", "content": f"That script has format problems:\n{e}\nRewrite it fully, fixed."}]
    d = ep_dir(args.ep)
    d.mkdir(parents=True, exist_ok=True)
    (d / "script.txt").write_text(text + "\n")
    print(f"Wrote {d / 'script.txt'}")
    print(text)


# ---------------------------------------------------------------- voices (ElevenLabs)

def eleven_get(path):
    import requests
    r = requests.get(ELEVEN + path, headers={"xi-api-key": need_key("ELEVENLABS_API_KEY")}, timeout=60)
    if r.status_code >= 400:
        sys.exit(f"ElevenLabs {path} failed ({r.status_code}): {r.text[:300]}")
    return r.json()


def pick_voice(name, spec, voices, taken):
    """Choose a voice from the account by gender label and hint words."""
    hint = set(spec.get("hint", "").lower().replace(",", " ").split())
    want_gender = spec.get("gender", "").lower()
    best, best_score = None, -1e9
    for v in voices:
        labels = {k: str(val).lower() for k, val in (v.get("labels") or {}).items()}
        text = " ".join([v.get("name", ""), v.get("description") or "", *labels.values()]).lower()
        if want_gender and labels.get("gender") and labels["gender"] != want_gender:
            continue
        score = sum(2 for w in hint if len(w) > 3 and w in text)
        score += 3 if want_gender and labels.get("gender") == want_gender else 0
        score -= 10 if v["voice_id"] in taken else 0
        if score > best_score:
            best, best_score = v, score
    if not best:
        sys.exit(f"No ElevenLabs voice fits {name} ({spec}). Set one with: studio.py setvoice {name} <voice_id>")
    return best


def ensure_voices(cast, names):
    missing = [n for n in names if not cast["characters"][n]["voice"].get("voice_id")]
    if not missing:
        return
    voices = eleven_get("/voices")["voices"]
    taken = {c["voice"]["voice_id"] for c in cast["characters"].values() if c["voice"].get("voice_id")}
    for n in missing:
        v = pick_voice(n, cast["characters"][n]["voice"], voices, taken)
        cast["characters"][n]["voice"]["voice_id"] = v["voice_id"]
        cast["characters"][n]["voice"]["voice_name"] = v.get("name")
        taken.add(v["voice_id"])
        print(f"  {n}: picked voice '{v.get('name')}' ({v['voice_id']})")
    save_cast(cast)


def tts(text, voice_id):
    """Return (mp3 bytes, alignment dict) from ElevenLabs with character timestamps."""
    import requests
    key = need_key("ELEVENLABS_API_KEY")
    body = {"text": text, "model_id": TTS_MODEL,
            "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.35, "use_speaker_boost": True}}
    for attempt in range(4):
        r = requests.post(f"{ELEVEN}/text-to-speech/{voice_id}/with-timestamps",
                          params={"output_format": "mp3_44100_128"},
                          headers={"xi-api-key": key, "Content-Type": "application/json"},
                          json=body, timeout=120)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 ** attempt * 2)
            continue
        if r.status_code >= 400:
            sys.exit(f"ElevenLabs TTS failed ({r.status_code}): {r.text[:400]}")
        j = r.json()
        al = j.get("alignment") or j.get("normalized_alignment") or {}
        return base64.b64decode(j["audio_base64"]), {
            "chars": al.get("characters", []),
            "starts": al.get("character_start_times_seconds", []),
            "ends": al.get("character_end_times_seconds", []),
        }
    sys.exit("ElevenLabs kept failing; try again in a minute.")


def audio_duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                          "default=nw=1:nk=1", str(path)], capture_output=True, text=True)
    return float(out.stdout.strip() or 0)


def offline_line(text, path):
    """Silent stand-in audio with evenly spread character timing, for testing without a key."""
    dur = round(0.6 + 0.075 * len(text), 2)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                    "-t", str(dur), "-q:a", "9", str(path)], check=True)
    step = (dur - 0.4) / max(len(text), 1)
    starts = [0.2 + i * step for i in range(len(text))]
    return {"chars": list(text), "starts": starts, "ends": [s + step for s in starts]}


def cmd_voices(args):
    script = load_episode(args.ep)
    cast = load_cast()
    lines = all_lines(script)
    if args.only:
        lines = [ln for ln in lines if ln["char"] in {o.upper() for o in args.only}]
    if not args.offline:
        ensure_voices(cast, sorted({ln["char"] for ln in lines}))
    out = ep_dir(args.ep) / "audio"
    out.mkdir(exist_ok=True)
    for ln in lines:
        mp3 = out / f"{ln['id']:03d}.mp3"
        meta = out / f"{ln['id']:03d}.json"
        if mp3.exists() and meta.exists() and not args.force:
            old = json.loads(meta.read_text())
            if old.get("text") == ln["text"] and old.get("char") == ln["char"] and old.get("offline") == args.offline:
                continue
        if args.offline:
            align = offline_line(ln["text"], mp3)
            voice_id = None
        else:
            voice_id = cast["characters"][ln["char"]]["voice"]["voice_id"]
            data, align = tts(ln["text"], voice_id)
            mp3.write_bytes(data)
        meta.write_text(json.dumps({"char": ln["char"], "text": ln["text"], "voice_id": voice_id,
                                    "offline": args.offline, "duration": audio_duration(mp3),
                                    "alignment": align}, indent=1))
        print(f"  {mp3.name}  {ln['char']}: {ln['text'][:60]}")
    print(f"Voices done: {len(lines)} lines in {out}")


def cmd_samples(args):
    script = load_episode(args.ep)
    src = ep_dir(args.ep) / "audio"
    dst = ep_dir(args.ep) / "samples"
    dst.mkdir(exist_ok=True)
    seen = {}
    for ln in all_lines(script):
        if ln["char"] not in seen and (src / f"{ln['id']:03d}.mp3").exists():
            seen[ln["char"]] = ln
            shutil.copy(src / f"{ln['id']:03d}.mp3", dst / f"{ln['char']}.mp3")
            print(f"  {dst / (ln['char'] + '.mp3')}  \"{ln['text']}\"")
    if not seen:
        sys.exit("No audio yet; run voices first.")


def cmd_cast(args):
    cast = load_cast()
    voices = eleven_get("/voices")["voices"]
    used = {c["voice"].get("voice_id"): n for n, c in cast["characters"].items()}
    for v in voices:
        lab = v.get("labels") or {}
        tag = f"  <- {used[v['voice_id']]}" if v["voice_id"] in used else ""
        print(f"{v['voice_id']}  {v.get('name', ''):<22} {lab.get('gender', ''):<7} {lab.get('age', ''):<12} "
              f"{lab.get('accent', ''):<12} {lab.get('description') or lab.get('descriptive', '')}{tag}")


def cmd_setvoice(args):
    cast = load_cast()
    name = args.char.upper()
    if name not in cast["characters"]:
        sys.exit(f"Unknown character {name}")
    cast["characters"][name]["voice"]["voice_id"] = args.voice_id
    cast["characters"][name]["voice"].pop("voice_name", None)
    save_cast(cast)
    print(f"{name} now uses {args.voice_id}. Re-run voices with --only {name} --force to redo their lines.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("new"); s.add_argument("ep"); s.add_argument("title", nargs="?"); s.set_defaults(f=cmd_new)
    s = sub.add_parser("check"); s.add_argument("ep"); s.set_defaults(f=cmd_check)
    s = sub.add_parser("write"); s.add_argument("ep"); s.add_argument("--premise", required=True); s.set_defaults(f=cmd_write)
    s = sub.add_parser("voices"); s.add_argument("ep"); s.add_argument("--offline", action="store_true")
    s.add_argument("--force", action="store_true"); s.add_argument("--only", nargs="*"); s.set_defaults(f=cmd_voices)
    s = sub.add_parser("samples"); s.add_argument("ep"); s.set_defaults(f=cmd_samples)
    s = sub.add_parser("cast"); s.set_defaults(f=cmd_cast)
    s = sub.add_parser("setvoice"); s.add_argument("char"); s.add_argument("voice_id"); s.set_defaults(f=cmd_setvoice)
    args = p.parse_args()
    args.f(args)


if __name__ == "__main__":
    main()
