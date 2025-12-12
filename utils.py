from PIL import Image
import time, os, re, sys
import subprocess
import signal
from hashlib import md5
from threading import Thread, Lock
import requests

import config, backend
from run_command import run_command

global_lock = Lock()
global_running_flag = True
download_jobs = []
current_url = ""
has_new_download = True

busy_flag = False

current_python = sys.executable
if not current_python:
    current_python = "python3"
print("Using python interpreter:", current_python)

if not config.custom_gallery_dl_location:
    py_exec_path = os.path.dirname(current_python)
    if py_exec_path and os.path.exists(os.path.join(py_exec_path, "gallery-dl")):
        config.custom_gallery_dl_location = os.path.join(
            py_exec_path, "gallery-dl"
        )
    print("Using gallery-dl location:", config.custom_gallery_dl_location)

config.fs_bases["x"] = os.path.expanduser(config.fs_bases["x"])
config.fs_bases["bsky"] = os.path.expanduser(config.fs_bases["bsky"])
config.fs_bases["reddit"] = os.path.expanduser(config.fs_bases["reddit"])
config.fs_bases["fa"] = os.path.expanduser(config.fs_bases["fa"])
config.cache_path = os.path.expanduser(config.cache_path)

config.url_base = config.url_base.strip("/")
if config.url_base:
    config.url_base = "/" + config.url_base


headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
}


def create_image_thumbnail(image_path, thumbnail_path, thumbnail_size):
    image = Image.open(image_path)
    image.thumbnail((thumbnail_size, thumbnail_size))
    image.convert("RGB").save(thumbnail_path)


def create_video_thumbnail(video_path, thumbnail_path):
    cmd = [
        "ffmpeg",
        "-i",
        video_path,
        "-ss",
        "00:00:00.000",
        "-vframes",
        "1",
        thumbnail_path,
    ]
    cmd = [str(x) for x in cmd]
    os.system(" ".join(cmd))


def filter_ascii(text):
    if not text:
        return ""
    return "".join(c for c in text if ord(c) < 128)


def create_thumbnail(path, thumbnail_size=config.thubnail_size):
    config.cache_path = os.path.expanduser(config.cache_path)
    if not os.path.exists(config.cache_path):
        os.makedirs(config.cache_path)
    thumbnail_path = md5(path.encode()).hexdigest() + f"_{thumbnail_size}.jpg"
    thumbnail_path = os.path.join(config.cache_path, thumbnail_path)
    if os.path.exists(thumbnail_path):
        # print("Thumbnail exists:", thumbnail_path)
        return thumbnail_path
    print("Creating thumbnail:", thumbnail_path)
    if path.split(".")[-1].lower() in ["mp4", "mov", "avi", "mkv", "webm", "m4v"]:
        create_video_thumbnail(path, thumbnail_path)
    elif path.split(".")[-1].lower() in [
        "jpg",
        "jpeg",
        "png",
        "gif",
        "bmp",
        "tiff",
        "webp",
    ]:
        create_image_thumbnail(path, thumbnail_path, thumbnail_size)
    else:
        print("Unsupported file type for thumbnail:", path)
        print("Still trying to create thumbnail with video method.")
        create_video_thumbnail(path, thumbnail_path)
    return thumbnail_path


class DownloadWorker(Thread):
    def __init__(self, db):
        super().__init__()
        self.db = db

    def run(self):
        global download_jobs, global_lock, global_running_flag, current_url, has_new_download, busy_flag
        while global_running_flag:
            try:
                with global_lock:
                    if len(download_jobs) > 0:
                        current_url, full, media_only = download_jobs.pop(0)
                        print("-->", current_url, full, media_only)
                        print(f"Downloading {current_url}")
                    else:
                        time.sleep(1)
                        continue
                if config.custom_gallery_dl_location:
                    cmd = [os.path.expanduser(config.custom_gallery_dl_location)]
                else:
                    cmd = ["gallery-dl"]
                if "bsky" in current_url:
                    # cookies not avalible yet
                    name = re.search(r"profile/([a-zA-Z0-9\-\_\.]+)", current_url)
                    if not name:
                        print("Invalid bsky URL:", current_url)
                        continue
                    name = name.group(1).lower()
                    cmd += [
                        "-c",
                        (
                            "gallery-dl-config-media-only.json"
                            if media_only
                            else "gallery-dl-config.json"
                        ),
                        current_url,
                        "-D",
                        f"{config.fs_bases['bsky']}/{name}/",
                    ]
                    cmd = [str(x) for x in cmd]
                    type = "bsky"
                elif "x.com" in current_url or "twitter.com" in current_url:
                    name = re.search(
                        r"x.com/([a-zA-Z0-9\-\_\.]+)", current_url
                    ) or re.search(r"twitter.com/([a-zA-Z0-9\-\_\.]+)", current_url)
                    if not name:
                        print("Invalid x.com URL:", current_url)
                        continue
                    name = name.group(1).lower()
                    if config.cookies_list["x"]:
                        cmd += [
                            "-c",
                            (
                                "gallery-dl-config-media-only.json"
                                if media_only
                                else "gallery-dl-config.json"
                            ),
                            "-C",
                            config.cookies_list["x"],
                            current_url,
                            "-D",
                            f"{config.fs_bases['x']}/{name}/",
                        ]
                        cmd = [str(x) for x in cmd]
                    else:
                        cmd += [
                            "-c",
                            "gallery-dl-config.json",
                            current_url,
                            "-D",
                            f"{config.fs_bases['x']}/{name}/",
                        ]
                        cmd = [str(x) for x in cmd]
                    type = "x"
                elif "reddit.com" in current_url:
                    name = re.search(r"reddit.com/r/([a-zA-Z0-9\-\_\.]+)", current_url)
                    if not name:
                        print("Invalid reddit URL:", current_url)
                        continue
                    name = name.group(1).lower()
                    cmd += [
                        "-c",
                        "gallery-dl-config.json",
                        current_url,
                        "-D",
                        f"{config.fs_bases['reddit']}/{name}/",
                    ]
                    type = "reddit"
                elif "furaffinity" in current_url:
                    name = re.search(
                        r"furaffinity.net/(user|gallery|scraps|journals)/([\w\d_\-\.\~]+)",
                        current_url,
                    )
                    if not name:
                        print("Guessing username now...")
                        user_fs_path = os.path.expanduser(config.fs_bases["fa"])
                        existing_users = os.listdir(user_fs_path)
                        existing_users.sort(
                            key=lambda x: os.path.getmtime(
                                os.path.join(user_fs_path, x)
                            ),
                            reverse=True,
                        )
                        if existing_users:
                            name = existing_users[0]
                            print("Using most recently updated user:", name)
                        else:
                            name = "ignore"
                    else:
                        name = name.group(2).lower()
                    cmd = [
                        current_python,
                        "./fadl/fadl.py",
                        "-o",
                        f"{config.fs_bases['fa']}/",
                        current_url,
                    ]
                    type = "fa"
                else:
                    print("Unsupported URL:", current_url)
                    continue
                print("User:", name, "Type:", type)

                def trigger_action():
                    backend.flag_user(self.db, name, type)

                run_command(
                    cmd,
                    ["#"] if not full else [],
                    triggers=[
                        ("NotFoundError", trigger_action),
                        ("AuthorizationError", trigger_action),
                    ],
                )
                try:
                    busy_flag = True
                    backend.scan_for_users(type, self.db, name)
                    backend.scan_for_posts(type, self.db, name)
                    backend.scan_for_media(type, self.db, name)
                    self.db.commit()
                    has_new_download = True
                    backend.query_cache = dict()
                    print(name, "downloaded")
                    busy_flag = False
                except Exception as e:
                    busy_flag = False
                    print(e)
                    print("Scan Failed.")
                current_url = ""
            except Exception as e:
                print("Error in download worker:", e)
                time.sleep(1)


def update_daemon():
    global download_jobs, global_running_flag, has_new_download, busy_flag
    try:
        users_to_watch = [u for u in backend.all_users if not u.flagged][::-1]
        for user in users_to_watch:
            if user.type == "x":
                url = f"https://x.com/{user.user_name}"
            elif user.type == "bsky":
                url = f"https://bsky.app/profile/{user.user_name}"
            else:
                continue
            download_jobs.append((url, False, True))
            print(f"[update daemon] Added {url} to queue.")
            time.sleep(10)
    except Exception as e:
        print("[update daemon]", e)
        time.sleep(10)


mention_pattern = re.compile(r"(^| |\n|[^\x00-\x7F]|\:)@([a-zA-Z0-9\-\_\.]+)")
hashtag_pattern = re.compile(r"(^| |\n|[^\x00-\x7F]|\:)#([\w\-\_\+]+)")
# url_pattern = re.compile(r"https?://[\w\-_\./@\?\=\&]+")
url_pattern = re.compile(
    r"(^| |\n|[^\x00-\x7F]|\:)([\w\-\_\.\?\=\&\#\:]+\.[\w\-\_\./@\?\=\&\#\:\+\%]+)"
)


def embed_hyperlink(type, text_content):
    if not text_content:
        return ""
    if type in ["x", "bsky", "reddit"]:
        text_content = text_content.replace("http://", "").replace("https://", "")

        urls = url_pattern.findall(text_content)
        urls = [url[1] for url in urls if not ".." in url[1]]
        urls = list(set(urls))

        for url in urls:
            if len(url) < 7:
                continue
            top_domain = url.split(".")[-1]
            if top_domain.lower() in [
                "jpg",
                "jpeg",
                "png",
                "gif",
                "bmp",
                "tiff",
                "webp",
                "mp4",
                "mov",
                "avi",
                "mkv",
                "webm",
                "m4v",
                "mp3",
                "wav",
                "flac",
                "aac",
            ]:
                continue
            https_url = "https://" + url
            if https_url.endswith("."):
                https_url = https_url[:-1]
            url_display_text = https_url.replace("https://", "")
            if len(url_display_text) > 40:
                url_display_text = url_display_text[:40] + "..."
            # print("url:", url, https_url, url_display_text)
            text_content = text_content.replace(
                url,
                f"<a class='hyperlink' href='{https_url}' target=\"_blank\">{url_display_text}</a>",
            )
        if type == "x":
            user_url = "https://x.com/{user}"
            hastag_url = "https://x.com/hashtag/{tag}"
        else:
            user_url = "https://bsky.app/profile/{user}"
            hastag_url = "https://bsky.app/hashtag/{tag}"
        mentions = [i[1] for i in mention_pattern.findall(text_content)]
        mentions = list(set(mentions))
        hashtags = [i[1] for i in hashtag_pattern.findall(text_content)]
        hashtags = list(set(hashtags))
        for mention in mentions:
            if mention.endswith("."):
                mention = mention[:-1]
            text_content = text_content.replace(
                f"@{mention}",
                f"<a class='hyperlink' href='{user_url.format(user=mention)}' target=\"_blank\">@{mention}</a>",
            )
        for hashtag in hashtags:
            text_content = text_content.replace(
                f"#{hashtag}",
                f"<a class='hyperlink' href='{hastag_url.format(tag=hashtag)}' target=\"_blank\">#{hashtag}</a>",
            )
        text_content = text_content.replace("\n", "<br>")
    elif type == "fa":
        text_content = text_content.replace(
            "//a.furaffinity.net/", config.url_base + "/cache_proxy/a.furaffinity.net/"
        )
        text_content = text_content.replace(
            'href="/user/', f'href="{config.url_base}/user/fa/'
        )
        text_content = text_content.replace("\n", "")
        while "</br>" * 3 in text_content:
            text_content = text_content.replace("</br>" * 3, "</br>")
        while "<br>" * 3 in text_content:
            text_content = text_content.replace("<br>" * 3, "<br>")
    return text_content


def list_and(list1, list2):
    # Convert both lists to sets for efficient intersection
    set1 = set(list1)
    set2 = set(list2)

    # Find the intersection of the two sets
    intersection = set1.intersection(set2)

    # Convert the intersection back to a list and return it
    return list(intersection)


def get_reddit_about(subreddit_name):
    url = f"https://www.reddit.com/r/{subreddit_name}/about.json"
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            return data.get("data", {})
        else:
            print(
                f"Failed to fetch subreddit info. Status code: {response.status_code}"
            )
            return {}
    except Exception as e:
        print(f"Error fetching subreddit info: {e}")
        return {}
