"""Failure hunt: text -> ElevenLabs TTS -> channel -> STT (Scribe v2 + local Whisper) -> parse -> did it fail?"""
import hashlib, io, json, os, re, subprocess, sys, time
from pathlib import Path
from experiments.failure_hunt import keys
D = os.path.dirname(os.path.abspath(__file__)); A = f"{D}/audio"; os.makedirs(A, exist_ok=True)
LOG = f"{D}/attempts.jsonl"
ROOT = Path(__file__).resolve().parents[2]
_el = None; _wh = {}
def el():
    global _el
    if _el is None:
        from elevenlabs.client import ElevenLabs; _el = ElevenLabs(api_key=keys.get("ELEVENLABS_API_KEY"))
    return _el
def h(*a): return hashlib.sha256(json.dumps(a, sort_keys=True).encode()).hexdigest()[:16]
def channel_identity(src):
    return Path(src).resolve().relative_to(ROOT).as_posix()

def tts(text, voice, speed=1.0, model="eleven_flash_v2_5"):
    p = f"{A}/tts_{h(text,voice,speed,model)}.mp3"
    if not os.path.exists(p):
        audio = el().text_to_speech.convert(voice_id=voice, text=text, model_id=model,
            output_format="mp3_44100_128", voice_settings={"speed": speed, "stability": 0.5, "similarity_boost": 0.75})
        with open(p, "wb") as f: f.write(b"".join(audio))
    return p

def pcm(path):  # -> float32 16k mono
    import numpy as np
    r = subprocess.run(["ffmpeg","-v","error","-i",path,"-ac","1","-ar","16000","-f","s16le","-"],capture_output=True,check=True)
    return np.frombuffer(r.stdout, np.int16).astype(np.float32)/32768
def wav(x, path):
    import numpy as np
    raw=(np.clip(x,-1,1)*32767).astype(np.int16).tobytes()
    subprocess.run(["ffmpeg","-v","error","-y","-f","s16le","-ar","16000","-ac","1","-i","-",path],input=raw,check=True)
    return path

def channel(src, ch):
    import numpy as np
    """ch: clean | phone | phone_snr10 | phone_snr5 | phone_snr0 | trunc_X (cut X s off end of speech) | phone_trunc_X"""
    identity = channel_identity(src)
    out = f"{A}/ch_{h(identity,ch)}.wav"
    if os.path.exists(out): return out
    x = pcm(src)
    if "trunc" in ch:
        cut = float(ch.split("trunc_")[1]); nz = np.where(np.abs(x) > 0.02)[0]
        end = nz[-1] if len(nz) else len(x); x = x[:max(0, end - int(cut*16000))]
    if ch.startswith("phone"):
        tmp = wav(x, out + ".pre.wav")
        r = subprocess.run(["ffmpeg","-v","error","-i",tmp,"-af","highpass=f=300,lowpass=f=3400","-ar","8000","-acodec","pcm_mulaw","-f","wav","-"],capture_output=True,check=True)
        os.remove(tmp); y = open(out+".mu.wav","wb"); y.write(r.stdout); y.close()
        x = pcm(out+".mu.wav"); os.remove(out+".mu.wav")
        m = re.search(r"snr(\d+)", ch)
        if m:
            snr = float(m.group(1)); rng = np.random.default_rng(int(h(identity,ch)[:8],16))
            sig = np.sqrt(np.mean(x[np.abs(x)>0.01]**2)) if np.any(np.abs(x)>0.01) else 0.05
            n = rng.standard_normal(len(x)).astype(np.float32); n = np.convolve(n, np.ones(8)/8, "same")  # pinkish
            n *= sig / (10**(snr/20)) / (np.sqrt(np.mean(n**2))+1e-9); x = x + n
    return wav(x, out)

def stt_scribe(path):
    c = f"{path}.scribe.json"
    if os.path.exists(c): return json.load(open(c))["text"]
    with open(path,"rb") as f:
        r = el().speech_to_text.convert(file=io.BytesIO(f.read()), model_id="scribe_v2")
    json.dump({"text": r.text}, open(c,"w")); return r.text

def stt_whisper(path, size="small"):
    c = f"{path}.whisper_{size}.json"
    if os.path.exists(c): return json.load(open(c))
    from faster_whisper import WhisperModel
    if size not in _wh: _wh[size] = WhisperModel(size, device="cpu", compute_type="int8")
    segs, info = _wh[size].transcribe(path, language="en", beam_size=5, vad_filter=False)
    segs = list(segs); r = {"text": " ".join(s.text.strip() for s in segs),
        "no_speech_prob": max([s.no_speech_prob for s in segs], default=1.0),
        "avg_logprob": min([s.avg_logprob for s in segs], default=0.0)}
    json.dump(r, open(c,"w")); return r

# --- tiny parser for money terms ---
U={w:i for i,w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
T={w:10*i for i,w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w!="_"}
def words_to_num(ws):
    total=cur=0; got=False
    for w in ws:
        if w in U: cur+=U[w]; got=True
        elif w in T: cur+=T[w]; got=True
        elif w=="hundred": cur=(cur or 1)*100; got=True
        elif w=="thousand": total+=(cur or 1)*1000; cur=0; got=True
        elif w=="and" and got: continue
        else: break
    return (total+cur) if got else None
def norm(t): return re.sub(r"[^a-z0-9$., ]"," ",t.lower().replace("-"," "))
def parse(text):
    t = norm(text); amt = acct = None
    m = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", t) or re.search(r"([\d,]+(?:\.\d+)?)\s*dollars?", t)
    if m: amt = float(m.group(1).replace(",",""))
    else:
        toks=t.split()
        for i,w in enumerate(toks):
            if w.startswith("dollar"):
                j=i-1
                while j>=0 and (toks[j] in U or toks[j] in T or toks[j] in ("hundred","thousand","and")): j-=1
                amt = words_to_num(toks[j+1:i]); break
    m = re.search(r"ending\s+([a-z0-9 ,.]+?)(?:[,.]?\s*(?:today|$))", t)
    if m:
        seg = m.group(1).replace(",", " ").replace(".", " ").split(); d=""
        for w in seg:
            if w.isdigit(): d+=w
            elif w in U and U[w]<10: d+=str(U[w])
            elif w in U or w in T:
                n = words_to_num([w]); d += str(n)
            elif w in ("oh","o"): d+="0"
        acct = d or None
    return {"amount": amt, "acct": acct}

def run(case, text, truth, voice, speed=1.0, ch="clean", recognizers=("whisper_small","scribe")):
    src = tts(text, voice, speed); p = channel(src, ch); res={}
    for r in recognizers:
        if r=="scribe": tx = stt_scribe(p); extra={}
        else: w = stt_whisper(p, r.split("_")[1]); tx=w["text"]; extra={k:w[k] for k in ("no_speech_prob","avg_logprob")}
        pr = parse(tx); fail = [k for k in ("amount","acct") if truth.get(k) is not None and pr[k]!=truth[k]]
        res[r] = {"text": tx, **pr, "fail": fail, **extra}
    rec = {"ts": time.strftime("%H:%M:%S"), "case": case, "text": text, "voice": voice, "speed": speed, "channel": ch, "truth": truth, "results": res,
           "tts_file": channel_identity(src), "channel_file": channel_identity(p), "channel_hash_version": 2}
    open(LOG,"a").write(json.dumps(rec)+"\n"); return rec

def gen_noise(kind, secs=6):
    import numpy as np
    p = f"{A}/gen_{kind}_{secs}.wav"
    if os.path.exists(p): return p
    rng = np.random.default_rng(7); n = int(secs*16000)
    if kind == "silence": x = np.zeros(n, np.float32)
    elif kind == "hiss": x = 0.02*rng.standard_normal(n).astype(np.float32)
    elif kind == "rustle":
        x = rng.standard_normal(n).astype(np.float32)*0.05
        env = (rng.random(n//1600+1) > 0.6).repeat(1600)[:n]; x = x*env
    return wav(x, p)

def run_confirm(case, src, truth_affirm, ch="clean"):
    p = channel(src, ch); res = {}
    for r in ("scribe", "whisper_small"):
        if r == "scribe": tx = stt_scribe(p); extra = {}
        else: w = stt_whisper(p, "small"); tx = w["text"]; extra = {k: w[k] for k in ("no_speech_prob", "avg_logprob")}
        res[r] = {"text": tx, **extra}
    rec = {"ts": time.strftime("%H:%M:%S"), "case": case, "src": os.path.basename(src), "channel": ch, "truth_affirm": truth_affirm, "results": res, "kind": "confirm",
           "source_file": channel_identity(src), "channel_file": channel_identity(p), "channel_hash_version": 2}
    open(f"{D}/confirm_attempts.jsonl", "a").write(json.dumps(rec)+"\n"); return rec
