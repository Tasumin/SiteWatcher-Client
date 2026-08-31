import atexit
import json
import os
import re
import subprocess
import threading
import time
from urllib.parse import quote

import requests
import websocket

from .checks import CREATE_FLAGS, _rtsp_with_credentials, _tool
from .viewing_window import enter_viewing_window, leave_viewing_window

MAX_STREAMS = max(1, int(os.getenv("SITEWATCH_MAX_LIVE_STREAMS", "2")))
MAX_TRANSCODES = max(0, int(os.getenv("SITEWATCH_MAX_LIVE_TRANSCODES", "1")))
MAX_TRANSCODE_BITRATE_KBPS = max(256, int(os.getenv("SITEWATCH_LIVE_MAX_BITRATE_KBPS", "2500")))
MAX_TRANSCODE_WIDTH = max(320, int(os.getenv("SITEWATCH_LIVE_MAX_WIDTH", "1280")))
TRANSCODE_THREADS = max(1, min(16, int(os.getenv("SITEWATCH_LIVE_TRANSCODE_THREADS", "2"))))
STARTUP_TIMEOUT = max(5, int(os.getenv("SITEWATCH_LIVE_STARTUP_TIMEOUT_SECONDS", "15")))
AUDIO_BITRATE_KBPS = max(32, min(256, int(os.getenv("SITEWATCH_LIVE_AUDIO_BITRATE_KBPS", "96"))))

_workers = {}
_workers_lock = threading.RLock()


def _ws_base(server_url: str) -> str:
    base = server_url.rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base[len("https://"):]
    if base.startswith("http://"):
        return "ws://" + base[len("http://"):]
    raise ValueError("SITEWATCH_SERVER_URL must start with http:// or https://")


def _redact(text: str) -> str:
    return re.sub(r"(rtsp://[^:/\s@]+:)[^@\s]+@", r"\1*****@", str(text or ""), flags=re.I)[-1200:]


def _stop_probe(proc):
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate(); proc.wait(timeout=2)
    except Exception:
        try: proc.kill(); proc.wait(timeout=2)
        except Exception: pass


def _probe(target: str, timeout: int, session_id: str):
    cmd = [
        _tool("ffprobe"), "-v", "error", "-rtsp_transport", "tcp",
        "-timeout", str(timeout * 1_000_000),
        "-show_entries", "stream=codec_type,codec_name,width,height,bit_rate,avg_frame_rate",
        "-of", "json", target,
    ]
    proc = None
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=CREATE_FLAGS)
        print(f"[live] RTSP probe started session={session_id[:8]} pid={proc.pid}", flush=True)
        try:
            stdout, stderr = proc.communicate(timeout=timeout + 3)
        except subprocess.TimeoutExpired as exc:
            _stop_probe(proc)
            raise RuntimeError("RTSP connection timeout") from exc
    except RuntimeError:
        raise
    except Exception as exc:
        _stop_probe(proc)
        raise RuntimeError(str(exc)) from exc
    if proc.returncode != 0:
        err = _redact(stderr)
        low = err.lower()
        if "401" in low or "unauthorized" in low or "authentication" in low:
            raise RuntimeError("RTSP authentication failed")
        if "timed out" in low or "timeout" in low:
            raise RuntimeError("RTSP connection timeout")
        if "connection refused" in low:
            raise RuntimeError("RTSP connection refused")
        raise RuntimeError(err or "Unable to open RTSP stream")
    try:
        streams = json.loads(stdout or "{}").get("streams") or []
        video = next((stream for stream in streams if str(stream.get("codec_type") or "").lower() == "video"), None)
        audio = next((stream for stream in streams if str(stream.get("codec_type") or "").lower() == "audio"), None)
    except Exception as exc:
        raise RuntimeError("Unable to read RTSP codec information") from exc
    if not video or not video.get("codec_name"):
        raise RuntimeError("No video stream found")
    return {"video": video, "audio": audio}


def _reserve_mode(session_id: str, codec: str) -> str:
    with _workers_lock:
        record = _workers.get(session_id)
        if not record:
            raise RuntimeError("Live stream job was cancelled")
        if codec.lower() in {"h264", "avc1"}:
            record["mode"] = "copy"
            return "copy"
        active = sum(
            1 for sid, worker in _workers.items()
            if sid != session_id and worker.get("mode") == "transcode" and not worker["stop"].is_set()
        )
        if MAX_TRANSCODES < 1 or active >= MAX_TRANSCODES:
            raise RuntimeError("H.265 transcoding capacity exhausted")
        record["mode"] = "transcode"
        return "transcode"


def _ffmpeg_command(target: str, mode: str, timeout: int, has_audio: bool):
    common = [
        _tool("ffmpeg"), "-hide_banner", "-loglevel", "warning",
        "-rtsp_transport", "tcp", "-timeout", str(timeout * 1_000_000),
        "-fflags", "+genpts+discardcorrupt", "-i", target, "-map", "0:v:0",
    ]
    if has_audio:
        common += [
            "-map", "0:a:0?",
            "-c:a", "aac", "-b:a", f"{AUDIO_BITRATE_KBPS}k", "-ar", "48000",
        ]
    else:
        common += ["-an"]
    if mode == "copy":
        return common + [
            "-c:v", "copy", "-tag:v", "avc1",
            "-movflags", "+frag_keyframe+empty_moov+default_base_moof+omit_tfhd_offset",
            "-f", "mp4", "pipe:1",
        ]
    return common + [
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
        "-threads", str(TRANSCODE_THREADS), "-pix_fmt", "yuv420p",
        "-vf", f"scale='min({MAX_TRANSCODE_WIDTH},iw)':-2:force_original_aspect_ratio=decrease",
        "-maxrate", f"{MAX_TRANSCODE_BITRATE_KBPS}k", "-bufsize", f"{MAX_TRANSCODE_BITRATE_KBPS * 2}k",
        "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
        "-movflags", "+frag_keyframe+empty_moov+default_base_moof+omit_tfhd_offset",
        "-f", "mp4", "pipe:1",
    ]


def _send_json(ws, payload):
    ws.send(json.dumps(payload, separators=(",", ":")))


def _report_stream_node(server_url: str, token: str, node_id: str):
    if not node_id:
        return
    try:
        response = requests.post(
            server_url.rstrip("/") + "/api/agent/stream-node",
            headers={"Authorization": f"Bearer {token}"},
            json={"nodeId": node_id},
            timeout=10,
        )
        if not response.ok:
            print(f"[live] unable to register relay node: HTTP {response.status_code}", flush=True)
    except Exception as exc:
        print(f"[live] unable to register relay node: {exc}", flush=True)


def _stderr_reader(proc, tail, stop_event):
    try:
        while not stop_event.is_set():
            line = proc.stderr.readline()
            if not line:
                break
            tail.append(_redact(line.decode("utf-8", errors="replace").strip()))
            if len(tail) > 20:
                del tail[:-20]
    except Exception:
        pass


def _stream_worker(server_url: str, token: str, job: dict, node_id: str, control_ws):
    session_id = str(job.get("sessionId") or "")
    source_type = str(job.get("sourceType") or "")
    source_id = str(job.get("sourceId") or "")
    url = str(job.get("url") or "")
    username = job.get("username")
    password = job.get("password")
    timeout = max(3, int(job.get("timeoutSeconds") or STARTUP_TIMEOUT))
    stop_event = None
    proc = None
    uplink = None
    stderr_tail = []
    try:
        with _workers_lock:
            record = _workers.get(session_id)
            if not record:
                return
            stop_event = record["stop"]
        if not session_id or source_type not in {"device", "nvr_stream"} or not source_id or not url:
            raise RuntimeError("Malformed live stream job")

        enter_viewing_window(source_type, source_id, session_id)
        target = _rtsp_with_credentials(url, username, password)
        probe = _probe(target, timeout, session_id)
        info = probe["video"]
        audio_info = probe.get("audio")
        has_audio = bool(audio_info and audio_info.get("codec_name"))
        codec = str(info.get("codec_name") or "").lower()
        mode = _reserve_mode(session_id, codec)
        command = _ffmpeg_command(target, mode, timeout, has_audio)

        uplink_url = (
            f"{_ws_base(server_url)}/stream/uplink"
            f"?session={quote(session_id, safe='')}"
            f"&token={quote(token, safe='')}"
        )
        options = {"timeout": 20, "enable_multithread": True}
        if node_id:
            options["cookie"] = "sitewatch_tunnel_node=" + quote(node_id, safe="-_.~")
        uplink = websocket.create_connection(uplink_url, **options)
        uplink.settimeout(None)

        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            bufsize=0,
            creationflags=CREATE_FLAGS,
        )
        with _workers_lock:
            if session_id in _workers:
                _workers[session_id]["proc"] = proc
        threading.Thread(target=_stderr_reader, args=(proc, stderr_tail, stop_event), daemon=True).start()
        mime_type = 'video/mp4; codecs="avc1.42E01E, mp4a.40.2"' if has_audio else "video/mp4; codecs=avc1.42E01E"
        _send_json(uplink, {
            "type": "stream-ready",
            "sessionId": session_id,
            "mimeType": mime_type,
            "mode": mode,
            "sourceCodec": codec,
            "outputCodec": "h264",
            "audioCodec": "aac" if has_audio else None,
            "sourceAudioCodec": str(audio_info.get("codec_name") or "").lower() if has_audio else None,
            "hasAudio": has_audio,
            "width": info.get("width"),
            "height": info.get("height"),
            "bitrateKbps": int(info.get("bit_rate") or 0) // 1000 if str(info.get("bit_rate") or "").isdigit() else None,
        })
        audio_log = f" audio={str(audio_info.get('codec_name') or '').lower()}->aac" if has_audio else " audio=none"
        print(f"[live] session={session_id[:8]} source={source_type}:{source_id} codec={codec} mode={mode}{audio_log}", flush=True)

        init_buffer = bytearray()
        init_sent = False
        bytes_sent = 0
        last_stats = time.monotonic()
        while not stop_event.is_set():
            chunk = proc.stdout.read(65536)
            if not chunk:
                if proc.poll() is not None:
                    break
                time.sleep(0.01)
                continue
            bytes_sent += len(chunk)
            if not init_sent:
                init_buffer.extend(chunk)
                marker = init_buffer.find(b"moof")
                if marker >= 4:
                    media_start = marker - 4
                    init = bytes(init_buffer[:media_start])
                    media = bytes(init_buffer[media_start:])
                    if init:
                        uplink.send(b"\x00" + init, opcode=websocket.ABNF.OPCODE_BINARY)
                    if media:
                        uplink.send(b"\x01" + media, opcode=websocket.ABNF.OPCODE_BINARY)
                    init_buffer.clear()
                    init_sent = True
                elif len(init_buffer) > 2 * 1024 * 1024:
                    raise RuntimeError("FFmpeg produced an invalid fragmented MP4 initialization segment")
            else:
                uplink.send(b"\x01" + chunk, opcode=websocket.ABNF.OPCODE_BINARY)

            now = time.monotonic()
            if now - last_stats >= 5:
                _send_json(uplink, {"type": "stats", "sessionId": session_id, "bytesSent": bytes_sent})
                last_stats = now

        if stop_event.is_set():
            try:
                _send_json(uplink, {"type": "stream-stop", "sessionId": session_id, "reason": "requested"})
            except Exception:
                pass
        elif proc and proc.poll() not in (None, 0):
            detail = next((line for line in reversed(stderr_tail) if line), "FFmpeg stream process exited")
            raise RuntimeError(detail)
        else:
            raise RuntimeError("Live RTSP stream ended")
    except Exception as exc:
        message = _redact(str(exc)) or "Live stream failed"
        print(f"[live] session={session_id[:8]} failed: {message}", flush=True)
        target_ws = uplink if uplink is not None else control_ws
        try:
            _send_json(target_ws, {"type": "stream-error", "sessionId": session_id, "error": message})
        except Exception:
            pass
    finally:
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        if uplink is not None:
            try:
                uplink.close()
            except Exception:
                pass
        if source_type and source_id and session_id:
            leave_viewing_window(source_type, source_id, session_id)
        with _workers_lock:
            _workers.pop(session_id, None)
        print(f"[live] session={session_id[:8]} worker cleaned up", flush=True)


def _start_job(server_url: str, token: str, job: dict, node_id: str, control_ws):
    session_id = str(job.get("sessionId") or "")
    if not session_id:
        return
    with _workers_lock:
        if session_id in _workers:
            return
        if len(_workers) >= MAX_STREAMS:
            _send_json(control_ws, {"type": "stream-error", "sessionId": session_id, "error": "Agent stream limit reached"})
            return
        _workers[session_id] = {"stop": threading.Event(), "mode": None, "proc": None}
    thread = threading.Thread(
        target=_stream_worker,
        args=(server_url, token, dict(job), node_id, control_ws),
        name=f"sitewatch-live-{session_id[:8]}",
        daemon=True,
    )
    thread.start()


def _stop_job(session_id: str):
    with _workers_lock:
        record = _workers.get(str(session_id))
        if record:
            record["stop"].set()


def _stop_all_workers():
    with _workers_lock:
        records = list(_workers.values())
    for record in records:
        try:
            record["stop"].set()
        except Exception:
            pass
        proc = record.get("proc")
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass


atexit.register(_stop_all_workers)


def live_stream_loop(server_url: str, token: str):
    retry_seconds = 2
    while True:
        control_ws = None
        node_id = ""
        try:
            control_url = f"{_ws_base(server_url)}/stream/agent?token={quote(token, safe='')}"
            print(f"[live] connecting streaming control channel to {_ws_base(server_url)}", flush=True)
            control_ws = websocket.create_connection(control_url, timeout=20, enable_multithread=True)
            control_ws.settimeout(None)
            retry_seconds = 2
            while True:
                raw = control_ws.recv()
                if raw is None or raw == b"" or raw == "":
                    raise ConnectionError("stream control channel closed")
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                try:
                    message = json.loads(raw)
                except Exception:
                    continue
                if not isinstance(message, dict):
                    continue
                message_type = str(message.get("type") or "")
                if message_type == "ready":
                    node_id = str(message.get("nodeId") or "")
                    _report_stream_node(server_url, token, node_id)
                    print(f"[live] streaming control channel connected node={node_id or 'default'}", flush=True)
                elif message_type == "stream-start":
                    _start_job(server_url, token, message, node_id, control_ws)
                elif message_type == "stream-stop":
                    _stop_job(str(message.get("sessionId") or ""))
        except Exception as exc:
            print(f"[live] control disconnected: {exc}; retrying in {retry_seconds}s", flush=True)
        finally:
            if control_ws is not None:
                try:
                    control_ws.close()
                except Exception:
                    pass
        time.sleep(retry_seconds)
        retry_seconds = min(retry_seconds * 2, 30)
