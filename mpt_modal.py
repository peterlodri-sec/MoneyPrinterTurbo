"""
MoneyPrinterTurbo on Modal: image + video generation endpoints.

Wraps the MoneyPrinterTurbo pipeline (topic -> LLM script -> Edge TTS ->
subtitles -> footage -> final mp4) as serverless Modal endpoints:

  POST /generate   { "topic": "...", "aspect": "9:16", "voice": "..." }
  GET  /videos     list generated final videos
  GET  /video/<task_id>/<file>   download a generated video

The LLM script step uses the deployment's OpenRouter key (deepseek-v4-flash
by default); footage is the bundled local clips unless a Pexels key is set.
Outputs persist in the `mpt-storage` Modal Volume under /mpt/storage.

Config is regenerated inside the container from config.example.toml + env so
no API key is baked into the image.
"""

import os
import uuid
from pathlib import Path

import modal

MPT_ROOT = Path(__file__).parent
STORAGE = "/mpt/storage"

storage_vol = modal.Volume.from_name("mpt-storage", create_if_missing=True)
mpt_secret = modal.Secret.from_name("mpt-secret")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install(
        "moviepy==2.2.1", "streamlit==1.59.1", "streamlit-tour==1.1.0",
        "edge_tts==7.2.7", "fastapi==0.136.3", "uvicorn==0.32.1",
        "openai==2.24.0", "faster-whisper==1.1.0", "loguru==0.7.3",
        "google-genai==2.11.0", "dashscope==1.20.14", "redis==5.2.0",
        "python-multipart==0.0.27", "pyyaml==6.0.3", "requests==2.33.1",
        "packaging==24.2", "socksio==1.0.0", "pydub==0.25.1",
        "litellm==1.86.2",
    )
    .add_local_dir(MPT_ROOT, "/mpt", ignore=[
        ".venv", ".git", "storage", "__pycache__", "*.pyc", "config.toml",
        "docs", ".github", "docker-compose*", "Dockerfile*", "webui.*",
    ])
)

app = modal.App("mpt-endpoints")

BUNDLED_CLIPS = [
    "/mpt/resource/videos/clip1.mp4",
    "/mpt/resource/videos/clip2.mp4",
]


def _write_config() -> None:
    """Regenerate config.toml from the example + env (no baked-in keys)."""
    example = Path("/mpt/config.example.toml").read_text()
    key = os.environ.get("OPENROUTER_API_KEY", "")
    example = example.replace(
        'llm_provider = "moonshot"', 'llm_provider = "openai"'
    ).replace(
        'openai_api_key = ""',
        f'openai_api_key = "{key}"',
    ).replace(
        'openai_base_url = ""',
        'openai_base_url = "https://openrouter.ai/api/v1"',
    ).replace(
        'openai_model_name = ""',
        'openai_model_name = "deepseek/deepseek-v4-flash"',
    )
    Path("/mpt/config.toml").write_text(example)
    Path("/mpt/storage").mkdir(parents=True, exist_ok=True)
    local_videos = Path("/mpt/storage/local_videos")
    local_videos.mkdir(parents=True, exist_ok=True)
    for clip in BUNDLED_CLIPS:
        dst = local_videos / Path(clip).name
        if not dst.exists():
            dst.write_bytes(Path(clip).read_bytes())


@app.cls(
    image=image,
    secrets=[mpt_secret],
    volumes={STORAGE: storage_vol},
    timeout=15 * 60,
)
@modal.concurrent(max_inputs=4)
class Mpt:
    @modal.enter()
    def setup(self):
        _write_config()

    @modal.method()
    def generate(
        self,
        topic: str,
        aspect: str = "9:16",
        voice: str = "en-US-JennyNeural",
        video_source: str = "local",
    ):
        import os as _os
        import sys as _sys
        _os.chdir("/mpt")
        _sys.path.insert(0, "/mpt")
        from app.models.schema import MaterialInfo, VideoParams
        from app.services.task import start

        task_id = uuid.uuid4().hex
        materials = None
        if video_source == "local":
            materials = [
                MaterialInfo(provider="local", url=Path(clip).name) for clip in BUNDLED_CLIPS
            ]
        params = VideoParams(
            video_subject=topic,
            video_aspect=aspect,
            video_source=video_source,
            video_materials=materials,
            voice_name=voice,
        )
        start(task_id, params, stop_at="video")

        task_dir = Path(STORAGE) / "tasks" / task_id
        finals = sorted(task_dir.glob("final-*.mp4")) if task_dir.exists() else []
        if not finals:
            raise RuntimeError(f"no final video produced for task {task_id}")
        name = finals[0].name
        storage_vol.commit()
        return {
            "task_id": task_id,
            "file": name,
            "download_url": f"/video/{task_id}/{name}",
        }

    @modal.method()
    def list_videos(self) -> list[dict]:
        storage_vol.reload()
        out = []
        tasks = Path(STORAGE) / "tasks"
        if tasks.exists():
            for task_dir in sorted(tasks.iterdir()):
                for final in sorted(task_dir.glob("final-*.mp4")):
                    out.append({
                        "task_id": task_dir.name,
                        "file": final.name,
                        "url": f"/video/{task_dir.name}/{final.name}",
                        "size_bytes": final.stat().st_size,
                    })
        return out


@app.local_entrypoint()
def run(topic: str = "What is a black hole, explained simply for young minds"):
    result = Mpt().generate.remote(topic=topic)
    print(result)


@app.function(image=image, secrets=[mpt_secret], volumes={STORAGE: storage_vol})
def read_video(task_id: str, file: str) -> bytes:
    storage_vol.reload()
    task_dir = Path(STORAGE) / "tasks" / task_id
    if not task_dir.exists():
        raise FileNotFoundError(f"no such task: {task_id}")
    candidates = list(task_dir.glob("*.mp4"))
    if not candidates:
        raise FileNotFoundError(f"no video for task: {task_id}")
    # A 48-byte stub is a moviepy header left mid-rename; prefer the largest file.
    target = task_dir / file if (task_dir / file).exists() else max(candidates, key=lambda p: p.stat().st_size)
    videos = [p for p in candidates if p.stat().st_size > 8192]
    if videos:
        target = max(videos, key=lambda p: p.stat().st_size)
    return target.read_bytes()


@app.function(image=image, volumes={STORAGE: storage_vol})
def list_finals() -> list[str]:
    storage_vol.reload()
    tasks = Path(STORAGE) / "tasks"
    if not tasks.exists():
        return []
    out = []
    for task_dir in sorted(tasks.iterdir()):
        if not task_dir.is_dir():
            continue
        videos = [p for p in task_dir.glob("*.mp4") if p.stat().st_size > 8192]
        if not videos:
            continue
        best = max(videos, key=lambda p: p.stat().st_size)
        out.append(f"{task_dir.name}/{best.name}")
    return out


@app.function(image=image)
@modal.fastapi_endpoint(method="POST", docs=True)
def generate_video(body: dict):
    topic = str(body.get("topic", "")).strip()
    if not topic:
        return {"error": "topic is required"}
    aspect = str(body.get("aspect", "9:16"))
    voice = str(body.get("voice", "en-US-JennyNeural"))
    source = str(body.get("video_source", "local"))
    job = Mpt().generate.spawn(topic=topic, aspect=aspect, voice=voice, video_source=source)
    return {"task_id": job.object_id, "status": "started", "poll": f"/videos"}


@app.function(image=image, volumes={STORAGE: storage_vol})
@modal.fastapi_endpoint(method="GET", docs=True)
def videos():
    from fastapi import Response
    items = list_finals.remote()
    return {"videos": items}


@app.function(image=image, volumes={STORAGE: storage_vol})
@modal.fastapi_endpoint(method="GET", docs=True)
def video(task_id: str, file: str):
    from fastapi import Response
    data = read_video.remote(task_id, file)
    return Response(content=data, media_type="video/mp4")
