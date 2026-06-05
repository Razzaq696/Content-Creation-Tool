Abdul Tool — Flask API Server v5.0
- YouTube fix: android_vr player
- Face Detection: OpenCV Haar Cascade (no YOLO, no download needed!)
"""
from flask import Flask, request, jsonify, send_file
import os, threading, uuid, subprocess, shutil, zipfile, io, traceback
import cv2
import numpy as np

app = Flask(__name__)
JOBS         = {}
DOWNLOAD_DIR = "/tmp/downloads"
CLIPS_DIR    = "/tmp/clips"
UPLOAD_DIR   = "/tmp/uploads"
REF_DIR      = "/tmp/ref_faces"
for d in [DOWNLOAD_DIR, CLIPS_DIR, UPLOAD_DIR, REF_DIR]:
    os.makedirs(d, exist_ok=True)

API_KEY = os.environ.get("API_KEY", "abdultool-secret-2024")

BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
YOUTUBE_COOKIES  = os.path.join(BASE_DIR, "cookies.txt")

# ── OpenCV Face Cascade (built-in, no download!) ──────────────────────────────
FACE_CASCADE = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

def check_auth():
    return request.headers.get("X-API-Key", "") == API_KEY

def upd(job_id, **kw):
    if job_id in JOBS:
        JOBS[job_id].update(kw)

def detect_platform(url):
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u: return "youtube"
    if "tiktok.com"  in u: return "tiktok"
    if "instagram.com" in u: return "instagram"
    return "other"

# ── Health ────────────────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    try:
        r = subprocess.run(["ffmpeg","-version"], capture_output=True, timeout=5)
        ff = r.returncode == 0
    except: ff = False
    try:
        import yt_dlp; yt = True
    except: yt = False
    return jsonify({
        "status":       "ok",
        "ffmpeg":       ff,
        "yt_dlp":       yt,
        "face_engine":  "OpenCV Haar Cascade (built-in)",
        "opencv":       cv2.__version__,
        "version":      "5.0",
        "cookies_exist": os.path.exists(YOUTUBE_COOKIES),
    })

# ── Upload cookies ────────────────────────────────────────────────────────────
@app.route("/upload-cookies", methods=["POST"])
def upload_cookies():
    if not check_auth(): return jsonify({"error":"Unauthorized"}),401
    if "file" not in request.files:
        return jsonify({"error":"No file. Field name: file"}),400
    request.files["file"].save(YOUTUBE_COOKIES)
    return jsonify({"success":True,"message":"cookies.txt saved"})

# ── Download video ────────────────────────────────────────────────────────────
@app.route("/download", methods=["POST"])
def download():
    if not check_auth(): return jsonify({"error":"Unauthorized"}),401
    data = request.get_json() or {}
    url  = data.get("url","").strip()
    if not url: return jsonify({"error":"URL required"}),400

    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {"status":"queued","percent":0,"downloaded_mb":0,
                    "total_mb":0,"speed_kb":0,"eta_sec":0,
                    "result":None,"filename":None,"size_mb":0,"error":None}

    def _run():
        import yt_dlp
        platform    = detect_platform(url)
        out_tmpl    = os.path.join(DOWNLOAD_DIR, f"{job_id}.%(ext)s")
        cookies_ok  = os.path.exists(YOUTUBE_COOKIES)

        def _hook(d):
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 1
                dl    = d.get("downloaded_bytes",0)
                upd(job_id, status="downloading",
                    percent=round(dl/total*100,1),
                    downloaded_mb=round(dl/1048576,1),
                    total_mb=round(total/1048576,1),
                    speed_kb=round((d.get("speed") or 0)/1024,1),
                    eta_sec=d.get("eta") or 0)
            elif d.get("status") == "finished":
                upd(job_id, status="processing", percent=99)

        # Base opts
        base = {
            "outtmpl":             out_tmpl,
            "progress_hooks":      [_hook],
            "noplaylist":          True,
            "quiet":               True,
            "no_warnings":         True,
            "socket_timeout":      30,
            "retries":             10,
            "fragment_retries":    10,
            "merge_output_format": "mp4",
        }
        if cookies_ok:
            base["cookiefile"] = YOUTUBE_COOKIES

        # Platform configs
        if platform == "youtube":
            # ✅ android_vr is the best bot bypass in 2025
            configs = [
                {**base,
                 "format": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]/best",
                 "extractor_args": {"youtube": {"player_client": ["android_vr"]}},
                 "http_headers": {"User-Agent": "com.google.android.youtube/19.09.37 (Linux; U; Android 13; en_US) gzip"}},
                {**base,
                 "format": "18",
                 "extractor_args": {"youtube": {"player_client": ["android_vr"]}},
                 "http_headers": {"User-Agent": "com.google.android.youtube/19.09.37 (Linux; U; Android 13; en_US) gzip"}},
                {**base,
                 "format": "best",
                 "extractor_args": {"youtube": {"player_client": ["android","web"]}},
                 "http_headers": {"User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36"}},
            ]
        elif platform == "tiktok":
            configs = [
                {**base,
                 "format": "bestvideo[ext=mp4]+bestaudio/best[ext=mp4]/best",
                 "http_headers": {"User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36",
                                  "Referer": "https://www.tiktok.com/"}},
            ]
        elif platform == "instagram":
            configs = [
                {**base,
                 "format": "bestvideo[ext=mp4]+bestaudio/best[ext=mp4]/best",
                 "http_headers": {"User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36"}},
            ]
        else:
            configs = [
                {**base,
                 "format": "bestvideo[height<=720][ext=mp4]+bestaudio/best[height<=720]/best",
                 "http_headers": {"User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36"}},
            ]

        last_err = None
        for opts in configs:
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info  = ydl.extract_info(url, download=True)
                    fname = ydl.prepare_filename(info)
                    mp4   = os.path.splitext(fname)[0]+".mp4"
                    path  = mp4 if os.path.exists(mp4) else fname
                    if os.path.exists(path):
                        upd(job_id, status="done", percent=100,
                            result=path, filename=os.path.basename(path),
                            size_mb=round(os.path.getsize(path)/1048576,1))
                        return
            except Exception as e:
                last_err = str(e)
                continue

        upd(job_id, status="failed",
            error=f"All attempts failed. Last error: {last_err}")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": job_id})

# ── Upload video from device ──────────────────────────────────────────────────
@app.route("/upload", methods=["POST"])
def upload_video():
    if not check_auth(): return jsonify({"error":"Unauthorized"}),401
    if "video" not in request.files:
        return jsonify({"error":"No video file"}),400
    f      = request.files["video"]
    job_id = str(uuid.uuid4())[:8]
    path   = os.path.join(UPLOAD_DIR, f"{job_id}_{f.filename}")
    f.save(path)
    size   = os.path.getsize(path)/1048576
    JOBS[job_id] = {"status":"done","percent":100,"result":path,
                    "filename":f.filename,"size_mb":round(size,1),"error":None}
    return jsonify({"job_id":job_id,"filename":f.filename,
                    "size_mb":round(size,1),"message":"Uploaded"})

# ── Upload reference face image ───────────────────────────────────────────────
@app.route("/upload-ref-face", methods=["POST"])
def upload_ref_face():
    if not check_auth(): return jsonify({"error":"Unauthorized"}),401
    if "image" not in request.files:
        return jsonify({"error":"No image. Field name: image"}),400
    f      = request.files["image"]
    job_id = request.form.get("job_id","default")
    ext    = os.path.splitext(f.filename)[1] or ".jpg"
    path   = os.path.join(REF_DIR, f"{job_id}_ref{ext}")
    f.save(path)

    img = cv2.imread(path)
    if img is None:
        return jsonify({"error":"Cannot read image file"}),400

    gray  = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    faces = FACE_CASCADE.detectMultiScale(gray, 1.1, 4, minSize=(30,30))
    if len(faces) == 0:
        return jsonify({"error":"No face detected in reference image. Use a clear frontal photo."}),400

    # Save face crop embedding
    x, y, w, h = faces[0]
    crop = cv2.resize(img[y:y+h, x:x+w], (64,64)).astype(np.float32)/255.0
    np.save(os.path.join(REF_DIR, f"{job_id}_ref.npy"), crop.flatten())

    return jsonify({"success":True,
                    "message":f"Face detected! Ready for specific face search."})

# ── Job status ────────────────────────────────────────────────────────────────
@app.route("/job/<job_id>", methods=["GET"])
def job_status(job_id):
    if not check_auth(): return jsonify({"error":"Unauthorized"}),401
    job = JOBS.get(job_id)
    if not job: return jsonify({"error":"Job not found"}),404
    return jsonify(job)

# ── Split video ───────────────────────────────────────────────────────────────
@app.route("/split", methods=["POST"])
def split():
    if not check_auth(): return jsonify({"error":"Unauthorized"}),401
    data      = request.get_json() or {}
    job_id    = data.get("job_id","")
    clip_secs = int(data.get("clip_seconds",4))

    job = JOBS.get(job_id)
    if not job or job.get("status") != "done":
        return jsonify({"error":"Video not ready."}),400
    video_path = job.get("result","")
    if not os.path.exists(video_path):
        return jsonify({"error":"Video file not found"}),400

    split_job_id = f"{job_id}_split"
    JOBS[split_job_id] = {"status":"processing","percent":0,
                          "clips":[],"count":0,"error":None}

    def _split():
        try:
            out_dir = os.path.join(CLIPS_DIR, job_id)
            os.makedirs(out_dir, exist_ok=True)
            ff = shutil.which("ffmpeg") or "ffmpeg"

            probe = subprocess.run([ff,"-i",video_path],
                capture_output=True, text=True)
            duration = 0
            for line in probe.stderr.split("\n"):
                if "Duration:" in line:
                    try:
                        t = line.split("Duration:")[1].split(",")[0].strip()
                        h,m,s = t.split(":")
                        duration = int(h)*3600+int(m)*60+float(s)
                    except: pass

            cmd = [ff,"-i",video_path,"-c","copy","-map","0",
                   "-segment_time",str(clip_secs),"-f","segment",
                   "-reset_timestamps","1","-y",
                   os.path.join(out_dir,"clip_%04d.mp4")]

            proc = subprocess.Popen(cmd, stderr=subprocess.PIPE,
                                    stdout=subprocess.PIPE, text=True)
            for line in proc.stderr:
                if "time=" in line and duration > 0:
                    try:
                        t = line.split("time=")[1].split(" ")[0].strip()
                        h,m,s = t.split(":")
                        cur = int(h)*3600+int(m)*60+float(s)
                        upd(split_job_id, percent=min(round(cur/duration*100,1),99))
                    except: pass
            proc.wait()
            if proc.returncode != 0:
                raise Exception(f"FFmpeg error code {proc.returncode}")

            clips = sorted([f for f in os.listdir(out_dir) if f.endswith(".mp4")])
            upd(split_job_id, status="done", percent=100,
                clips=clips, count=len(clips), job_dir=job_id)
        except Exception as e:
            upd(split_job_id, status="failed", error=str(e))

    threading.Thread(target=_split, daemon=True).start()
    return jsonify({"split_job_id": split_job_id})

# ── Face Filter — OpenCV ──────────────────────────────────────────────────────
@app.route("/face-filter", methods=["POST"])
def face_filter():
    if not check_auth(): return jsonify({"error":"Unauthorized"}),401
    data        = request.get_json() or {}
    job_id      = data.get("job_id","")
    filter_type = data.get("filter_type","any")
    clip_secs   = int(data.get("clip_seconds",4))

    clip_dir   = os.path.join(CLIPS_DIR, job_id)
    clips_ok   = (os.path.exists(clip_dir) and
                  any(f.endswith(".mp4") for f in os.listdir(clip_dir)))

    # Auto-split if clips not found
    if not clips_ok:
        job = JOBS.get(job_id)
        if not job or not job.get("result"):
            return jsonify({"error":"Video not found. Upload first."}),400
        video_path = job["result"]
        if not os.path.exists(video_path):
            return jsonify({"error":"Video file missing"}),400
        os.makedirs(clip_dir, exist_ok=True)
        ff = shutil.which("ffmpeg") or "ffmpeg"
        subprocess.run([ff,"-i",video_path,"-c","copy","-map","0",
                        "-segment_time",str(clip_secs),"-f","segment",
                        "-reset_timestamps","1","-y",
                        os.path.join(clip_dir,"clip_%04d.mp4")],
                       capture_output=True)

    filter_job_id = f"{job_id}_filter"
    JOBS[filter_job_id] = {"status":"processing","percent":0,
                           "matched":[],"total":0,"count":0,"error":None}

    def _filter():
        try:
            clips = sorted([f for f in os.listdir(clip_dir) if f.endswith(".mp4")])
            total = len(clips)
            upd(filter_job_id, total=total)

            # Load reference for specific face
            ref_vec = None
            if filter_type == "specific":
                npy = os.path.join(REF_DIR, f"{job_id}_ref.npy")
                if os.path.exists(npy):
                    ref_vec = np.load(npy)
                else:
                    upd(filter_job_id, status="failed",
                        error="Reference face not found. Upload reference image first.")
                    return

            matched = []
            for i, clip in enumerate(clips):
                cap   = cv2.VideoCapture(os.path.join(clip_dir, clip))
                found = False
                fc    = 0
                while cap.isOpened() and not found:
                    ret, frame = cap.read()
                    if not ret: break
                    if fc % 15 == 0:
                        gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                        faces = FACE_CASCADE.detectMultiScale(
                            gray, scaleFactor=1.1, minNeighbors=4,
                            minSize=(30,30))
                        if len(faces) > 0:
                            if filter_type == "any":
                                found = True
                            elif ref_vec is not None:
                                # Compare face crop
                                for (x,y,w,h) in faces:
                                    crop = cv2.resize(
                                        frame[y:y+h, x:x+w],
                                        (64,64)).astype(np.float32)/255.0
                                    vec = crop.flatten()
                                    # Cosine similarity
                                    sim = float(np.dot(ref_vec, vec) /
                                                (np.linalg.norm(ref_vec)*np.linalg.norm(vec)+1e-8))
                                    if sim >= 0.70:
                                        found = True
                                        break
                    fc += 1
                cap.release()
                if found: matched.append(clip)
                upd(filter_job_id,
                    percent=round((i+1)/total*100,1),
                    matched=matched, count=len(matched))

            upd(filter_job_id, status="done", percent=100,
                matched=matched, count=len(matched), total=total)

        except Exception as e:
            upd(filter_job_id, status="failed",
                error=str(e)+"\n"+traceback.format_exc())

    threading.Thread(target=_filter, daemon=True).start()
    return jsonify({"filter_job_id": filter_job_id})

# ── Serve files ───────────────────────────────────────────────────────────────
@app.route("/get-video/<job_id>", methods=["GET"])
def get_video(job_id):
    if not check_auth(): return jsonify({"error":"Unauthorized"}),401
    job = JOBS.get(job_id)
    if not job or not job.get("result"): return jsonify({"error":"Not found"}),404
    path = job["result"]
    if not os.path.exists(path): return jsonify({"error":"File missing"}),404
    return send_file(path, as_attachment=True,
                     download_name=job.get("filename","video.mp4"))

@app.route("/get-clips/<job_id>", methods=["GET"])
def get_clips(job_id):
    if not check_auth(): return jsonify({"error":"Unauthorized"}),401
    clip_dir = os.path.join(CLIPS_DIR, job_id)
    if not os.path.exists(clip_dir): return jsonify({"error":"No clips"}),404
    clips = sorted([f for f in os.listdir(clip_dir) if f.endswith(".mp4")])
    if not clips: return jsonify({"error":"Empty"}),404
    buf = io.BytesIO()
    with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED) as zf:
        for c in clips:
            zf.write(os.path.join(clip_dir,c), c)
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name=f"all_clips_{job_id}.zip",
                     mimetype="application/zip")

@app.route("/get-matched/<job_id>", methods=["GET"])
def get_matched(job_id):
    if not check_auth(): return jsonify({"error":"Unauthorized"}),401
    fj = JOBS.get(f"{job_id}_filter")
    if not fj or fj.get("status") != "done":
        return jsonify({"error":"Filter not done"}),400
    matched  = fj.get("matched",[])
    clip_dir = os.path.join(CLIPS_DIR, job_id)
    if not matched: return jsonify({"error":"No matched clips"}),404
    buf = io.BytesIO()
    with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED) as zf:
        for c in matched:
            p = os.path.join(clip_dir,c)
            if os.path.exists(p): zf.write(p,c)
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name=f"matched_{job_id}.zip",
                     mimetype="application/zip")

if __name__ == "__main__":
    port = int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0", port=port, debug=False)
