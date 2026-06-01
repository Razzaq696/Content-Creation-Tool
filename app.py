"""
Abdul Tool — Flask API Server v4.0
- Video download with progress
- Video upload from device
- Split with FFmpeg
- Face filter with MediaPipe
- Download clips/video as ZIP for mobile
"""
from flask import Flask, request, jsonify, send_file
import os, threading, uuid, subprocess, shutil, zipfile, tempfile

app = Flask(__name__)
JOBS         = {}
DOWNLOAD_DIR = "/tmp/downloads"
CLIPS_DIR    = "/tmp/clips"
UPLOAD_DIR   = "/tmp/uploads"
for d in [DOWNLOAD_DIR, CLIPS_DIR, UPLOAD_DIR]:
    os.makedirs(d, exist_ok=True)

API_KEY = os.environ.get("API_KEY", "abdultool-secret-2024")

def auth():
    return request.headers.get("X-API-Key", "") == API_KEY

def upd(job_id, **kw):
    if job_id in JOBS:
        JOBS[job_id].update(kw)

# ── Health ────────────────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    try:
        r = subprocess.run(["ffmpeg","-version"], capture_output=True, timeout=5)
        ff = r.returncode == 0
    except:
        ff = False
    try:
        import yt_dlp; yt = True
    except:
        yt = False
    return jsonify({"status":"ok","ffmpeg":ff,"yt_dlp":yt,"version":"4.0"})

# ── Download ──────────────────────────────────────────────────────────────────
@app.route("/download", methods=["POST"])
def download():
    if not auth(): return jsonify({"error":"Unauthorized"}),401
    data = request.get_json() or {}
    url  = data.get("url","").strip()
    if not url: return jsonify({"error":"URL required"}),400

    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {"status":"queued","percent":0,"downloaded_mb":0,
                    "total_mb":0,"speed_kb":0,"eta_sec":0,
                    "result":None,"filename":None,"size_mb":0,"error":None}

    def _run():
        import yt_dlp
        out_tmpl = os.path.join(DOWNLOAD_DIR, f"{job_id}.%(ext)s")

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

        formats = [
            "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[height<=720]/best",
            "18", "best"
        ]
        opts_base = {
            "outtmpl": out_tmpl, "progress_hooks":[_hook],
            "noplaylist":True, "quiet":True,
            "extractor_args":{"youtube":{"player_client":["android_vr","android","web"]}},
            "http_headers":{"User-Agent":"com.google.android.youtube/17.36.4 (Linux; U; Android 12; GB) gzip"},
        }
        last_err = None
        for fmt in formats:
            try:
                opts = {**opts_base,"format":fmt,"merge_output_format":"mp4"}
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
        upd(job_id, status="failed", error=last_err)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id":job_id})

# ── Upload from device ────────────────────────────────────────────────────────
@app.route("/upload", methods=["POST"])
def upload_video():
    if not auth(): return jsonify({"error":"Unauthorized"}),401
    if "video" not in request.files:
        return jsonify({"error":"No video file"}),400
    file   = request.files["video"]
    job_id = str(uuid.uuid4())[:8]
    path   = os.path.join(UPLOAD_DIR, f"{job_id}_{file.filename}")
    file.save(path)
    size   = os.path.getsize(path)/1048576
    JOBS[job_id] = {"status":"done","percent":100,
                    "result":path,"filename":file.filename,
                    "size_mb":round(size,1),"error":None}
    return jsonify({"job_id":job_id,"filename":file.filename,
                    "size_mb":round(size,1),"message":"Uploaded successfully"})

# ── Job status ────────────────────────────────────────────────────────────────
@app.route("/job/<job_id>", methods=["GET"])
def job_status(job_id):
    if not auth(): return jsonify({"error":"Unauthorized"}),401
    job = JOBS.get(job_id)
    if not job: return jsonify({"error":"Job not found"}),404
    return jsonify(job)

# ── Split ─────────────────────────────────────────────────────────────────────
@app.route("/split", methods=["POST"])
def split():
    if not auth(): return jsonify({"error":"Unauthorized"}),401
    data      = request.get_json() or {}
    job_id    = data.get("job_id","")
    clip_secs = int(data.get("clip_seconds",4))

    job = JOBS.get(job_id)
    if not job or job.get("status") != "done":
        return jsonify({"error":"Video not ready. Download or upload first."}),400
    video_path = job.get("result")
    if not video_path or not os.path.exists(video_path):
        return jsonify({"error":"Video file not found"}),400

    split_job_id = f"{job_id}_split"
    JOBS[split_job_id] = {"status":"processing","percent":0,
                          "clips":[],"count":0,"error":None}

    def _split():
        try:
            out_dir = os.path.join(CLIPS_DIR, job_id)
            os.makedirs(out_dir, exist_ok=True)
            ff = shutil.which("ffmpeg") or "ffmpeg"

            # Get duration
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

# ── Face Filter ───────────────────────────────────────────────────────────────
@app.route("/face-filter", methods=["POST"])
def face_filter():
    if not auth(): return jsonify({"error":"Unauthorized"}),401
    data     = request.get_json() or {}
    job_id   = data.get("job_id","")
    clip_dir = os.path.join(CLIPS_DIR, job_id)

    if not os.path.exists(clip_dir):
        return jsonify({"error":"No clips found. Run split first."}),400

    clips = sorted([f for f in os.listdir(clip_dir) if f.endswith(".mp4")])
    if not clips:
        return jsonify({"error":"Clips folder is empty. Split video first."}),400

    filter_job_id = f"{job_id}_filter"
    JOBS[filter_job_id] = {"status":"processing","percent":0,
                           "matched":[],"total":len(clips),"count":0,"error":None}

    def _filter():
        try:
            import cv2
            import mediapipe as mp
            detector = mp.solutions.face_detection.FaceDetection(
                model_selection=0, min_detection_confidence=0.5)

            matched = []
            total   = len(clips)

            for i, clip in enumerate(clips):
                clip_path = os.path.join(clip_dir, clip)
                cap   = cv2.VideoCapture(clip_path)
                found = False
                fc    = 0
                while cap.isOpened() and not found:
                    ret, frame = cap.read()
                    if not ret: break
                    if fc % 15 == 0:
                        try:
                            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                            res = detector.process(rgb)
                            if res.detections:
                                found = True
                        except: pass
                    fc += 1
                cap.release()
                if found:
                    matched.append(clip)
                pct = round((i+1)/total*100, 1)
                upd(filter_job_id, percent=pct, matched=matched, count=len(matched))

            upd(filter_job_id, status="done", percent=100,
                matched=matched, count=len(matched), total=total)
        except Exception as e:
            upd(filter_job_id, status="failed", error=str(e))

    threading.Thread(target=_filter, daemon=True).start()
    return jsonify({"filter_job_id": filter_job_id})

# ── Download single file ──────────────────────────────────────────────────────
@app.route("/get-video/<job_id>", methods=["GET"])
def get_video(job_id):
    """Download the original video file to phone"""
    if not auth(): return jsonify({"error":"Unauthorized"}),401
    job = JOBS.get(job_id)
    if not job or not job.get("result"):
        return jsonify({"error":"Video not found"}),404
    path = job["result"]
    if not os.path.exists(path):
        return jsonify({"error":"File not found on server"}),404
    return send_file(path, as_attachment=True,
                     download_name=job.get("filename","video.mp4"))

# ── Download clips as ZIP ─────────────────────────────────────────────────────
@app.route("/get-clips/<job_id>", methods=["GET"])
def get_clips(job_id):
    """ZIP all clips and send to phone"""
    if not auth(): return jsonify({"error":"Unauthorized"}),401
    clip_dir = os.path.join(CLIPS_DIR, job_id)
    if not os.path.exists(clip_dir):
        return jsonify({"error":"No clips found"}),404

    clips = sorted([f for f in os.listdir(clip_dir) if f.endswith(".mp4")])
    if not clips:
        return jsonify({"error":"No clips in folder"}),404

    # Check filter results — only send matched if available
    filter_job_id = f"{job_id}_filter"
    filter_job = JOBS.get(filter_job_id)
    if filter_job and filter_job.get("status") == "done" and filter_job.get("matched"):
        clips_to_send = filter_job["matched"]
        zip_name = f"matched_clips_{job_id}.zip"
    else:
        clips_to_send = clips
        zip_name = f"all_clips_{job_id}.zip"

    # Create ZIP
    zip_path = os.path.join("/tmp", zip_name)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for clip in clips_to_send:
            clip_path = os.path.join(clip_dir, clip)
            if os.path.exists(clip_path):
                zf.write(clip_path, clip)

    return send_file(zip_path, as_attachment=True, download_name=zip_name)

# ── Download matched clips ZIP ────────────────────────────────────────────────
@app.route("/get-matched/<job_id>", methods=["GET"])
def get_matched(job_id):
    """Only send face-matched clips as ZIP"""
    if not auth(): return jsonify({"error":"Unauthorized"}),401
    filter_job_id = f"{job_id}_filter"
    fj = JOBS.get(filter_job_id)
    if not fj or fj.get("status") != "done":
        return jsonify({"error":"Face filter not done yet"}),400

    matched = fj.get("matched",[])
    if not matched:
        return jsonify({"error":"No matched clips found"}),404

    clip_dir = os.path.join(CLIPS_DIR, job_id)
    zip_name = f"matched_{job_id}.zip"
    zip_path = os.path.join("/tmp", zip_name)

    with zipfile.ZipFile(zip_path,"w",zipfile.ZIP_DEFLATED) as zf:
        for clip in matched:
            p = os.path.join(clip_dir, clip)
            if os.path.exists(p):
                zf.write(p, clip)

    return send_file(zip_path, as_attachment=True, download_name=zip_name)

if __name__ == "__main__":
    port = int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0", port=port, debug=False)
