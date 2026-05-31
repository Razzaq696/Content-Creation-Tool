"""
Abdul Tool — Flask API Server
Railway.app pe deploy karo
"""
from flask import Flask, request, jsonify, send_file
import os, threading, uuid, datetime, subprocess, shutil, json

app = Flask(__name__)
JOBS         = {}
DOWNLOAD_DIR = "/tmp/downloads"
CLIPS_DIR    = "/tmp/clips"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(CLIPS_DIR,    exist_ok=True)

API_KEY = os.environ.get("API_KEY", "abdultool-secret-2024")

def check_auth():
    return request.headers.get("X-API-Key", "") == API_KEY

# ── Health ───────────────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    # Check ffmpeg available
    try:
        result = subprocess.run(["ffmpeg", "-version"],
            capture_output=True, text=True, timeout=5)
        ffmpeg_ok = "ffmpeg version" in result.stdout
    except Exception:
        ffmpeg_ok = False
    try:
        import yt_dlp
        ytdlp_ok = True
    except Exception:
        ytdlp_ok = False
    return jsonify({
        "status":    "ok",
        "app":       "Abdul Tool API",
        "version":   "2.0",
        "ffmpeg":    ffmpeg_ok,
        "yt_dlp":    ytdlp_ok,
    })

# ── Download video ────────────────────────────────────────────────────────────
@app.route("/download", methods=["POST"])
def download():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json()
    url  = (data or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "URL required"}), 400

    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "status": "queued", "percent": 0,
        "downloaded_mb": 0, "total_mb": 0,
        "speed_kb": 0, "eta_sec": 0,
        "result": None, "filename": None, "error": None
    }

    def _run():
        import yt_dlp
        out_tmpl = os.path.join(DOWNLOAD_DIR, f"{job_id}.%(ext)s")

        def _hook(d):
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 1
                dl    = d.get("downloaded_bytes", 0)
                JOBS[job_id].update({
                    "status":        "downloading",
                    "percent":       round(dl / total * 100, 1),
                    "downloaded_mb": round(dl / 1048576, 1),
                    "total_mb":      round(total / 1048576, 1),
                    "speed_kb":      round((d.get("speed") or 0) / 1024, 1),
                    "eta_sec":       d.get("eta") or 0,
                })
            elif d.get("status") == "finished":
                JOBS[job_id].update({"status": "processing", "percent": 99})

        opts = {
            "format":              "bestvideo[height<=720]+bestaudio/best",
            "merge_output_format": "mp4",
            "outtmpl":             out_tmpl,
            "progress_hooks":      [_hook],
            "noplaylist":          True,
            "quiet":               True,
            "extractor_args":      {"youtube": {"player_client": ["android", "web"]}},
            "http_headers":        {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
            },
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info  = ydl.extract_info(url, download=True)
                fname = ydl.prepare_filename(info)
                mp4   = os.path.splitext(fname)[0] + ".mp4"
                path  = mp4 if os.path.exists(mp4) else fname
                size  = os.path.getsize(path) / 1048576
                JOBS[job_id].update({
                    "status":   "done",
                    "percent":  100,
                    "result":   path,
                    "filename": os.path.basename(path),
                    "size_mb":  round(size, 1),
                })
        except Exception as e:
            JOBS[job_id].update({"status": "failed", "error": str(e)})

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": job_id})

# ── Job status ────────────────────────────────────────────────────────────────
@app.route("/job/<job_id>", methods=["GET"])
def job_status(job_id):
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)

# ── Split video ───────────────────────────────────────────────────────────────
@app.route("/split", methods=["POST"])
def split():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401

    data      = request.get_json() or {}
    job_id    = data.get("job_id", "")
    clip_secs = int(data.get("clip_seconds", 4))

    # Find video path
    video_path = None
    if job_id and job_id in JOBS and JOBS[job_id].get("status") == "done":
        video_path = JOBS[job_id]["result"]
    elif job_id == "device_video":
        return jsonify({"error": "Device video split not supported on server. Use URL download first."}), 400

    if not video_path or not os.path.exists(video_path):
        return jsonify({"error": "Video not found. Please download first."}), 400

    split_job_id = f"{job_id}_split"
    JOBS[split_job_id] = {
        "status": "processing", "percent": 0,
        "clips": [], "count": 0, "error": None
    }

    def _split():
        try:
            out_dir = os.path.join(CLIPS_DIR, job_id)
            os.makedirs(out_dir, exist_ok=True)

            # Check ffmpeg
            ff = shutil.which("ffmpeg") or "ffmpeg"

            # Get video duration first
            probe_cmd = [ff, "-i", video_path]
            probe = subprocess.run(probe_cmd, capture_output=True, text=True)
            duration = 0
            for line in probe.stderr.split("\n"):
                if "Duration:" in line:
                    try:
                        t = line.split("Duration:")[1].split(",")[0].strip()
                        h, m, s = t.split(":")
                        duration = int(h)*3600 + int(m)*60 + float(s)
                    except Exception:
                        pass

            JOBS[split_job_id]["total_duration"] = duration

            cmd = [
                ff, "-i", video_path,
                "-c", "copy",
                "-map", "0",
                "-segment_time", str(clip_secs),
                "-f", "segment",
                "-reset_timestamps", "1",
                "-y",
                os.path.join(out_dir, "clip_%04d.mp4")
            ]
            process = subprocess.Popen(
                cmd, stderr=subprocess.PIPE,
                stdout=subprocess.PIPE, text=True
            )

            # Parse ffmpeg progress
            for line in process.stderr:
                if "time=" in line and duration > 0:
                    try:
                        t_str = line.split("time=")[1].split(" ")[0].strip()
                        h, m, s = t_str.split(":")
                        current = int(h)*3600 + int(m)*60 + float(s)
                        pct = min(round(current / duration * 100, 1), 99)
                        JOBS[split_job_id]["percent"] = pct
                    except Exception:
                        pass

            process.wait()

            if process.returncode != 0:
                raise Exception(f"ffmpeg error (code {process.returncode})")

            clips = sorted([
                os.path.join(out_dir, f)
                for f in os.listdir(out_dir)
                if f.endswith(".mp4")
            ])

            JOBS[split_job_id].update({
                "status":  "done",
                "percent": 100,
                "clips":   [os.path.basename(c) for c in clips],
                "count":   len(clips),
                "job_dir": job_id,
            })

        except Exception as e:
            JOBS[split_job_id].update({
                "status": "failed",
                "error":  str(e)
            })

    threading.Thread(target=_split, daemon=True).start()
    return jsonify({"split_job_id": split_job_id})

# ── Face filter ───────────────────────────────────────────────────────────────
@app.route("/face-filter", methods=["POST"])
def face_filter():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401

    data          = request.get_json() or {}
    job_id        = data.get("job_id", "")
    filter_type   = data.get("filter_type", "any")
    clip_dir      = os.path.join(CLIPS_DIR, job_id)

    if not os.path.exists(clip_dir):
        return jsonify({"error": "Clips not found. Run /split first."}), 400

    filter_job_id = f"{job_id}_filter"
    JOBS[filter_job_id] = {
        "status": "processing", "percent": 0,
        "matched": [], "total": 0, "count": 0, "error": None
    }

    def _filter():
        try:
            import cv2, mediapipe as mp
            mp_face = mp.solutions.face_detection.FaceDetection(
                model_selection=0, min_detection_confidence=0.5)

            clips   = sorted([f for f in os.listdir(clip_dir) if f.endswith(".mp4")])
            matched = []
            total   = len(clips)
            JOBS[filter_job_id]["total"] = total

            for i, clip in enumerate(clips):
                clip_path = os.path.join(clip_dir, clip)
                cap   = cv2.VideoCapture(clip_path)
                found = False
                fc    = 0
                while cap.isOpened() and not found:
                    ret, frame = cap.read()
                    if not ret: break
                    if fc % 15 == 0:
                        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        res = mp_face.process(rgb)
                        if res.detections:
                            found = True
                    fc += 1
                cap.release()
                if found:
                    matched.append(clip)

                pct = round((i + 1) / total * 100, 1)
                JOBS[filter_job_id].update({
                    "percent": pct,
                    "matched": matched,
                    "count":   len(matched),
                })

            JOBS[filter_job_id].update({
                "status":  "done",
                "percent": 100,
                "matched": matched,
                "count":   len(matched),
                "total":   total,
            })
        except Exception as e:
            JOBS[filter_job_id].update({"status": "failed", "error": str(e)})

    threading.Thread(target=_filter, daemon=True).start()
    return jsonify({"filter_job_id": filter_job_id})

# ── Clips list ────────────────────────────────────────────────────────────────
@app.route("/clips/<job_id>", methods=["GET"])
def list_clips(job_id):
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401
    clip_dir = os.path.join(CLIPS_DIR, job_id)
    if not os.path.exists(clip_dir):
        return jsonify({"clips": [], "count": 0})
    clips = sorted([f for f in os.listdir(clip_dir) if f.endswith(".mp4")])
    return jsonify({"clips": clips, "count": len(clips)})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
