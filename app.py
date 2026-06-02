"""
Abdul Tool — Flask API Server v3.2
Face Detection: YOLOv8 (ultralytics)
- Any Face:      YOLOv8n-face — fast detection
- Specific Face: YOLOv8n-face crop + cosine similarity embeddings
"""
from flask import Flask, request, jsonify, send_file
import os, threading, uuid, subprocess, shutil, zipfile, io, traceback

app = Flask(__name__)

JOBS         = {}
DOWNLOAD_DIR = "/tmp/downloads"
CLIPS_DIR    = "/tmp/clips"
UPLOAD_DIR   = "/tmp/uploads"
REF_DIR      = "/tmp/ref_faces"
MODEL_DIR    = "/tmp/models"

for d in [DOWNLOAD_DIR, CLIPS_DIR, UPLOAD_DIR, REF_DIR, MODEL_DIR]:
    os.makedirs(d, exist_ok=True)

API_KEY = os.environ.get("API_KEY", "abdultool-secret-2024")

# ── YOLO model path ────────────────────────────────────────────────────────────
YOLO_FACE_MODEL = os.path.join(MODEL_DIR, "yolov8n-face.pt")
YOLO_MODEL_URL  = "https://github.com/SannketNikam/Face-Detection/raw/main/yolov8n-face.pt"

def get_yolo_model():
    """Load YOLOv8 face model — download once, cache in /tmp/models."""
    from ultralytics import YOLO
    if not os.path.exists(YOLO_FACE_MODEL):
        import urllib.request
        urllib.request.urlretrieve(YOLO_MODEL_URL, YOLO_FACE_MODEL)
    return YOLO(YOLO_FACE_MODEL)

# Shared model instance (loaded lazily)
_yolo_model      = None
_yolo_model_lock = threading.Lock()

def yolo():
    global _yolo_model
    if _yolo_model is None:
        with _yolo_model_lock:
            if _yolo_model is None:
                _yolo_model = get_yolo_model()
    return _yolo_model

# ── Helpers ────────────────────────────────────────────────────────────────────
def check_auth():
    return request.headers.get("X-API-Key", "") == API_KEY

def update_job(job_id, **kwargs):
    if job_id in JOBS:
        JOBS[job_id].update(kwargs)

def get_face_embedding(img_rgb):
    """
    Detect face with YOLO, crop it, resize to 128x128,
    return flattened normalized numpy array as embedding.
    """
    import cv2, numpy as np
    model   = yolo()
    results = model(img_rgb, verbose=False, conf=0.4)
    boxes   = results[0].boxes

    if boxes is None or len(boxes) == 0:
        return None

    # Pick largest face box
    best_box = None
    best_area = 0
    for box in boxes.xyxy.tolist():
        x1, y1, x2, y2 = map(int, box[:4])
        area = (x2 - x1) * (y2 - y1)
        if area > best_area:
            best_area = area
            best_box  = (x1, y1, x2, y2)

    if best_box is None:
        return None

    x1, y1, x2, y2 = best_box
    # Clamp to image bounds
    h, w = img_rgb.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)

    crop = img_rgb[y1:y2, x1:x2]
    if crop.size == 0:
        return None

    crop_resized = cv2.resize(crop, (128, 128)).astype(np.float32) / 255.0
    embedding    = crop_resized.flatten()
    norm         = np.linalg.norm(embedding)
    return embedding / norm if norm > 0 else embedding

def cosine_similarity(a, b):
    import numpy as np
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))

# ── Health ─────────────────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    ffmpeg_ok = False
    ytdlp_ok  = False
    yolo_ok   = False
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        ffmpeg_ok = r.returncode == 0
    except Exception:
        pass
    try:
        import yt_dlp
        ytdlp_ok = True
    except Exception:
        pass
    try:
        from ultralytics import YOLO
        yolo_ok = True
    except Exception:
        pass
    return jsonify({"status": "ok", "ffmpeg": ffmpeg_ok,
                    "yt_dlp": ytdlp_ok, "yolo": yolo_ok, "version": "3.2"})

# ── Download video (URL) ───────────────────────────────────────────────────────
@app.route("/download", methods=["POST"])
def download():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json() or {}
    url  = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL required"}), 400

    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "status": "queued", "percent": 0,
        "downloaded_mb": 0, "total_mb": 0,
        "speed_kb": 0, "eta_sec": 0,
        "result": None, "filename": None,
        "size_mb": 0, "error": None
    }

    def _run():
        import yt_dlp
        out_tmpl = os.path.join(DOWNLOAD_DIR, f"{job_id}.%(ext)s")

        def _hook(d):
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 1
                dl    = d.get("downloaded_bytes", 0)
                update_job(job_id,
                    status        = "downloading",
                    percent       = round(dl / total * 100, 1),
                    downloaded_mb = round(dl / 1048576, 1),
                    total_mb      = round(total / 1048576, 1),
                    speed_kb      = round((d.get("speed") or 0) / 1024, 1),
                    eta_sec       = d.get("eta") or 0,
                )
            elif d.get("status") == "finished":
                update_job(job_id, status="processing", percent=99)

        format_attempts = [
            "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[height<=720]/best",
            "18",
            "best",
        ]
        opts_base = {
            "outtmpl":        out_tmpl,
            "progress_hooks": [_hook],
            "noplaylist":     True,
            "quiet":          True,
            "no_warnings":    True,
            "socket_timeout": 30,
            "retries":        3,
            "extractor_args": {
                "youtube": {
                    "player_client": ["android_vr", "android", "web"],
                    "player_skip":   ["webpage", "configs"],
                }
            },
            "http_headers": {
                "User-Agent": (
                    "com.google.android.youtube/17.36.4 "
                    "(Linux; U; Android 12; GB) gzip"
                ),
            },
        }
        last_error = None
        for fmt in format_attempts:
            try:
                opts = {**opts_base, "format": fmt, "merge_output_format": "mp4"}
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info  = ydl.extract_info(url, download=True)
                    fname = ydl.prepare_filename(info)
                    mp4   = os.path.splitext(fname)[0] + ".mp4"
                    path  = mp4 if os.path.exists(mp4) else fname
                    if os.path.exists(path):
                        size = os.path.getsize(path) / 1048576
                        update_job(job_id,
                            status="done", percent=100,
                            result=path, filename=os.path.basename(path),
                            size_mb=round(size, 1)
                        )
                        return
            except Exception as e:
                last_error = str(e)
                continue
        update_job(job_id, status="failed",
                   error=last_error or "All download attempts failed")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": job_id})

# ── Upload video from device ───────────────────────────────────────────────────
@app.route("/upload", methods=["POST"])
def upload_video():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401
    if "video" not in request.files:
        return jsonify({"error": "No video file in request"}), 400

    file      = request.files["video"]
    job_id    = str(uuid.uuid4())[:8]
    save_path = os.path.join(UPLOAD_DIR, f"{job_id}_{file.filename}")
    file.save(save_path)
    size_mb = os.path.getsize(save_path) / 1048576
    JOBS[job_id] = {
        "status":   "done",  "percent":  100,
        "result":   save_path, "filename": file.filename,
        "size_mb":  round(size_mb, 1), "error": None,
    }
    return jsonify({
        "job_id":   job_id,
        "filename": file.filename,
        "size_mb":  round(size_mb, 1),
        "message":  "Video uploaded successfully"
    })

# ── Upload reference face image ────────────────────────────────────────────────
@app.route("/upload-ref-face", methods=["POST"])
def upload_ref_face():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401
    if "image" not in request.files:
        return jsonify({"error": "No image file. Use field name: image"}), 400

    file   = request.files["image"]
    job_id = request.form.get("job_id", "")
    if not job_id:
        return jsonify({"error": "job_id required"}), 400

    ext      = os.path.splitext(file.filename)[1] or ".jpg"
    ref_path = os.path.join(REF_DIR, f"{job_id}_ref{ext}")
    file.save(ref_path)

    # Verify face detectable with YOLO + save embedding
    try:
        import cv2, numpy as np
        img = cv2.imread(ref_path)
        if img is None:
            os.remove(ref_path)
            return jsonify({"error": "Could not read image file."}), 400

        img_rgb   = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        embedding = get_face_embedding(img_rgb)

        if embedding is None:
            os.remove(ref_path)
            return jsonify({
                "error": "No face detected in reference image. "
                         "Use a clear frontal photo with good lighting."
            }), 400

        # Save embedding as .npy next to the image
        emb_path = os.path.join(REF_DIR, f"{job_id}_ref.npy")
        np.save(emb_path, embedding)

        return jsonify({
            "success": True,
            "message": "✅ Face detected and saved! Ready for specific face search."
        })

    except Exception as e:
        return jsonify({"error": f"Processing error: {str(e)}"}), 500

# ── Job status ─────────────────────────────────────────────────────────────────
@app.route("/job/<job_id>", methods=["GET"])
def job_status(job_id):
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)

# ── Split video ────────────────────────────────────────────────────────────────
@app.route("/split", methods=["POST"])
def split():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401

    data      = request.get_json() or {}
    job_id    = data.get("job_id", "")
    clip_secs = int(data.get("clip_seconds", 4))

    job = JOBS.get(job_id)
    if not job or job.get("status") != "done":
        return jsonify({"error": "Video not ready. Download or upload first."}), 400

    video_path = job.get("result")
    if not video_path or not os.path.exists(video_path):
        return jsonify({"error": "Video file not found on server."}), 400

    split_job_id = f"{job_id}_split"
    JOBS[split_job_id] = {
        "status": "processing", "percent": 0,
        "clips": [], "count": 0, "error": None
    }

    def _split():
        try:
            out_dir = os.path.join(CLIPS_DIR, job_id)
            os.makedirs(out_dir, exist_ok=True)
            ff = shutil.which("ffmpeg") or "ffmpeg"

            # Get duration
            probe    = subprocess.run([ff, "-i", video_path],
                                      capture_output=True, text=True)
            duration = 0
            for line in probe.stderr.split("\n"):
                if "Duration:" in line:
                    try:
                        t = line.split("Duration:")[1].split(",")[0].strip()
                        h, m, s = t.split(":")
                        duration = int(h)*3600 + int(m)*60 + float(s)
                    except Exception:
                        pass

            cmd = [
                ff, "-i", video_path,
                "-c", "copy", "-map", "0",
                "-segment_time", str(clip_secs),
                "-f", "segment", "-reset_timestamps", "1", "-y",
                os.path.join(out_dir, "clip_%04d.mp4")
            ]
            process = subprocess.Popen(cmd, stderr=subprocess.PIPE,
                                       stdout=subprocess.PIPE, text=True)
            for line in process.stderr:
                if "time=" in line and duration > 0:
                    try:
                        t_str = line.split("time=")[1].split(" ")[0].strip()
                        h, m, s = t_str.split(":")
                        cur = int(h)*3600 + int(m)*60 + float(s)
                        pct = min(round(cur / duration * 100, 1), 99)
                        update_job(split_job_id, percent=pct)
                    except Exception:
                        pass
            process.wait()
            if process.returncode != 0:
                raise Exception(f"ffmpeg failed code {process.returncode}")

            clips = sorted([f for f in os.listdir(out_dir) if f.endswith(".mp4")])
            update_job(split_job_id, status="done", percent=100,
                       clips=clips, count=len(clips), job_dir=job_id)
        except Exception as e:
            update_job(split_job_id, status="failed", error=str(e))

    threading.Thread(target=_split, daemon=True).start()
    return jsonify({"split_job_id": split_job_id})

# ── Face Filter — YOLO ─────────────────────────────────────────────────────────
@app.route("/face-filter", methods=["POST"])
def face_filter():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401

    data        = request.get_json() or {}
    job_id      = data.get("job_id", "")
    filter_type = data.get("filter_type", "any")   # "any" | "specific"
    threshold   = float(data.get("threshold", 0.75))
    clip_secs   = int(data.get("clip_seconds", 4))
    clip_dir    = os.path.join(CLIPS_DIR, job_id)

    # ── Auto-split if clips don't exist yet ───────────────────────────────
    clips_exist = (os.path.exists(clip_dir) and
                   any(f.endswith(".mp4") for f in os.listdir(clip_dir)))
    if not clips_exist:
        job = JOBS.get(job_id)
        if not job or not job.get("result"):
            return jsonify({"error": "Video not found. Upload a video first."}), 400
        video_path = job["result"]
        if not os.path.exists(video_path):
            return jsonify({"error": "Video file missing on server."}), 400
        os.makedirs(clip_dir, exist_ok=True)
        ff = shutil.which("ffmpeg") or "ffmpeg"
        subprocess.run([
            ff, "-i", video_path,
            "-c", "copy", "-map", "0",
            "-segment_time", str(clip_secs),
            "-f", "segment", "-reset_timestamps", "1", "-y",
            os.path.join(clip_dir, "clip_%04d.mp4")
        ], capture_output=True)

    filter_job_id = f"{job_id}_filter"
    JOBS[filter_job_id] = {
        "status": "processing", "percent": 0,
        "matched": [], "total": 0, "count": 0,
        "mode": filter_type, "error": None
    }

    def _filter():
        try:
            import cv2, numpy as np

            model = yolo()  # load once

            # ── Load reference embedding for specific mode ─────────────────
            ref_embedding = None
            if filter_type == "specific":
                emb_path = os.path.join(REF_DIR, f"{job_id}_ref.npy")
                if os.path.exists(emb_path):
                    ref_embedding = np.load(emb_path)
                else:
                    # Try to build embedding from saved image
                    ref_files = [f for f in os.listdir(REF_DIR)
                                 if f.startswith(f"{job_id}_ref") and
                                 not f.endswith(".npy")]
                    if ref_files:
                        img = cv2.imread(os.path.join(REF_DIR, ref_files[0]))
                        if img is not None:
                            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                            ref_embedding = get_face_embedding(rgb)

                if ref_embedding is None:
                    update_job(filter_job_id, status="failed",
                               error="Reference image not found or no face detected. "
                                     "Upload a reference photo first.")
                    return

            clips   = sorted([f for f in os.listdir(clip_dir) if f.endswith(".mp4")])
            total   = len(clips)
            matched = []
            update_job(filter_job_id, total=total)

            for i, clip in enumerate(clips):
                clip_path = os.path.join(clip_dir, clip)
                cap       = cv2.VideoCapture(clip_path)
                found     = False
                frame_no  = 0

                while cap.isOpened() and not found:
                    ret, frame = cap.read()
                    if not ret:
                        break

                    # Check every 8th frame for speed
                    if frame_no % 8 == 0:
                        rgb     = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        results = model(rgb, verbose=False, conf=0.4)
                        boxes   = results[0].boxes
                        has_face = boxes is not None and len(boxes) > 0

                        if has_face:
                            if filter_type == "specific" and ref_embedding is not None:
                                # Get embedding of detected face and compare
                                frame_emb = get_face_embedding(rgb)
                                if frame_emb is not None:
                                    sim = cosine_similarity(ref_embedding, frame_emb)
                                    if sim >= threshold:
                                        found = True
                            else:
                                # Any face mode
                                found = True

                    frame_no += 1

                cap.release()
                if found:
                    matched.append(clip)

                pct = round((i + 1) / total * 100, 1)
                update_job(filter_job_id, percent=pct,
                           matched=matched, count=len(matched))

            update_job(filter_job_id,
                       status="done", percent=100,
                       matched=matched, count=len(matched),
                       total=total, source_job_id=job_id)

        except Exception as e:
            update_job(filter_job_id, status="failed",
                       error=str(e) + "\n" + traceback.format_exc())

    threading.Thread(target=_filter, daemon=True).start()
    return jsonify({"filter_job_id": filter_job_id})

# ── Serve video file ───────────────────────────────────────────────────────────
@app.route("/get-video/<job_id>", methods=["GET"])
def get_video(job_id):
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401
    job = JOBS.get(job_id)
    if not job or not job.get("result"):
        return jsonify({"error": "Video not found"}), 404
    path = job["result"]
    if not os.path.exists(path):
        return jsonify({"error": "File not found on disk"}), 404
    return send_file(path, as_attachment=True,
                     download_name=job.get("filename", "video.mp4"),
                     mimetype="video/mp4")

# ── Serve all clips as ZIP ─────────────────────────────────────────────────────
@app.route("/get-clips/<job_id>", methods=["GET"])
def get_clips(job_id):
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401
    clip_dir = os.path.join(CLIPS_DIR, job_id)
    if not os.path.exists(clip_dir):
        return jsonify({"error": "No clips found"}), 404

    clips = sorted([f for f in os.listdir(clip_dir) if f.endswith(".mp4")])
    if not clips:
        return jsonify({"error": "No clips in directory"}), 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for clip in clips:
            zf.write(os.path.join(clip_dir, clip), clip)
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name=f"all_clips_{job_id}.zip",
                     mimetype="application/zip")

# ── Serve matched clips as ZIP ─────────────────────────────────────────────────
@app.route("/get-matched/<job_id>", methods=["GET"])
def get_matched(job_id):
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401

    filter_job_id = f"{job_id}_filter"
    job = JOBS.get(filter_job_id)
    if not job or job.get("status") != "done":
        return jsonify({"error": "Filter not done yet"}), 400

    matched  = job.get("matched", [])
    clip_dir = os.path.join(CLIPS_DIR, job_id)

    if not matched:
        return jsonify({"error": "No matched clips found"}), 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for clip in matched:
            path = os.path.join(clip_dir, clip)
            if os.path.exists(path):
                zf.write(path, clip)
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name=f"matched_{job_id}.zip",
                     mimetype="application/zip")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
