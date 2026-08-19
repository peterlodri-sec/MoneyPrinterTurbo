#!/usr/bin/env python3
"""batch_shorts.py — generate + publish a batch of youth-education YouTube
Shorts, MoneyPrinterTurbo style, via the Modal endpoints and the wired
Google-Cloud YouTube publisher.

Usage: python3 batch_shorts.py [--topics-file topics.txt]
Topics default to the education-for-youth / constellation set.
"""

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

GENERATE_URL = "https://peterlodri-sec--mpt-endpoints-generate-video.modal.run"
VIDEOS_URL = "https://peterlodri-sec--mpt-endpoints-videos.modal.run"
VIDEO_URL = "https://peterlodri-sec--mpt-endpoints-video.modal.run"
PUBLISHER = Path("/Users/lodripeter/workspace/peterlodri-sec/vaked-sentinel/scripts")

DEFAULT_TOPICS = [
    ("How computers guess things: the binary search trick", "Binary Search"),
    ("What is an array, shown with a memory game", "Arrays"),
    ("How a computer checks a win in Tic Tac Toe", "Logic"),
    ("The logic of deduction: think like a code breaker", "Deduction"),
    ("Why computers use only 0 and 1", "Binary"),
    ("What is recursion, explained with a maze", "Recursion"),
    ("How video games move: timers and states", "Games"),
    ("What is an if statement, the tiny brain of code", "Code"),
]

REPO_URL = "https://github.com/peterlodri-sec/offline-game-school"
CHANNEL_NOTE = "Free offline games that teach these ideas: " + REPO_URL

VOICE = "en-US-JennyNeural"
TAGS = ["Education", "Youth", "Learning", "Shorts", "Constellation", "Science"]


def generate(topic: str) -> None:
    payload = json.dumps({"topic": topic, "aspect": "9:16", "voice": VOICE}).encode()
    req = urllib.request.Request(
        GENERATE_URL, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        body = json.loads(resp.read())
    print(f"[+] queued: {topic} ({body.get('task_id', '?')})")


def list_videos() -> list[dict]:
    with urllib.request.urlopen(VIDEOS_URL, timeout=60) as resp:
        items = json.loads(resp.read())["videos"]
    return [{"task_id": i.rpartition("/")[0], "file": i.rpartition("/")[2]} for i in items]


def download(task_id: str, file: str) -> Path:
    tmp = Path("/tmp") / f"mpt-{task_id}.mp4"
    if tmp.exists() and tmp.stat().st_size > 8192:
        return tmp
    url = f"{VIDEO_URL}?task_id={task_id}&file={file}"
    print(f"    downloading {task_id}")
    urllib.request.urlretrieve(url, tmp)
    if tmp.stat().st_size < 8192:
        raise RuntimeError(f"stub download for {task_id}")
    return tmp


def publish(task_id: str, file: str, topic: str, short: str) -> str:
    sys.path.insert(0, str(PUBLISHER))
    from youtube_publisher import publish_to_youtube

    video = download(task_id, file)
    title = f"{topic} | {short} for young minds"
    description = (
        f"{topic}. A short, honest lesson for young minds - {short}. "
        f"Free offline games that teach this idea: {REPO_URL} "
        f"Made with MoneyPrinterTurbo on Modal. fine touch from within - vaked.dev"
    )
    print(f"[*] uploading {task_id} -> {title}")
    url = publish_to_youtube(
        video_path=str(video), title=title, description=description, tags=TAGS
    )
    if not url:
        raise RuntimeError(f"upload failed for {task_id}")
    print(f"[+] published: {url}")
    return url


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topics-file", default=None)
    ap.add_argument("--max-wait-min", type=int, default=15)
    args = ap.parse_args()

    topics = DEFAULT_TOPICS
    if args.topics_file:
        topics = [(line.strip(), "") for line in Path(args.topics_file).read_text().splitlines() if line.strip()]

    before = {v["task_id"] for v in list_videos()}
    for topic, _short in topics:
        generate(topic)

    deadline = time.time() + args.max_wait_min * 60
    done = set()
    while time.time() < deadline:
        current = {v["task_id"] for v in list_videos()}
        new_ids = current - before - done
        if new_ids:
            by_id = {v["task_id"]: v for v in list_videos()}
            for task_id in sorted(new_ids):
                info = by_id[task_id]
                title, short = topics[len(done) % len(topics)]
                try:
                    publish(task_id, info["file"], title, short)
                    done.add(task_id)
                except Exception as exc:
                    print(f"[!] failed {task_id}: {exc}")
                    done.add(task_id)  # avoid re-publishing a broken one forever
        if len(done) >= len(topics):
            break
        time.sleep(30)

    print(f"done: published {len(done)} of {len(topics)}")


if __name__ == "__main__":
    main()
