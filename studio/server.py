#!/usr/bin/env python3
"""Episode maker web app.  python3 studio/server.py  ->  http://localhost:8765

New episode, write script (Claude), edit, make voices (ElevenLabs), build video, watch.
Each long step runs the same command-line tools in the background and streams their log.
"""
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import BIBLE, EPISODES, ROOT, ScriptError, approval_state, line_key, parse_script  # noqa: E402

PORT = int(os.environ.get("PORT", "8765"))
STUDIO = Path(__file__).resolve().parent
JOBS = {}
EP_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def run_job(label, cmd):
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "label": label, "status": "running", "log": "", "started": time.time()}
    JOBS[jid] = job

    def work():
        p = subprocess.Popen([sys.executable, *cmd], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, bufsize=1)
        for line in p.stdout:
            job["log"] += line
        job["status"] = "done" if p.wait() == 0 else "failed"
    threading.Thread(target=work, daemon=True).start()
    return job


def episode_info(ep):
    d = EPISODES / ep
    script = d / "script.txt"
    state, when = approval_state(ep)
    info = {"id": ep, "title": ep, "lines": 0, "voiced": 0, "video": None, "problems": None,
            "approval": state, "approved_at": when}
    if script.exists():
        try:
            s = parse_script(script.read_text())
            info["title"] = s["title"]
            lines = [e for sc in s["scenes"] for e in sc["events"] if e["type"] == "line"]
            info["lines"] = len(lines)
            for ln in lines:
                m = d / "audio" / f"{line_key(ln)}.json"
                if m.exists() and not json.loads(m.read_text()).get("offline"):
                    info["voiced"] += 1
        except ScriptError as e:
            info["problems"] = str(e)
    mp4 = d / f"{ep}.mp4"
    if mp4.exists():
        info["video"] = f"/files/episodes/{ep}/{ep}.mp4?v={int(mp4.stat().st_mtime)}"
    return info


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def ep_or_404(self, ep):
        if not EP_RE.match(ep):
            self.send_json({"error": "bad episode id"}, 400)
            return None
        return ep

    # ---------------------------------------------------------------- GET
    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/episodes":
            eps = sorted(p.name for p in EPISODES.iterdir() if p.is_dir() and EP_RE.match(p.name)) if EPISODES.exists() else []
            lineup = BIBLE / "lineup.png"
            self.send_json({"episodes": [episode_info(e) for e in eps],
                            "lineup": f"/files/bible/lineup.png?v={int(lineup.stat().st_mtime)}" if lineup.exists() else None})
        elif m := re.fullmatch(r"/api/episodes/([^/]+)/script", path):
            if ep := self.ep_or_404(m.group(1)):
                p = EPISODES / ep / "script.txt"
                self.send_json({"script": p.read_text() if p.exists() else ""})
        elif m := re.fullmatch(r"/api/jobs/([a-f0-9]+)", path):
            job = JOBS.get(m.group(1))
            self.send_json(job or {"error": "no such job"}, 200 if job else 404)
        elif path.startswith("/files/"):
            self.serve_file(path[len("/files/"):])
        else:
            self.send_json({"error": "not found"}, 404)

    def serve_file(self, rel):
        target = (ROOT / rel).resolve()
        allowed = [EPISODES.resolve(), BIBLE.resolve()]
        if not any(target.is_relative_to(a) for a in allowed) or not target.is_file():
            return self.send_json({"error": "not found"}, 404)
        size = target.stat().st_size
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        if rng and (m := re.match(r"bytes=(\d*)-(\d*)", rng)):
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            end = min(end, size - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(target, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 16, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(chunk)

    # ---------------------------------------------------------------- POST / PUT
    def do_POST(self):
        path = urlparse(self.path).path
        data = self.body()
        if path == "/api/episodes":
            ep = (data.get("id") or "").strip()
            if not EP_RE.match(ep):
                return self.send_json({"error": "Episode id: letters, numbers, - or _ (e.g. ep02)"}, 400)
            if (EPISODES / ep / "script.txt").exists():
                return self.send_json({"error": f"{ep} already exists"}, 400)
            r = subprocess.run([sys.executable, str(STUDIO / "studio.py"), "new", ep, data.get("title") or ep],
                               cwd=ROOT, capture_output=True, text=True)
            return self.send_json({"ok": r.returncode == 0, "log": r.stdout + r.stderr}, 200 if r.returncode == 0 else 400)
        m = re.fullmatch(r"/api/episodes/([^/]+)/(approve|estimate)", path)
        if m and self.ep_or_404(m.group(1)):
            r = subprocess.run([sys.executable, str(STUDIO / "studio.py"), m.group(2), m.group(1)],
                               cwd=ROOT, capture_output=True, text=True)
            credits = re.search(r"about ([\d,]+) credits", r.stdout)
            return self.send_json({"ok": r.returncode == 0, "log": r.stdout + r.stderr,
                                   "credits": int(credits.group(1).replace(",", "")) if credits else None},
                                  200 if r.returncode == 0 else 400)
        m = re.fullmatch(r"/api/episodes/([^/]+)/(write|voices|build)", path)
        if not m or not self.ep_or_404(m.group(1)):
            return self.send_json({"error": "not found"}, 404)
        ep, action = m.groups()
        if action == "write":
            premise = (data.get("premise") or "").strip()
            if not premise:
                return self.send_json({"error": "Give a premise"}, 400)
            job = run_job(f"Write {ep}", [str(STUDIO / "studio.py"), "write", ep, "--premise", premise])
        elif action == "voices":
            cmd = [str(STUDIO / "studio.py"), "voices", ep] + (["--offline"] if data.get("offline") else
                                                                ["--yes"] if data.get("confirmed") else [])
            job = run_job(f"Voices {ep}", cmd)
        else:
            cmd = [str(STUDIO / "puppet.py"), "build", ep] + (["--captions"] if data.get("captions") else [])
            job = run_job(f"Build {ep}", cmd)
        self.send_json(job)

    def do_PUT(self):
        path = urlparse(self.path).path
        m = re.fullmatch(r"/api/episodes/([^/]+)/script", path)
        if not m or not self.ep_or_404(m.group(1)):
            return self.send_json({"error": "not found"}, 404)
        text = self.body().get("script", "")
        try:
            parse_script(text)
        except ScriptError as e:
            return self.send_json({"error": str(e)}, 400)
        (EPISODES / m.group(1)).mkdir(parents=True, exist_ok=True)
        (EPISODES / m.group(1) / "script.txt").write_text(text if text.endswith("\n") else text + "\n")
        self.send_json({"ok": True})


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Endo-Monster Studio</title>
<style>
:root{--bg:#fbf6f1;--card:#fff;--ink:#3a2433;--muted:#86707d;--rose:#c4467f;--rose2:#f6d9e6;--line:#ead9df}
*{box-sizing:border-box}body{margin:0;font:16px/1.45 system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--ink)}
header{padding:18px 20px;background:var(--rose);color:#fff}header h1{margin:0;font-size:22px}
main{max-width:1100px;margin:0 auto;padding:16px;display:grid;gap:16px;grid-template-columns:280px 1fr}
@media(max-width:800px){main{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px}
h2{font-size:16px;margin:0 0 10px}button{font:inherit;border:0;border-radius:10px;padding:9px 14px;background:var(--rose);color:#fff;cursor:pointer}
button.ghost{background:var(--rose2);color:var(--ink)}button:disabled{opacity:.5;cursor:default}
input,textarea{font:inherit;width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:10px;background:#fff;color:var(--ink)}
textarea{font-family:ui-monospace,Menlo,monospace;font-size:14px;min-height:340px}
.ep{display:block;width:100%;text-align:left;margin:4px 0;background:#fff;color:var(--ink);border:1px solid var(--line)}
.ep.on{border-color:var(--rose);background:var(--rose2)}.small{font-size:13px;color:var(--muted)}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:8px 0}
pre{background:#2b1d26;color:#f7e9ef;border-radius:10px;padding:10px;max-height:220px;overflow:auto;font-size:12.5px;white-space:pre-wrap}
video,img.lineup{width:100%;border-radius:10px;background:#000}.err{color:#b0263f;white-space:pre-wrap}
</style></head><body>
<header><h1>There's an Endo-Monster in My Tummy! &middot; Studio</h1></header>
<main>
 <section>
  <div class="card"><h2>Episodes</h2><div id="eps"></div></div>
  <div class="card" style="margin-top:16px"><h2>New episode</h2>
   <input id="newId" placeholder="id, e.g. ep02"><div style="height:6px"></div>
   <input id="newTitle" placeholder="Title"><div class="row"><button id="mk">Create</button></div>
   <div id="newErr" class="err small"></div></div>
  <div class="card" style="margin-top:16px"><h2>Cast</h2><div id="lineup" class="small">No lineup yet (puppet.py lineup)</div></div>
 </section>
 <section id="detail" class="card"><p class="small">Pick or create an episode.</p></section>
</main>
<script>
let cur=null, poll=null;
const $=s=>document.querySelector(s);
async function api(path,opt={}){const r=await fetch(path,{headers:{'Content-Type':'application/json'},...opt});
 const j=await r.json().catch(()=>({error:'bad response'}));if(!r.ok)throw new Error(j.error||r.statusText);return j}
async function load(){const j=await api('/api/episodes');const box=$('#eps');box.innerHTML='';
 j.episodes.forEach(e=>{const b=document.createElement('button');b.className='ep'+(e.id===cur?' on':'');
  b.innerHTML=`<b>${e.id}</b> &middot; ${esc(e.title)}<br><span class="small">${e.lines} lines &middot; ${e.voiced} voiced${e.video?' &middot; video':''}</span>`;
  b.onclick=()=>open(e.id);box.appendChild(b)});
 if(j.lineup)$('#lineup').innerHTML=`<a href="${j.lineup}" target="_blank"><img class="lineup" src="${j.lineup}" alt="Character lineup"></a>`;
 return j}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
async function open(id){cur=id;const [list,s]=await Promise.all([load(),api(`/api/episodes/${id}/script`)]);
 const e=list.episodes.find(x=>x.id===id);
 $('#detail').innerHTML=`<h2>${esc(e.title)} <span class="small">(${id})</span></h2>
  <div class="row"><input id="premise" placeholder="Premise for Claude to write this episode"><button id="write">Write script</button></div>
  <textarea id="script" spellcheck="false"></textarea>
  <div class="row"><span class="small" id="appr">${e.approval==='approved'?'&#10003; Approved '+esc(e.approved_at||''):e.approval==='changed'?'Changed since approval: approve again':'Not approved yet'}</span></div>
  <div class="row"><button class="ghost" id="save">Save script</button>
   <button class="ghost" id="approve">Approve script</button>
   <button id="voices">Make voices</button><label class="small"><input type="checkbox" id="offline" style="width:auto"> silent test voices</label>
   <button id="build">Build video</button><label class="small"><input type="checkbox" id="captions" style="width:auto"> captions</label></div>
  <div id="msg" class="err small">${e.problems?esc(e.problems):''}</div>
  <pre id="log" hidden></pre>
  <div id="vid">${e.video?`<video controls src="${e.video}"></video>`:''}</div>`;
 $('#script').value=s.script;
 $('#save').onclick=save;
 $('#write').onclick=()=>job(`/api/episodes/${id}/write`,{premise:$('#premise').value});
 $('#approve').onclick=async()=>{if(!await save())return;try{const r=await api(`/api/episodes/${id}/approve`,{method:'POST',body:'{}'});
   $('#msg').textContent=r.log;await open(id);$('#msg').textContent=r.log}catch(e){$('#msg').textContent=e.message}};
 $('#voices').onclick=async()=>{if(!await save())return;const offline=$('#offline').checked;
   if(offline)return job(`/api/episodes/${id}/voices`,{offline:true});
   const info=await load();const ep=info.episodes.find(x=>x.id===id);
   if(ep.approval!=='approved'){$('#msg').textContent='Approve the script first, so voices are only made once.';return}
   const est=await api(`/api/episodes/${id}/estimate`,{method:'POST',body:'{}'});
   if(!est.credits){$('#msg').textContent='Every line already has its recording. Nothing to spend.';return}
   if(confirm(`Voice ${est.log.match(/Lines to voice now: (\d+)/)[1]} lines for about ${est.credits.toLocaleString()} ElevenLabs credits?`))
     job(`/api/episodes/${id}/voices`,{confirmed:true})};
 $('#build').onclick=async()=>{if(await save())job(`/api/episodes/${id}/build`,{captions:$('#captions').checked})};
}
async function save(){$('#msg').textContent='';try{await api(`/api/episodes/${cur}/script`,{method:'PUT',body:JSON.stringify({script:$('#script').value})});
 $('#msg').textContent='';load();return true}catch(e){$('#msg').textContent=e.message;return false}}
async function job(path,data){$('#msg').textContent='';let j;try{j=await api(path,{method:'POST',body:JSON.stringify(data)})}catch(e){$('#msg').textContent=e.message;return}
 document.querySelectorAll('#detail button').forEach(b=>b.disabled=true);const log=$('#log');log.hidden=false;clearInterval(poll);
 poll=setInterval(async()=>{const s=await api('/api/jobs/'+j.id);log.textContent=s.label+' - '+s.status+'\n'+s.log;log.scrollTop=1e9;
  if(s.status!=='running'){clearInterval(poll);const keep=log.textContent;await open(cur);$('#log').hidden=false;$('#log').textContent=keep}},1000)}
$('#mk').onclick=async()=>{$('#newErr').textContent='';try{const id=$('#newId').value.trim();
 await api('/api/episodes',{method:'POST',body:JSON.stringify({id,title:$('#newTitle').value.trim()})});$('#newId').value='';$('#newTitle').value='';open(id)}catch(e){$('#newErr').textContent=e.message}};
load();
</script></body></html>"""


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Endo-Monster Studio running at http://localhost:{PORT}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
