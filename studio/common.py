"""Shared helpers: paths, .env loading, cast, and the episode script format.

Script format (episodes/<ep>/script.txt):

    TITLE: Petunia and the Puddle
    # comment lines start with '#'
    SCENE puddle: PETUNIA left, FRIEND right
    NARRATOR: Once upon a time...
    PETUNIA [happy]: I love puddles!
    @FRIEND [worried]            <- stage direction: change a face without speaking
    @ENDO-MONSTER sneak left     <- motion: enter/sneak/peek (from left|right), exit (to left|right), hop, shake
    FRIEND [worried]: Are you okay?

Positions: farleft, left, center, right, farright (or a number 0..1).
Expressions: neutral, happy, sad, worried, surprised, hurting.
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIBLE = ROOT / "bible"
CHARS = BIBLE / "characters"
BGS = BIBLE / "backgrounds"
EPISODES = ROOT / "episodes"
CAST_FILE = BIBLE / "cast.json"

EXPRESSIONS = ["neutral", "happy", "sad", "worried", "surprised", "hurting"]
MOUTHS = ["closed", "small", "open", "round"]
POSITIONS = {"farleft": 0.14, "left": 0.30, "center": 0.50, "right": 0.70, "farright": 0.86}


def load_env():
    """Load KEY=VALUE pairs from .env into os.environ (without overriding)."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def need_key(name):
    load_env()
    val = os.environ.get(name, "").strip()
    if not val:
        sys.exit(f"Missing {name}. Put it in .env as {name}=... (see .env.example)")
    return val


def load_cast():
    return json.loads(CAST_FILE.read_text())


def save_cast(cast):
    CAST_FILE.write_text(json.dumps(cast, indent=2) + "\n")


def slug(name):
    """'ENDO-MONSTER' -> 'ENDO-MONSTER' folder name (kept as-is, safe chars only)."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", name.upper())


def ep_dir(ep):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", ep):
        sys.exit(f"Bad episode id: {ep!r}")
    return EPISODES / ep


class ScriptError(ValueError):
    pass


LINE_RE = re.compile(r"^([A-Z][A-Z0-9 _-]*?)\s*(?:\[([a-z]+)\])?\s*:\s*(.+)$")
DIRECTION_RE = re.compile(r"^@([A-Z][A-Z0-9 _-]*?)\s*\[([a-z]+)\]\s*$")
MOVE_RE = re.compile(r"^@([A-Z][A-Z0-9 _-]*?)\s+(enter|exit|sneak|peek|hop|shake)s?(?:\s+(?:from\s+|to\s+)?(left|right))?\s*$", re.I)
MOVES = ["enter", "exit", "sneak", "peek", "hop", "shake"]
SCENE_RE = re.compile(r"^SCENE\s+([a-z0-9_-]+)\s*(?::\s*(.*))?$", re.I)


def parse_script(text, cast=None):
    """Return {"title": str, "scenes": [{"bg", "stage": {CHAR: x}, "events": [...]}]}.

    Each event is {"type": "line", "id": n, "char", "expr", "text"} or
    {"type": "face", "char", "expr"}.
    """
    cast = cast or load_cast()
    known = set(cast["characters"])
    title, scenes, n = "Untitled", [], 0
    errors = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.upper().startswith("TITLE:"):
            title = line.split(":", 1)[1].strip()
            continue
        m = SCENE_RE.match(line)
        if m:
            stage = {}
            for part in filter(None, (p.strip() for p in (m.group(2) or "").split(","))):
                bits = part.rsplit(None, 1)
                name = bits[0].upper()
                pos = bits[1].lower() if len(bits) > 1 else "center"
                if name not in known:
                    errors.append(f"line {lineno}: unknown character {name}")
                    continue
                try:
                    x = POSITIONS[pos] if pos in POSITIONS else float(pos)
                except ValueError:
                    errors.append(f"line {lineno}: bad position {pos!r}")
                    continue
                stage[name] = x
            scenes.append({"bg": m.group(1).lower(), "stage": stage, "events": []})
            continue
        m = MOVE_RE.match(line)
        if m:
            name = m.group(1).strip().upper()
            if not scenes:
                errors.append(f"line {lineno}: motion before any SCENE")
            elif name not in scenes[-1]["stage"]:
                errors.append(f"line {lineno}: {name} must be on this SCENE line to move")
            else:
                scenes[-1]["events"].append({"type": "move", "char": name, "action": m.group(2).lower(),
                                             "side": (m.group(3) or "").lower() or None})
            continue
        m = DIRECTION_RE.match(line)
        if m:
            name, expr = m.group(1).strip().upper(), m.group(2)
            if not scenes:
                errors.append(f"line {lineno}: direction before any SCENE")
            elif name not in known or expr not in EXPRESSIONS:
                errors.append(f"line {lineno}: bad direction {line!r}")
            else:
                scenes[-1]["events"].append({"type": "face", "char": name, "expr": expr})
            continue
        m = LINE_RE.match(line)
        if m:
            name, expr, said = m.group(1).strip().upper(), m.group(2) or None, m.group(3).strip()
            if not scenes:
                errors.append(f"line {lineno}: dialogue before any SCENE")
                continue
            if name not in known:
                errors.append(f"line {lineno}: unknown character {name}")
                continue
            if expr and expr not in EXPRESSIONS:
                errors.append(f"line {lineno}: unknown expression [{expr}] (use {', '.join(EXPRESSIONS)})")
                continue
            n += 1
            scenes[-1]["events"].append(
                {"type": "line", "id": n, "char": name, "expr": expr, "text": said})
            continue
        errors.append(f"line {lineno}: can't read {line!r}")
    if not any(e["type"] == "line" for s in scenes for e in s["events"]):
        errors.append("script has no dialogue lines")
    if errors:
        raise ScriptError("\n".join(errors))
    return {"title": title, "scenes": scenes}


def load_episode(ep):
    path = ep_dir(ep) / "script.txt"
    if not path.exists():
        sys.exit(f"No script at {path}")
    try:
        return parse_script(path.read_text())
    except ScriptError as e:
        sys.exit(f"Script problems in {path}:\n{e}")


def line_key(ln):
    """Recording file name for a line: tied to who says it and the exact words, not its position,
    so inserting or moving lines never re-voices (and re-charges) lines that didn't change."""
    import hashlib
    return hashlib.sha1(f"{ln['char']}|{ln['text']}".encode()).hexdigest()[:12]


def script_fingerprint(text):
    import hashlib
    norm = "\n".join(l.rstrip() for l in text.strip().splitlines())
    return hashlib.sha1(norm.encode()).hexdigest()


def approval_state(ep):
    """('approved'|'changed'|'none', approved_at)"""
    d = ep_dir(ep)
    a, sc = d / "approved.json", d / "script.txt"
    if not a.exists() or not sc.exists():
        return "none", None
    info = json.loads(a.read_text())
    return ("approved" if info.get("fingerprint") == script_fingerprint(sc.read_text()) else "changed"), info.get("approved_at")


def all_lines(script):
    return [e for s in script["scenes"] for e in s["events"] if e["type"] == "line"]
