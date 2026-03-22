from PIL import Image
import time, os, re, sys, json
import traceback
import subprocess
import signal, psutil
from hashlib import md5
from threading import Thread, Lock
import requests
from random import randint
from uuid import uuid4
from bs4 import BeautifulSoup

from flask import request


def time_it(func):
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        logger.log(f"Function {func.__name__} took {end_time - start_time:.4f} seconds")
        return result

    return wrapper


import config

if not config.config_read:
    print("Unit test: config not read, reading now...")
    config.read_config()

import backend, logger
from run_command import run_command

global_lock = Lock()
global_running_flag = True
download_jobs = []
current_url = ""
has_new_download = True

busy_flag = False
restart_needed = False

global_headers = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
}


def copy_ua_from_request():
    global global_headers
    if request and request.headers.get("User-Agent"):
        global_headers["User-Agent"] = request.headers.get("User-Agent")
        logger.log(
            f"Copied User-Agent from request: {global_headers['User-Agent']}",
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

TYPE_DOWNLOAD = 0
TYPE_RESCAN = 1


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
    elif extension in ["txt", "md", "html", "htm", "pdf", "doc", "docx"]:
        return TEXT
    else:
        return UNKNOWN


allowed_domain = [
    "furaffinity.net",
    "youtube.com",
    "youtu.be",
    "ytimg.com",
    "fanbox.cc",
    "pixiv",
    "itch.",
]


def check_link_allowed(url):
    for domain in allowed_domain:
        if domain in url:
            return True
    return False


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
        if time.time() - auth_pool[session_key][0] > 24 * 3600:
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


def get_mem_usage_mb():
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()
    return mem_info.rss / (1024 * 1024)  # Convert bytes to MB


def get_stats():
    data = {
        "memory_usage_mb": get_mem_usage_mb(),
        "download_queue_length": len(download_jobs),
        "restart_needed": restart_needed,
        "busy": busy_flag,
        "post_count": backend.db.get_post_count(),
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
    thumbnail_path = md5(path.encode()).hexdigest() + f"_{thumbnail_size}.jpg"
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
    def __init__(self, db):
        super().__init__()
        self.db = db

    def run(self):
        global download_jobs, global_lock, global_running_flag, current_url, has_new_download, busy_flag
        while global_running_flag:
            try:
                with global_lock:
                    if len(download_jobs) > 0:
                        current_url, full, media_only, job_type = download_jobs.pop(0)
                        logger.log("-->", current_url, full, media_only)
                        logger.log(f"Downloading {current_url}")
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
                        logger.log("Invalid bsky URL:", current_url)
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
                        current_url,
                        "-D",
                        user_fs_path,
                    ]
                    cmd = [str(x) for x in cmd]
                    type = "bsky"
                elif "x.com" in current_url or "twitter.com" in current_url:
                    name = re.search(
                        r"x.com/([a-zA-Z0-9\-\_\.]+)", current_url
                    ) or re.search(r"twitter.com/([a-zA-Z0-9\-\_\.]+)", current_url)
                    if not name:
                        logger.log("Invalid x.com URL:", current_url)
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
                            current_url,
                            "-D",
                            user_fs_path,
                        ]
                        cmd = [str(x) for x in cmd]
                    else:
                        cmd += [
                            "-c",
                            "gdl_conf/gallery-dl-config.json",
                            current_url,
                            "-D",
                            user_fs_path,
                        ]
                        cmd = [str(x) for x in cmd]
                    type = "x"
                elif "reddit.com" in current_url:
                    name = re.search(r"reddit.com/r/([a-zA-Z0-9\-\_\.]+)", current_url)
                    if not name:
                        if "reddit.com/user/" in current_url:
                            name = "reddit_users"
                        else:
                            logger.log("Invalid reddit URL:", current_url)
                            continue
                    else:
                        name = name.group(1).lower()
                    user_fs_path = f"{config.fs_bases['reddit']}/{name}/"
                    cmd += [
                        "-c",
                        "gdl_conf/gallery-dl-config.json",
                        current_url,
                        "-D",
                        user_fs_path,
                    ]
                    type = "reddit"
                elif "furaffinity" in current_url:
                    name = re.search(
                        r"furaffinity.net/(user|gallery|scraps|journals)/([\w\d_\-\.\~]+)",
                        current_url,
                    )
                    user_fs_path = os.path.expanduser(config.fs_bases["fa"])
                    if not name:
                        name = "ignore"
                    else:
                        name = name.group(2).lower()
                    cmd = [
                        config.current_python,
                        "./fadl/fadl.py",
                        "-o",
                        user_fs_path,
                        "--user-agent",
                        f"\"{global_headers['User-Agent']}\"",
                        current_url,
                    ]
                    type = "fa"
                else:
                    logger.log("Unsupported URL:", current_url)
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
                        ],
                    )
                else:
                    logger.log("This is a rescan job, not performing download.")
                try:
                    if name == "ignore":
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
                            name = "ignore"
                    busy_flag = True
                    backend.scan_for_users(type, name)
                    if job_type == TYPE_DOWNLOAD:
                        backend.scan_for_posts(type, name)
                    else:
                        backend.scan_for_posts(type, name, True)
                    self.db.commit()
                    has_new_download = True
                    backend.query_cache = dict()
                    logger.log(name, "downloaded")
                    busy_flag = False
                except Exception as e:
                    busy_flag = False
                    logger.log(traceback.format_exc(), type="error")
                    logger.log("Scan Failed.", type="error")
                current_url = ""
            except Exception as e:
                logger.log(
                    "Error in download worker:", traceback.format_exc(), type="error"
                )
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
            download_jobs.append((url, False, True, TYPE_DOWNLOAD))
            logger.log(f"[update daemon] Added {url} to queue.")
            time.sleep(10)
    except Exception as e:
        logger.log("[update daemon]", traceback.format_exc(), type="error")
        time.sleep(10)


def render_markdown(text_content):
    # simple markdown rendering
    text_content = (
        text_content.replace("\n", "<br>")
        .replace("http://", "")
        .replace("https://", "")
    )
    # simple markdown link parsing [text](url)
    text_content = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        r'<a class="hyperlink url" href="https://\2" target="_blank">\1</a>',
        text_content,
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
    return text_content


cache_embeded_link = {}


def embed_hyperlink(type, text_content_in):
    global cache_embeded_link
    display_link = ""
    if len(cache_embeded_link) > 1000:
        cache_embeded_link = {}
    if not text_content_in:
        return "", None
    if text_content_in in cache_embeded_link:
        return cache_embeded_link[text_content_in]
    if type in ["x", "bsky"]:
        text_content = (
            text_content_in.replace("http://", "")
            .replace("https://", "")
            .replace("＃", "#")
            .replace("＠", "@")
        )
        tokens = re.split(
            r"([:;<>,，。→【】\[\]'\"!\s\n\(\)]|[^\x00-\x7F]+)", text_content
        )
        tokens = [token for token in tokens if token]
        # logger.log(tokens)
        for i, token in enumerate(tokens):
            try:
                if (
                    token == "#"
                    and i + 1 < len(tokens)
                    and tokens[i + 1]
                    and tokens[i + 1] != " "
                ):
                    token = "#" + tokens[i + 1]
                    tokens[i + 1] = ""
                if (
                    token == "@"
                    and i + 1 < len(tokens)
                    and tokens[i + 1]
                    and tokens[i + 1] != " "
                ):
                    next_token = tokens[i + 1]
                    at_content = re.match(r"([a-zA-Z0-9\-\_\.]+)", next_token)
                    if at_content:
                        token = "@" + at_content.group(1)
                        tokens[i + 1] = next_token[len(at_content.group(1)) :]

                if token == "\n":
                    tokens[i] = "<br>"
                elif token.startswith("@") and len(token) > 1:
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
                    tokens[i] = tokens[i].rsplit(".")
                    tokens[i] = (
                        f'<a class="hyperlink hashtag" onclick="show_loading_icon()" href="{config.url_base}/tl?q={token[1:]}">{token}</a>'
                    )
                elif token.endswith(".bsky.social") and len(token) > 11:
                    token = token.split("/")[-1]
                    tokens[i] = (
                        f'<a class="hyperlink iconusername" href="{config.url_base}/user/bsky/{token}">@{token}</a>'
                    )
                elif token.startswith("www.furaffinity.net/user/") or token.startswith(
                    "furaffinity.net/user/"
                ):
                    umatch = token.strip("/").split("/")[-1]
                    tokens[i] = (
                        f'<a class="hyperlink iconusername" href="{config.url_base}/user/fa/{umatch}">~{umatch}</a>'
                    )
                elif "." in token:
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
                            token = uname_match.group(1)
                            tokens[i] = (
                                f'<a class="hyperlink iconusername" href="{config.url_base}/user/x/{token}">@{token}</a>'
                            )
                            continue
                        if check_link_allowed(token):
                            display_link = token
                        url_shorten = token
                        if len(token) > 30:
                            url_shorten = token[:15] + "..." + token[-10:]
                        tokens[i] = (
                            f'<a class="hyperlink url" href="https://{token}" target="_blank">{url_shorten}</a>'
                        )
            except Exception as e:
                logger.log(
                    "Error embedding hyperlink:", traceback.format_exc(), type="error"
                )
        text_content = "".join(tokens)
    elif type == "reddit":
        text_content = render_markdown(text_content_in)
    elif type == "fa":
        text_content = text_content_in.replace(
            "//a.furaffinity.net/", config.url_base + "/cache_proxy/a.furaffinity.net/"
        )
        text_content = text_content.replace(
            "https://d.furaffinity.net/",
            config.url_base + "/cache_proxy/d.furaffinity.net/",
        )
        text_content = text_content.replace(
            'href="/user/', f'href="{config.url_base}/user/fa/'
        )
        text_content = text_content.replace(
            "https://twitter.com/", config.url_base + "/user/x/"
        )
        text_content = text_content.replace(
            "https://x.com/", config.url_base + "/user/x/"
        )
        text_content = text_content.replace("\n", "")
        while text_content.endswith("</br>"):
            text_content = text_content[:-5]
        while "</br>" * 7 in text_content:
            text_content = text_content.replace("</br>" * 7, "")

    cache_embeded_link[text_content_in] = (text_content, display_link)
    return text_content, display_link


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
        description = soup.find("meta", {"name": "description"})
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
