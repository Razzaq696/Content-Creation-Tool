"""
Abdul Tool — Flask API Server
Deploy on Railway.app / Render.com
"""
from flask import Flask, request, jsonify
import os, threading, uuid, datetime, yt_dlp, subprocess, shutil

app = Flask(__name__)
JOBS = {}          # job_id → {status, percent, result, error}
DOWNLOAD_DIR = "/tmp/downloads"
CLIPS_DIR    = "/tmp/clips"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(CLIPS_DIR,    exist_ok=True)

# ── Auth middleware ──────────────────────────────────────────────────────────
API_KEY = os.environ.get("API_KEY", "abdultool-secret-2024")

def check_auth():
    key = request.headers.get("X-API-Key", "")
    return key == API_KEY

# ── Health check ─────────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "app": "Abdul Tool API", "version": "1.0"})

# ── Download video ───────────────────────────────────────────────────────────
@app.route("/download", methods=["POST"])
def download():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()
    url  = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL required"}), 400

    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {"status": "queued", "percent": 0, "result": None, "error": None}

    def _run():
        out_tmpl = os.path.join(DOWNLOAD_DIR, f"{job_id}.%(ext)s")

        def _hook(d):
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 1
                dl    = d.get("downloaded_bytes", 0)
                JOBS[job_id]["percent"]       = round(dl / total * 100, 1)
                JOBS[job_id]["downloaded_mb"] = round(dl / 1048576, 1)
                JOBS[job_id]["total_mb"]      = round(total / 1048576, 1)
                JOBS[job_id]["speed_kb"]      = round((d.get("speed") or 0) / 1024, 1)
                JOBS[job_id]["eta_sec"]       = d.get("eta") or 0
                JOBS[job_id]["status"]        = "downloading"
            elif d.get("status") == "finished":
                JOBS[job_id]["status"]  = "processing"
                JOBS[job_id]["percent"] = 99

        opts = {
            "format": "bestvideo[height<=720]+bestaudio/best",
            "merge_output_format": "mp4",
            "outtmpl": out_tmpl,
            "progress_hooks": [_hook],
            "noplaylist": True,
            "quiet": True,
            "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
            },
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                fname = ydl.prepare_filename(info)
                mp4   = os.path.splitext(fname)[0] + ".mp4"
                path  = mp4 if os.path.exists(mp4) else fname
                JOBS[job_id].update({
                    "status":    "done",
                    "percent":   100,
                    "result":    path,
                    "filename":  os.path.basename(path),
                    "size_mb":   round(os.path.getsize(path) / 1048576, 1),
                })
        except Exception as e:
            JOBS[job_id].update({"status": "failed", "error": str(e)})

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": job_id, "message": "Download started"})

# ── Job status ───────────────────────────────────────────────────────────────
@app.route("/job/<job_id>", methods=["GET"])
def job_status(job_id):
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)

# ── Split video ──────────────────────────────────────────────────────────────
@app.route("/split", methods=["POST"])
def split():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401

    data      = request.get_json()
    job_id    = data.get("job_id", "")
    clip_secs = int(data.get("clip_seconds", 4))

    job = JOBS.get(job_id)
    if not job or job["status"] != "done":
        return jsonify({"error": "Invalid or unfinished job"}), 400

    video_path = job["result"]
    out_dir    = os.path.join(CLIPS_DIR, job_id)
    os.makedirs(out_dir, exist_ok=True)

    split_job_id = f"{job_id}_split"
    JOBS[split_job_id] = {"status": "processing", "percent": 0, "clips": [], "error": None}

    def _split():
        try:
            cmd = [
                "ffmpeg", "-i", video_path,
                "-c", "copy", "-map", "0",
                "-segment_time", str(clip_secs),
                "-f", "segment", "-reset_timestamps", "1",
                os.path.join(out_dir, "clip_%03d.mp4"),
                "-y"
            ]
            subprocess.run(cmd, capture_output=True, check=True)
            clips = sorted([
                f"/clips/{job_id}/{f}"
                for f in os.listdir(out_dir) if f.endswith(".mp4")
            ])
            JOBS[split_job_id].update({
                "status":  "done",
                "percent": 100,
                "clips":   clips,
                "count":   len(clips),
            })
        except Exception as e:
            JOBS[split_job_id].update({"status": "failed", "error": str(e)})

    threading.Thread(target=_split, daemon=True).start()
    return jsonify({"split_job_id": split_job_id})

# ── Face filter ──────────────────────────────────────────────────────────────
@app.route("/face-filter", methods=["POST"])
def face_filter():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401

    data     = request.get_json()
    job_id   = data.get("job_id", "")
    clip_dir = os.path.join(CLIPS_DIR, job_id)

    if not os.path.exists(clip_dir):
        return jsonify({"error": "Clips not found. Run /split first."}), 400

    filter_job_id = f"{job_id}_filter"
    JOBS[filter_job_id] = {"status": "processing", "percent": 0,
                           "matched": [], "total": 0, "error": None}

    def _filter():
        try:
            import cv2
            import mediapipe as mp
            mp_face = mp.solutions.face_detection.FaceDetection(
                model_selection=0, min_detection_confidence=0.5)

            clips   = sorted([f for f in os.listdir(clip_dir) if f.endswith(".mp4")])
            matched = []
            total   = len(clips)

            for i, clip in enumerate(clips):
                clip_path = os.path.join(clip_dir, clip)
                cap = cv2.VideoCapture(clip_path)
                found = False
                frame_count = 0
                while cap.isOpened() and not found:
                    ret, frame = cap.read()
                    if not ret: break
                    if frame_count % 10 == 0:
                        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        result = mp_face.process(rgb)
                        if result.detections:
                            found = True
                    frame_count += 1
                cap.release()
                if found:
                    matched.append(f"/clips/{job_id}/{clip}")
                pct = round((i + 1) / total * 100, 1)
                JOBS[filter_job_id].update({"percent": pct, "matched": matched})

            JOBS[filter_job_id].update({
                "status":  "done",
                "percent": 100,
                "matched": matched,
                "total":   total,
                "count":   len(matched),
            })
        except Exception as e:
            JOBS[filter_job_id].update({"status": "failed", "error": str(e)})

    threading.Thread(target=_filter, daemon=True).start()
    return jsonify({"filter_job_id": filter_job_id})

# ── Clips list ───────────────────────────────────────────────────────────────
@app.route("/clips/<job_id>", methods=["GET"])
def list_clips(job_id):
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401
    clip_dir = os.path.join(CLIPS_DIR, job_id)
    if not os.path.exists(clip_dir):
        return jsonify({"clips": []})
    clips = sorted([f for f in os.listdir(clip_dir) if f.endswith(".mp4")])
    return jsonify({"clips": clips, "count": len(clips)})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
