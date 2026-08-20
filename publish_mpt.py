#!/usr/bin/env python3
"""publish_mpt.py — pull a MoneyPrinterTurbo video off Modal and publish it
to the constellation YouTube channel via the wired Google-Cloud YouTube
publisher (vaked-sentinel/scripts/youtube_publisher).

Usage:
  python3 publish_mpt.py                          # newest video, auto title
  python3 publish_mpt.py <task_id> --title "..." --description "..." --tags A B C
"""

import argparse
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

VIDEOS_URL = "https://peterlodri-sec--mpt-endpoints-videos.modal.run"
VIDEO_URL = "https://peterlodri-sec--mpt-endpoints-video.modal.run"
PUBLISHER = "/Users/lodripeter/workspace/peterlodri-sec/vaked-sentinel/scripts/youtube_publisher.py"

DEFAULT_TITLE = "Why curiosity matters: a short lesson for young minds"
DEFAULT_DESC = (
    "A short, honest lesson for young minds — curiosity is the engine of "
    "learning. Made with MoneyPrinterTurbo on Modal. "
    "fine touch from within · vaked.dev"
)
DEFAULT_TAGS = ["Education", "Youth", "Learning", "Curiosity", "Science", "Constellation"]


def fetch_newest() -> tuple[str, str]:
    with urllib.request.urlopen(VIDEOS_URL, timeout=60) as resp:
        items = json.loads(resp.read())["videos"]
    if not items:
        raise SystemExit("no videos on the Modal endpoint yet")
    item = items[-1]
    task_id, _, file = item.rpartition("/")
    return task_id, file


def download(task_id: str, file: str) -> Path:
    url = f"{VIDEO_URL}?task_id={task_id}&file={file}"
    tmp = Path(tempfile.gettempdir()) / f"mpt-{task_id}.mp4"
    print(f"downloading {url} -> {tmp}")
    urllib.request.urlretrieve(url, tmp)
    if tmp.stat().st_size < 8192:
        raise SystemExit(f"downloaded file too small ({tmp.stat().st_size} bytes)")
    return tmp


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("task_id", nargs="?")
    ap.add_argument("--title", default=DEFAULT_TITLE)
    ap.add_argument("--description", default=DEFAULT_DESC)
    ap.add_argument("--tags", nargs="*", default=DEFAULT_TAGS)
    args = ap.parse_args()

    if args.task_id:
        task_id, file = args.task_id, "final-1.mp4"
    else:
        task_id, file = fetch_newest()

    video = download(task_id, file)
    print(f"publishing {video} ({(video.stat().st_size / 1e6):.1f} MB) to YouTube ...")

    sys.path.insert(0, "/Users/lodripeter/workspace/peterlodri-sec/vaked-sentinel/scripts")
    from youtube_publisher import publish_to_youtube

    url = publish_to_youtube(
        video_path=str(video),
        title=args.title,
        description=args.description,
        tags=args.tags,
    )
    if not url:
        raise SystemExit("YouTube upload failed")
    print("YouTube URL:", url)


if __name__ == "__main__":
    main()
