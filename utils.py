from PIL import Image
import time, os, re, sys, json, math, shutil
import traceback
import subprocess
import signal, psutil
from hashlib import md5
from threading import Thread, Lock
import requests
from random import randint
from uuid import uuid4
from bs4 import BeautifulSoup
from html import escape

from flask import request


def md5_hash(text):
    return md5(text.encode()).hexdigest()


def time_it(func):
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        logger.log(f"Function {func.__name__} took {end_time - start_time:.4f} seconds")
        return result

    return wrapper


def identify_site(url):
    if "x.com" in url or "twitter.com" in url:
        return "x"
    elif "bsky.app" in url:
        return "bsky"
    elif "furaffinity.net" in url:
        return "fa"
    elif "patreon.com" in url:
        return "patreon"
    elif ("kemono." in url) or ("coomer." in url):
        return "patreon"
    elif "e621.net" in url:
        return "e621"
    elif "reddit.com" in url:
        return "reddit"
    else:
        return "maintenance"


def id2time_gueeser(id_, type_):
    try:
        if type_ == "x":
            # snowflake id time guessing
            timestamp = (int(id_) >> 22) + 1288834974657
            return timestamp / 1000
        elif type_ == "e621":
            # e621 id time guessing
            # magic number got by linear regression
            ts = 56.55 * int(id_) + 1417571635
            return ts
        else:
            return int(time.time())
    except Exception as e:
        logger.log("Error in id2time_gueeser:", traceback.format_exc(), type="error")
        return int(time.time())


def strip_suffix(text, suffix):
    if not suffix:
        return text
    while text.endswith(suffix):
        text = text[: -len(suffix)]
    return text


def remove_duplicate_with_order(l: list):
    seen = set()
    result = []
    for item in l:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def strip_suffix_list(text, suffixes):
    for suffix in suffixes:
        text = strip_suffix(text, suffix)
    return text


import config

if not config.config_read:
    print("Unit test: config not read, reading now...")
    config.read_config()

import backend, logger, database
from run_command import run_command

global_lock = Lock()
scan_lock = Lock()
global_running_flag = True
download_jobs = []
jobs_queue = {
    "x": [],
    "bsky": [],
    "reddit": [],
    "fa": [],
    "patreon": [],
    "e621": [],
    "maintenance": [],
}
current_jobs = dict()


def get_full_download_queue():
    full_queue = []
    for site in jobs_queue:
        full_queue.extend(jobs_queue[site])
    return full_queue


def get_current_jobs_list():
    return list(current_jobs.values())


running_workers = set()
has_new_download = True

busy_flag = False
restart_needed = False

global_headers = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
}

if os.path.exists("tmp/ua.txt"):
    with open("tmp/ua.txt", "r") as f:
        ua = f.read().strip()
        if ua:
            global_headers["User-Agent"] = ua
            logger.log(f"Loaded User-Agent from file: {global_headers['User-Agent']}")


def copy_ua_from_request():
    global global_headers
    if request and request.headers.get("User-Agent"):
        global_headers["User-Agent"] = request.headers.get("User-Agent")
        with open("tmp/ua.txt", "w") as f:
            f.write(global_headers["User-Agent"])
        logger.log(
            f"I'm stealing User-Agent from user: {global_headers['User-Agent']}",
            type="warning",
            verbose=1,
        )


sort_keys = {
    "new": lambda x: x[4],
    "top": lambda x: x[7],
    "random": lambda x: randint(0, 1 << 30),
    "e0": lambda x: x[0],
    "e1": lambda x: x[1],
}

UNKNOWN = 0
VIDEO = 1
IMAGE = 2
AUDIO = 3
FLASH = 4
TEXT = 5
ARCHIVE = 6
PROJECT_SOURCE = 7
PLAIN_TEXT = 8

TYPE_DOWNLOAD = 0
TYPE_RESCAN = 1
TYPE_REBUILD = 2

STARTING = 0
RUNNING = 1
SCANNING = 2

current_status = STARTING


def media_type_from_extension(extension):
    extension = extension.split(".")[-1].lower()
    if extension in ["mp4", "mov", "avi", "mkv", "webm", "m4v"]:
        return VIDEO
    elif extension in ["jpg", "jpeg", "png", "gif", "bmp", "tiff", "webp"]:
        return IMAGE
    elif extension in ["mp3", "wav", "flac", "aac"]:
        return AUDIO
    elif extension in ["swf", "flv"]:
        return FLASH
    elif extension in ["html", "htm", "pdf", "doc", "docx"]:
        return TEXT
    elif extension in ["txt", "md"]:
        return PLAIN_TEXT
    elif extension in ["zip", "rar", "7z", "tar", "bz2", "001", "002", "003"]:
        return ARCHIVE
    elif extension in ["psd", "blend", "clip"]:
        return PROJECT_SOURCE
    else:
        return UNKNOWN


exclude_files = [
    "thumbs.db",
    ".ds_store",
    "user.json",
    "about.json",
    "user.json.gz",
    "about.json.gz",
]

allowed_to_proxy = [
    "furaffinity.net/",
    "ytimg.com",
    "youtube.com",
    "youtu.be",
    "fanbox.cc",
    "pixiv",
    "itch.",
    "e621.net/",
    "storage.googleapis.com/",
    "googleusercontent.com/",
    # "patreon.com",
]

allowed_to_probe = [
    "youtu.be",
    "youtube.com",
    "fanbox.cc",
    "pixiv",
    "itch.io",
    "querie.me/answer",
    "forms.gle",
    # "patreon.com",
]

allowed_to_embed = [
    re.compile(r"furaffinity\.net/view/"),
    re.compile(r"furaffinity\.net/journal/"),
    re.compile(r"at\://"),
    re.compile(r"x\.com/"),
    re.compile(r"twitter\.com/[a-zA-Z0-9\-\_\.]+/status/"),
    re.compile(r"bsky\.app/profile/[a-zA-Z0-9\-\_\.\:]+/post/[a-zA-Z0-9]+"),
]


def check_allowed_to_proxy(url):
    for link in allowed_to_proxy:
        if link in url:
            return True
    return False


def check_allowed_to_probe(url):
    for link in allowed_to_probe:
        if link in url:
            return True
    return False


def check_allowed_to_embed(url):
    for link in allowed_to_embed:
        if isinstance(link, re.Pattern):
            if link.search(url):
                return True
        elif link in url:
            return True
    return False


def extract_domain(url):
    try:
        domain = url.replace("http://", "").replace("https://", "").split("/")[0]
        return domain
    except Exception as e:
        logger.log(
            "Error extracting domain from URL:", traceback.format_exc(), type="error"
        )
        return ""


ROLE_UNAUTHORIZED = 0
ROLE_AUTHORIZED = 1
ROLE_ADMIN = 2

auth_pool = {}

search_term_excludes = set(["mode:full", "-", ",", ".", "?", "+", ":"])


def get_role(session_key, super_secret_key=None):
    if super_secret_key and config.super_session_key == super_secret_key:
        return ROLE_ADMIN
    if session_key in auth_pool:
        # Check if session is expired
        if time.time() - auth_pool[session_key][0] > 7 * 24 * 3600:
            del auth_pool[session_key]
            return ROLE_UNAUTHORIZED
        return auth_pool[session_key][1]
    return ROLE_UNAUTHORIZED


def uuid():
    return str(uuid4())


def get_proxy_dict():
    if config.proxy:
        return {"http": config.proxy, "https": config.proxy}
    return None


def select_cookies_for_url(url):
    cookies_txt = None
    cookies = {}
    if "x.com" in url or "twitter.com" in url:
        cookies_txt = config.cookies_list.get("x", None)
    elif "bsky.app" in url:
        cookies_txt = config.cookies_list.get("bsky", None)
    elif "furaffinity.net" in url:
        cookies_txt = config.cookies_list.get("fa", None) or "fadl/cookies.txt"
    logger.log(
        f"Selecting cookies for URL: {url}, using cookies file: {cookies_txt}",
        verbose=1,
    )
    if cookies_txt and os.path.exists(cookies_txt):
        with open(cookies_txt, "r", encoding="utf-8") as f:
            cookies_txt = f.read()
            for line in cookies_txt.splitlines():
                if line.startswith("#") or not line.strip():
                    continue
                parts = line.strip().split("\t")
                if len(parts) >= 7:
                    name = parts[5]
                    value = parts[6]
                    cookies[name] = value
    logger.log(f"Selected cookies: {cookies}", verbose=1)
    return cookies


def get(url, headers=None):
    logger.log(f"GET -> {url}", type="attention")
    comblined_headers = global_headers.copy()
    if headers:
        comblined_headers.update(headers)
    # select cookies
    cookies = select_cookies_for_url(url)
    for _ in range(3):
        try:
            return requests.get(
                url,
                headers=comblined_headers,
                proxies=get_proxy_dict(),
                timeout=10,
                cookies=cookies,
            )
        except Exception as e:
            logger.log(
                f"Error in GET request, retrying... Attempt {_+1}", type="warning"
            )
            time.sleep(1)
    raise Exception(f"Failed to GET {url} after 3 attempts")


def post(url, json=None, headers=None):
    logger.log(f"POST -> {url}", type="attention")
    comblined_headers = global_headers.copy()
    if headers:
        comblined_headers.update(headers)
    # select cookies
    cookies = select_cookies_for_url(url)
    for _ in range(3):
        try:
            return requests.post(
                url,
                json=json,
                headers=comblined_headers,
                proxies=get_proxy_dict(),
                timeout=10,
                cookies=cookies,
            )
        except Exception as e:
            logger.log(
                f"Error in POST request, retrying... Attempt {_+1}", type="warning"
            )
            time.sleep(1)
    raise Exception(f"Failed to POST {url} after 3 attempts")


def format_tags(tags):
    if not tags:
        return ""
    formatted = " ".join([f"#{tag}" for tag in tags])
    return formatted


def get_mem_usage_mb():
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()
    return mem_info.rss / (1024 * 1024)  # Convert bytes to MB


def get_stats():
    data = {
        "memory_usage_mb": get_mem_usage_mb(),
        "restart_needed": restart_needed,
        "busy": busy_flag,
        "post_count": database.db.get_post_count(),
    }
    return data


def create_image_thumbnail(image_path, thumbnail_path, thumbnail_size):
    image = Image.open(image_path)
    image.thumbnail((thumbnail_size, thumbnail_size))
    image.convert("RGB").save(thumbnail_path)


def create_video_thumbnail(video_path, thumbnail_path):
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        f'"{video_path}"',
        "-ss",
        "00:00:00.000",
        "-vframes",
        "1",
        thumbnail_path,
    ]
    cmd = [str(x) for x in cmd]
    os.system(" ".join(cmd))
    # open thumbnail file, check if its all black/ near black
    try:
        image = Image.open(thumbnail_path)
        extrema = image.getextrema()
        if all([e[0] == 0 and e[1] < 10 for e in extrema]):
            logger.log("Video thumbnail is all black, trying again at 0.1 second")
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                f'"{video_path}"',
                "-ss",
                "00:00:00.100",
                "-vframes",
                "1",
                thumbnail_path,
            ]
            cmd = [str(x) for x in cmd]
            os.system(" ".join(cmd))
    except Exception as e:
        logger.log(
            "Error checking video thumbnail:", traceback.format_exc(), type="error"
        )


def filter_ascii(text):
    if not text:
        return ""
    return "".join(c for c in text if ord(c) < 128)


def create_thumbnail(path, thumbnail_size=config.thumbnail_size):
    config.cache_path = os.path.expanduser(config.cache_path)
    if not os.path.exists(config.cache_path):
        os.makedirs(config.cache_path)
    thumbnail_path = md5_hash(path) + f"_{thumbnail_size}.jpg"
    thumbnail_path = f"{thumbnail_path[0:2]}/{thumbnail_path[2:4]}/{thumbnail_path}"
    thumbnail_path = os.path.join(config.cache_path, thumbnail_path)
    if os.path.exists(thumbnail_path):
        # logger.log("Thumbnail exists:", thumbnail_path)
        return thumbnail_path
    if not os.path.exists(os.path.dirname(thumbnail_path)):
        os.makedirs(os.path.dirname(thumbnail_path))
    logger.log("Creating thumbnail:", thumbnail_path, verbose=1)
    if media_type_from_extension(path) == VIDEO:
        create_video_thumbnail(path, thumbnail_path)
    elif media_type_from_extension(path) == IMAGE:
        create_image_thumbnail(path, thumbnail_path, thumbnail_size)
    else:
        logger.log("Unsupported file type for thumbnail:", path)
        logger.log("Still trying to create thumbnail with video method.")
        create_video_thumbnail(path, thumbnail_path)
    return thumbnail_path


class DownloadWorker(Thread):
    def __init__(self, db, perferred_site="maintenance"):
        super().__init__()
        self.db = db
        self.perferred_site = perferred_site

    def run(self):
        global download_jobs, jobs_queue, global_lock, global_running_flag, has_new_download, busy_flag
        while global_running_flag:
            try:
                time.sleep(1)
                url = ""
                job_id = str(uuid())
                with global_lock:
                    ### Test code, don't forget to remove
                    if len(jobs_queue.get(self.perferred_site, [])) > 0:
                        url, full, media_only, job_type = jobs_queue[
                            self.perferred_site
                        ].pop(0)
                        current_jobs[job_id] = {
                            "url": url,
                            "full": full,
                            "media_only": media_only,
                            "job_type": job_type,
                        }
                        logger.log(
                            f"Processing {url} from {self.perferred_site} queue."
                        )
                    else:
                        # logger.log(f"No jobs in {self.perferred_site} queue.")
                        continue

                if config.custom_gallery_dl_location:
                    cmd = [os.path.expanduser(config.custom_gallery_dl_location)]
                else:
                    cmd = ["gallery-dl"]
                if (job_type == TYPE_RESCAN or job_type == TYPE_REBUILD) and re.match(
                    r"[a-zA-Z0-9_.\[\]\(\)-]+@[a-zA-Z]+", url
                ):
                    name, type = url.split("@")
                    cmd = []
                    user_fs_path = f"{config.fs_bases[type]}/{name}/"
                    if job_type == TYPE_REBUILD:
                        # for rebuild we need to remove user from database to trigger full rescan and metadata rebuild
                        database.db.remove_user(f"{name}@{type}")
                    job_type = TYPE_RESCAN
                elif "bsky" in url:
                    # cookies not avalible yet
                    name = re.search(r"profile/([a-zA-Z0-9\-\_\.]+)", url)
                    if not name:
                        logger.log("Invalid bsky URL:", url)
                        continue
                    name = name.group(1).lower()
                    user_fs_path = f"{config.fs_bases['bsky']}/{name}/"
                    cmd += [
                        "-c",
                        (
                            "gdl_conf/gallery-dl-config-media-only.json"
                            if media_only
                            else "gdl_conf/gallery-dl-config.json"
                        ),
                        url,
                        "-D",
                        user_fs_path,
                    ]
                    cmd = [str(x) for x in cmd]
                    type = "bsky"
                elif "x.com" in url or "twitter.com" in url:
                    name = re.search(r"x.com/([a-zA-Z0-9\-\_\.]+)", url) or re.search(
                        r"twitter.com/([a-zA-Z0-9\-\_\.]+)", url
                    )
                    if not name:
                        logger.log("Invalid x.com URL:", url)
                        del current_jobs[job_id]
                        continue
                    name = name.group(1).lower()
                    user_fs_path = f"{config.fs_bases['x']}/{name}/"
                    if config.cookies_list["x"]:
                        cmd += [
                            "-c",
                            (
                                "gdl_conf/gallery-dl-config-media-only.json"
                                if media_only
                                else "gdl_conf/gallery-dl-config.json"
                            ),
                            "-C",
                            config.cookies_list["x"],
                            url,
                            "-D",
                            user_fs_path,
                        ]
                        cmd = [str(x) for x in cmd]
                    else:
                        cmd += [
                            "-c",
                            "gdl_conf/gallery-dl-config.json",
                            url,
                            "-D",
                            user_fs_path,
                        ]
                        cmd = [str(x) for x in cmd]
                    type = "x"
                elif "reddit.com" in url:
                    name = re.search(r"reddit.com/r/([a-zA-Z0-9\-\_\.]+)", url)
                    if not name:
                        if "reddit.com/user/" in url:
                            name = "reddit_users"
                        else:
                            logger.log("Invalid reddit URL:", url)
                            del current_jobs[job_id]
                            continue
                    else:
                        name = name.group(1).lower()
                    user_fs_path = f"{config.fs_bases['reddit']}/{name}/"
                    cmd += [
                        "-c",
                        "gdl_conf/gallery-dl-config.json",
                        url,
                        "-D",
                        user_fs_path,
                    ]
                    type = "reddit"
                elif "furaffinity" in url:
                    name = re.search(
                        r"furaffinity.net/(user|gallery|scraps|journals)/([\w\d_\-\.\~]+)",
                        url,
                    )
                    user_fs_path = os.path.expanduser(config.fs_bases["fa"])
                    if not name:
                        name = "TBD"
                    else:
                        name = name.group(2).lower()
                    cmd = [
                        config.current_python,
                        "./fadl/fadl.py",
                        "-o",
                        user_fs_path,
                        "--user-agent",
                        f"\"{global_headers['User-Agent']}\"",
                        url,
                    ]
                    if full:
                        cmd += ["-f"]
                    type = "fa"
                elif "patreon.com" in url:
                    url = url.strip("/")
                    name = url.split("/")[-1].lower()
                    user_fs_path = os.path.expanduser(
                        f"{config.fs_bases['patreon']}/{name}/"
                    )
                    cmd = ["echo not supported yet"]
                    job_type = TYPE_RESCAN
                    type = "patreon"
                elif ("kemono." in url) or ("coomer." in url):
                    url = url.split("?")[0].strip("/")
                    if "kemono." in url:
                        name = "TBD"
                    elif "coomer." in url:
                        name = re.search(r"user/([\w\d_\-\.]+)", url)
                        if not name:
                            name = "TBD"
                        else:
                            name = name.group(1).lower()
                    user_fs_path = os.path.expanduser(f"{config.fs_bases['patreon']}")
                    cmd = [
                        config.current_python,
                        "kemonodl/kemonodl.py",
                        "-o",
                        user_fs_path,
                        url,
                        "--no-interactive",
                    ]
                    type = "patreon"
                elif "e621.net" in url:
                    name = "TBD"
                    user_fs_path = os.path.expanduser(config.fs_bases["e621"])
                    cmd = [
                        config.current_python,
                        "e6dl/e6dl.py",
                        f"'{url}'",
                        "-o",
                        f"'{user_fs_path}'",
                        "--no-interactive",
                    ]
                    type = "e621"
                else:
                    logger.log("Unsupported URL:", url)
                    del current_jobs[job_id]
                    continue
                if config.proxy:
                    cmd += ["--proxy", config.proxy]

                if not os.path.exists(user_fs_path) and not full:
                    logger.log(
                        "User directory does not exist, doing full download:",
                        user_fs_path,
                    )
                    full = True
                else:
                    logger.log(
                        "User directory exists, last modified:",
                        time.ctime(os.path.getmtime(user_fs_path)),
                        user_fs_path,
                    )

                logger.log(
                    "User:",
                    name,
                    "Type:",
                    type,
                    "Full:",
                    full,
                    "Media Only:",
                    media_only,
                    "Proxy:",
                    config.proxy,
                )

                def trigger_action():
                    backend.flag_user(name, type)

                if job_type == TYPE_DOWNLOAD:
                    backend.unflag_user(name, type)
                    run_command(
                        cmd,
                        ["#"] if not full else [],
                        triggers=[
                            ("NotFoundError", trigger_action),
                            ("AuthorizationError", trigger_action),
                            ("AccountTakedown", trigger_action),
                        ],
                        tag=f"{type}:{name}",
                    )
                else:
                    logger.log("This is a rescan job, not performing download.")
                try:
                    if name == "TBD":
                        logger.log("Guessing username now...")
                        existing_users = os.listdir(user_fs_path)
                        existing_users.sort(
                            key=lambda x: os.path.getmtime(
                                os.path.join(user_fs_path, x)
                            ),
                            reverse=True,
                        )
                        if existing_users:
                            name = existing_users[0]
                            logger.log("Using most recently updated user:", name)
                        else:
                            name = "TBD"
                    busy_flag = True
                    if type in ["x", "bsky", "reddit", "fa", "patreon", "e621"]:
                        backend.scan_for_users(type, name)
                        if job_type == TYPE_DOWNLOAD:
                            backend.scan_for_posts(type, name)
                        else:
                            backend.scan_for_posts(type, name, True)
                    else:
                        # custom scan
                        backend.scan_custom_user(scan_posts=True, force=True)
                    self.db.commit()
                    has_new_download = True
                    backend.query_cache = dict()
                    logger.log(name, "downloaded")
                    busy_flag = False
                except Exception as e:
                    busy_flag = False
                    logger.log(traceback.format_exc(), type="error")
                    logger.log("Scan Failed.", type="error")
                url = ""
            except Exception as e:
                logger.log(
                    "Error in download worker:", traceback.format_exc(), type="error"
                )
                time.sleep(1)
            finally:
                if job_id in current_jobs:
                    del current_jobs[job_id]
                if url:
                    logger.log(f"Finished processing {url}.")


def update_daemon():
    global download_jobs, global_running_flag, has_new_download, busy_flag
    # try:
    #     users_to_watch = [u for u in backend.all_users if not u.flagged][::-1]
    #     for user in users_to_watch:
    #         if user.type == "x":
    #             url = f"https://x.com/{user.user_name}"
    #         elif user.type == "bsky":
    #             url = f"https://bsky.app/profile/{user.user_name}"
    #         else:
    #             continue
    #         download_jobs.append((url, False, True, TYPE_DOWNLOAD))
    #         logger.log(f"[update daemon] Added {url} to queue.")
    #         time.sleep(10)
    # except Exception as e:
    #     logger.log("[update daemon]", traceback.format_exc(), type="error")
    #     time.sleep(10)
    logger.log("[update daemon] is deprecated.", type="warning")


def render_markdown(text_content):
    links = []
    # simple markdown rendering
    text_content = (
        text_content.replace("\n", "<br>")
        .replace("http://", "")
        .replace("https://", "")
        .replace("\[", "[")
        .replace("\]", "]")
    )
    # simple markdown link parsing [text](url)
    for match in re.finditer(r"\[([^\]]+)\]\(([^\)]+)\)", text_content):
        link_text = match.group(1)
        link_url = match.group(2)
        links.append(link_url)
        text_content = text_content.replace(
            match.group(0),
            f'<a class="hyperlink url" href="https://{link_url}" target="_blank">{link_text}</a>',
        )

    text_content = re.sub(r"\-{3,}\<br\>", "<hr>", text_content)
    text_content = text_content.replace("xcancel.com/", "x.com/").replace(
        "twitter.com/", "x.com/"
    )
    text_content = re.sub(
        r"# .+?\<br\>", lambda m: f"<h1>{m.group(0)[2:-4].strip()}</h1>", text_content
    )
    text_content = re.sub(
        r"## .+?\<br\>", lambda m: f"<h2>{m.group(0)[3:-4].strip()}</h2>", text_content
    )
    text_content = re.sub(
        r"### .+?\<br\>", lambda m: f"<h3>{m.group(0)[4:-4].strip()}</h3>", text_content
    )

    text_content = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text_content)
    text_content = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text_content)
    return text_content, links


def render_bbcode(text_content):
    # simple bbcode rendering
    text_content = text_content.replace("[b]", "<b>").replace("[/b]", "</b>")
    text_content = text_content.replace("[i]", "<i>").replace("[/i]", "</i>")
    text_content = text_content.replace("[u]", "<u>").replace("[/u]", "</u>")
    text_content = text_content.replace("[h1]", "<h1>").replace("[/h1]", "</h1>")
    text_content = text_content.replace("[h2]", "<h2>").replace("[/h2]", "</h2>")
    text_content = text_content.replace("[h3]", "<h3>").replace("[/h3]", "</h3>")
    text_content = text_content.replace("[hr]", "<hr>")
    text_content = text_content.replace("[sub]", "<sub>").replace("[/sub]", "</sub>")
    text_content = text_content.replace(
        "[center]", '<div style="text-align:center">'
    ).replace("[/center]", "</div>")

    text_content = re.sub(
        r"\[color\=#?([0-9a-fA-F]{3,8})\]", r'<span style="color:#\1">', text_content
    )
    text_content = text_content.replace("[/color]", "</span>")

    return text_content


cache_embeded_link = {}


def shorten_url(url, max_length=30):
    if len(url) <= max_length:
        return url
    return url[:27] + "..."


def tokenize_text(text):
    tokens = re.split(r"([\s\'\"\!\,\:\(\)：]+)", text)
    tokens = [token for token in tokens if token]
    return tokens


def embed_hyperlink(type, text_content_in, post_id=""):
    global cache_embeded_link
    links = []
    if len(cache_embeded_link) > 1000:
        cache_embeded_link = {}
    if not text_content_in:
        return "", []
    if (text_content_in, post_id) in cache_embeded_link:
        return cache_embeded_link[(text_content_in, post_id)]
    if type in ["x", "bsky"]:
        text_content = (
            text_content_in.replace("http://", "")
            .replace("https://", "")
            .replace("＃", "#")
            .replace("＠", "@")
        )
        # text_content = escape(text_content)
        tokens = tokenize_text(text_content)
        # logger.log(tokens)
        for i, token in enumerate(tokens):
            try:
                # if token == "#" and i + 1 < len(tokens):
                #     next_token = tokens[i + 1]
                #     if re.match(r"([^\#\s]+)", next_token):
                #         token = "#" + next_token
                #         tokens[i + 1] = ""
                # if token == "@" and i + 1 < len(tokens):
                #     next_token = tokens[i + 1]
                #     at_content = re.match(r"([a-zA-Z0-9\-\_\.]+)", next_token)
                #     if at_content:
                #         token = "@" + at_content.group(1)
                #         tokens[i + 1] = next_token[len(at_content.group(1)) :]

                if token == "\n":
                    tokens[i] = "<br>"
                elif token.startswith("@") and len(token) > 1:
                    # user mentions
                    # print("Original token:", token)
                    token = token.rstrip(".")
                    token = token.replace("/", "").replace("\\", "")
                    if "." in token:
                        type = "bsky"
                    else:
                        type = "x"
                    tokens[i] = (
                        f'<a class="hyperlink iconusername" href="{config.url_base}/user/{type}/{token[1:]}">{token}</a>'
                    )
                elif token.startswith("#") and len(token) > 2:
                    if len(token) > 30:
                        tokens[i] = token
                        continue
                    if token[1:].isnumeric():
                        tokens[i] = token
                        continue
                    # hashtags
                    tokens[i] = tokens[i].rsplit(".")
                    tokens[i] = (
                        f'<a class="hyperlink hashtag" onclick="show_loading_icon()" href="{config.url_base}/tl?q={token[1:]}">{token}</a>'
                    )
                elif token.endswith(".bsky.social") and len(token) > 11:
                    # bsky profile links
                    token = token.split("/")[-1]
                    tokens[i] = (
                        f'<a class="hyperlink iconusername" href="{config.url_base}/user/bsky/{token}">@{token}</a>'
                    )
                elif token.startswith("www.furaffinity.net/user/") or token.startswith(
                    "furaffinity.net/user/"
                ):
                    # fa profile links
                    umatch = token.strip("/").split("/")[-1]
                    tokens[i] = (
                        f'<a class="hyperlink iconusername" href="{config.url_base}/user/fa/{umatch}">~{umatch}</a>'
                    )
                elif "." in token:
                    # other links
                    if token[0] == "." or token[-1] == ".":
                        continue
                    if ".." in token:
                        continue
                    parts = [p for p in token.split(".") if p]
                    if len(parts) < 2:
                        continue
                    if (
                        len(parts[-1]) < 2
                    ):  # check if last part is valid domain extension
                        continue
                    if re.match(
                        r"\d+?", parts[-1]
                    ):  # check if last part is not all numbers
                        continue
                    if "@" in token and not "/" in token:
                        # email address
                        tokens[i] = (
                            f'<a class="hyperlink email" href="mailto:{token}" target="_blank">{token}</a>'
                        )
                    else:
                        if token.startswith("/"):
                            continue
                        if media_type_from_extension(token) != UNKNOWN:
                            continue
                        uname_match = re.match(
                            r"twitter.com/([a-zA-Z0-9\-\_\.]+)", token
                        ) or re.match(r"x.com/([a-zA-Z0-9\-\_\.]+)", token)
                        if uname_match:
                            # Handle case where token is a full tweet URL
                            if "/status/" in token:
                                token = token.split("?")[0]
                                token = f"x.com/{uname_match.group(1)}/status/{token.split('/')[3]}"
                                logger.log(
                                    "Embedded tweet link:",
                                    tokens[i],
                                    type="attention",
                                    verbose=3,
                                )
                            else:
                                token = uname_match.group(1)
                                tokens[i] = (
                                    f'<a class="hyperlink iconusername" href="{config.url_base}/user/x/{token}">@{token}</a>'
                                )
                                continue
                        if check_allowed_to_embed(token) or check_allowed_to_probe(
                            token
                        ):
                            links.append(token)
                        url_shorten = shorten_url(token)
                        tokens[i] = (
                            f'<a class="hyperlink url" href="https://{token}" target="_blank">{url_shorten}</a>'
                        )
            except Exception as e:
                logger.log(
                    "Error embedding hyperlink:", traceback.format_exc(), type="error"
                )
        text_content = "".join(tokens)
    elif type == "reddit":
        # fa urls
        fa_url_match = re.search(
            r"furaffinity\.net/(view|journal)/\d+", text_content_in
        )
        if fa_url_match:
            links.append(fa_url_match.group(0))
        # bsky urls
        bsky_url_match = re.search(
            r"bsky\.app/profile/[a-zA-Z0-9\-\_\.\:]+/post/[a-zA-Z0-9]+", text_content_in
        )
        if bsky_url_match:
            links.append(bsky_url_match.group(0))
        # x urls
        x_url_match = re.search(
            r"x\.com/[a-zA-Z0-9\-\_\.]+/status/[\d]+", text_content_in
        )
        if x_url_match:
            links.append(x_url_match.group(0))
        text_content, _links = render_markdown(text_content_in)
        links += _links
    elif (
        type in ["fa", "patreon"] or True
    ):  # for now, try to auto embed links in all types
        text_content = text_content_in.replace("\n", "")
        soup = BeautifulSoup(text_content, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            href = href.replace("http://", "").replace("https://", "")
            if href.startswith("/user/"):
                umatch = href.strip("/").split("/")[-1]
                a["href"] = f"{config.url_base}/user/fa/{umatch}"
            elif (
                ("twitter.com/" in href)
                or ("/x.com/" in href or href.startswith("x.com/"))
                or ("bsky.app/profile/" in href)
            ):
                uname_match = (
                    re.search(r"twitter.com/([a-zA-Z0-9\-\_\.]+)", href)
                    or re.search(r"x.com/([a-zA-Z0-9\-\_\.]+)", href)
                    or re.search(r"bsky.app/profile/([a-zA-Z0-9\-\_\.\:]+)", href)
                )
                if uname_match:
                    href = href.split("?")[0]
                    href = re.sub(r"/(photo|video)/\d+", "", href)
                    token = uname_match.group(1)
                    if "bsky.app/profile/" in href:
                        a["href"] = f"{config.url_base}/user/bsky/{token}"
                    else:
                        a["href"] = f"{config.url_base}/user/x/{token}"
                    # a.string = "@" + token
                    a["class"] = ["hyperlink", "iconusername"]
                    a["target"] = "_self"
            else:
                a["target"] = "_blank"
                a["class"] = ["auto_link", "external"]
            if check_allowed_to_embed(href) or check_allowed_to_probe(href):
                links.append(href)
        for img in soup.find_all("img", src=True):
            src = img["src"]
            src = src.replace("http://", "").replace("https://", "").lstrip("/")
            if re.match(r"[a-z]\.furaffinity\.net/", src):
                img["src"] = config.url_base + "/cache_proxy/" + src
            elif type == "patreon":
                img["src"] = ""
            elif type == "e621":
                img["src"] = config.url_base + "/cache_proxy/https://e621.net/" + src
        for youtubeWrapper in soup.find_all(class_="youtubeWrapper"):
            youtubeIframe = youtubeWrapper.find("iframe")
            if youtubeIframe and youtubeIframe.has_attr("src"):
                src = youtubeIframe["src"]
                youtubeID = re.search(r"/embed/([a-zA-Z0-9\-\_]+)", src)
                if youtubeID:
                    youtubeID = youtubeID.group(1)
                    youtube_url = f"https://www.youtube.com/watch?v={youtubeID}"
                    links.append(youtube_url)
            youtubeWrapper.attrs["style"] = "display:none;"
        text_content = str(soup)
        text_content = strip_suffix_list(
            text_content, ["<br>", "\n", "<br/>", "<br />", "</br>", "</h"]
        )
    else:  # not used for now, placeholder for futher custom parsing if needed
        text_content = text_content_in.replace("\n", "<br>")

    cache_embeded_link[(text_content_in, post_id)] = (text_content, links)
    return text_content, links


def list_and(list1, list2):
    # Convert both lists to sets for efficient intersection
    set1 = set(list1)
    set2 = set(list2)

    # Find the intersection of the two sets
    intersection = set1.intersection(set2)

    # Convert the intersection back to a list and return it
    return list(intersection)


def get_reddit_about(subreddit_name):
    if os.path.exists(
        os.path.join(config.fs_bases["reddit"], subreddit_name, "about.json")
    ):
        with open(
            os.path.join(config.fs_bases["reddit"], subreddit_name, "about.json"), "r"
        ) as f:
            try:
                data = json.load(f)
                return data.get("data", {})
            except Exception as e:
                logger.log(
                    f"Error loading cached subreddit info: {traceback.format_exc()}",
                    type="warning",
                )
    logger.log(f"Fetching subreddit info for: {subreddit_name}", type="attention")
    url = f"https://www.reddit.com/r/{subreddit_name}/about.json"
    try:
        response = get(url)
        if response.status_code == 200:
            data = response.json()
            with open(
                os.path.join(config.fs_bases["reddit"], subreddit_name, "about.json"),
                "w",
                encoding="utf-8",
            ) as f:
                json.dump(data, f)
            return data.get("data", {})
        else:
            logger.log(
                f"Failed to fetch subreddit info. Status code: {response.status_code}"
            )
            return {}
    except Exception as e:
        logger.log(
            f"Error fetching subreddit info: {traceback.format_exc()}", type="error"
        )
        return {}


def probe_url(url):
    if not url.startswith("http"):
        url = f"https://{url.lstrip('')}"
    title = ""
    description = ""
    thumbnail = ""
    logger.log("Probing", url, type="attention")
    try:
        html = get(url).text
        soup = BeautifulSoup(html)
        title = soup.title
        if title:
            title = title.text
        else:
            title = url
        description = soup.find("meta", {"property": "og:description"}) or soup.find(
            "meta", {"name": "description"}
        )
        if description:
            description = description.attrs.get("content", "")
        else:
            description = ""
        thumbnail = soup.find("meta", {"name": "twitter:image"}) or soup.find(
            "meta", {"property": "og:image"}
        )

        if thumbnail:
            thumbnail = thumbnail.attrs.get("content", "")
        else:
            thumbnail = ""
        logger.log("Probed", url, title, description, thumbnail, type="attention")
    except:
        logger.log(traceback.format_exc(), type="error")
    return title, description, thumbnail


def probe_video_duration(video_path):
    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            f'"{video_path}"',
        ]
        cmd = [str(x) for x in cmd]
        result = subprocess.check_output(" ".join(cmd), shell=True)
        print("ffprobe result:", result)
        duration = float(result.strip())
        return duration
    except Exception as e:
        logger.log(
            f"Error probing video duration: {traceback.format_exc()}", type="error"
        )
        return 0


search_suggestions_cache = {}


def build_search_suggestions():
    global search_suggestions_cache
    logger.log("Building search suggestions cache...")
    search_suggestions_cache = {}
    cursor = database.db.get_cursor()
    cursor.execute("SELECT DISTINCT user_name FROM users")
    for row in cursor.fetchall():
        user_name = row[0].lower().strip()
        if len(user_name) < 3:
            continue
        idx = user_name[:2]
        search_suggestions_cache.setdefault(idx, set()).add(user_name)
    cursor.execute("SELECT tags FROM posts")
    for row in cursor:
        tags = row[0].lower().strip()
        tags = tags.split(" ") if tags else []
        for tag in tags:
            if len(tag) < 3:
                continue
            idx = tag[:2]
            search_suggestions_cache.setdefault(idx, set()).add(tag)
    logger.log(
        "Built search suggestions cache with",
        sum(len(v) for v in search_suggestions_cache.values()),
        "entries.",
    )

def get_search_suggestions(query, limit=20):
    global search_suggestions_cache
    if len(query) < 2:
        return []
    idx = query[:2].lower()
    suggestions = search_suggestions_cache.get(idx, set())
    filtered = [s for s in suggestions if query in s]
    return sorted(filtered)[:limit]

def format_duration(seconds):
    if not seconds:
        return "00:00"
    try:
        seconds = int(seconds)
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes:02d}:{secs:02d}"
    except Exception as e:
        logger.log(f"Error formatting duration: {traceback.format_exc()}", type="error")
        return "00:00"


def format_size(size_bytes):
    if size_bytes == 0:
        return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    if size_bytes < 0:
        return "Invalid size"
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    return f"{round(size_bytes / p, 2)} {size_name[i]}"


def merge_folder_contents(src_folder, dst_folder):
    if not os.path.exists(src_folder):
        logger.log(f"Source folder does not exist: {src_folder}", type="error")
        return False
    if not os.path.exists(dst_folder):
        os.makedirs(dst_folder)
    for item in os.listdir(src_folder):
        src_item = os.path.join(src_folder, item)
        dst_item = os.path.join(dst_folder, item)
        if os.path.exists(dst_item):
            if os.path.isdir(src_item) and os.path.isdir(dst_item):
                merge_folder_contents(src_item, dst_item)
            else:
                logger.log(
                    f"{src_item} already exists as {dst_item}, removing.",
                    type="warning",
                )
                os.remove(src_item)
        else:
            shutil.move(src_item, dst_item)
    # After merging, remove the source folder if it's empty
    if os.path.exists(src_folder) and not os.listdir(src_folder):
        os.rmdir(src_folder)
    else:
        logger.log(
            f"Source folder not empty after merge, check for issues: {src_folder}",
            type="warning",
        )
    return True


def rename_user_and_fs_folder(old_uid, new_uid):
    old_name = old_uid.split("@")[0]
    old_type = old_uid.split("@")[1]
    new_name = new_uid.split("@")[0]
    new_type = new_uid.split("@")[1]

    if old_type != new_type:
        logger.log("Cannot rename user, type mismatch", type="error")
        return False
    old_fs_path = os.path.join(config.fs_bases[old_type], old_name)
    new_fs_path = os.path.join(config.fs_bases[new_type], new_name)
    if os.path.exists(new_fs_path):
        merge_folder_contents(old_fs_path, new_fs_path)
    else:
        shutil.move(old_fs_path, new_fs_path)
    database.db.rename_user(old_uid, new_uid)
    database.db.clear_cache()
    backend.all_users = backend.get_users()
    logger.log(
        f"Renamed user {old_uid} to {new_uid} and moved folder from {old_fs_path} to {new_fs_path}",
        type="attention",
    )
    return True
