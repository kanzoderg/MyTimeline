import json
import os, sys
from hashlib import md5

host = None
port = None
url_base = None
cookies_list = None
auth_files = None
fs_bases = None
cache_path = None
thumbnail_size = None
custom_gallery_dl_location = None
items_per_page = None
proxy = None
no_auth = None
user_pin = None
admin_pin = None
super_session_key = None
log_file = None
user_pin_hased = None
admin_pin_hased = None
allow_external_url_preview = False

config_read = False

current_python = sys.executable
if not os.path.exists(current_python):
    print(
        f"Warning: current python executable {current_python} does not exist, how??? Using fallback 'python' command, hope it works..."
    )
    current_python = "python"
print("Using python interpreter:", current_python)

kemono_proxy = "kemono.cr"
coomer_proxy = "coomer.st"

def read_config(filename="config.json"):
    global host, port, url_base, cookies_list, auth_files, fs_bases, cache_path, thumbnail_size, custom_gallery_dl_location, items_per_page, proxy, no_auth, user_pin, admin_pin, super_session_key, log_file, user_pin_hased, admin_pin_hased, config_read, current_python, allow_external_url_preview
    if os.path.exists(filename):
        with open(filename, "r") as f:
            _config_data = json.load(f)
    else:
        _config_data = {}

    host = _config_data.get("host", "0.0.0.0")
    port = _config_data.get("port", 8088)

    url_base = _config_data.get("url_base", "")
    url_base = url_base.strip("/")
    if url_base:
        url_base = "/" + url_base

    cookies_list = _config_data.get("cookies_list", {"x": "", "bsky": ""})
    if not cookies_list.get("x"):
        print(
            "Warning: No cookies file specified for x.com, some features may not work properly"
        )
    auth_files = _config_data.get("auth_files", {"x": "", "bsky": "./bsky_auth.json"})

    fs_bases = _config_data.get(
        "fs_bases",
        {
            "x": "./downloads/twitter",
            "bsky": "./downloads/bluesky",
            "reddit": "./downloads/reddit",
            "fa": "./downloads/furaffinity",
            "patreon": "./downloads/patreon",
        },
    )
    cache_path = _config_data.get("cache_path", "~/.cache/mt")

    fs_bases["x"] = os.path.expanduser(fs_bases.get("x", "./downloads/twitter"))
    fs_bases["bsky"] = os.path.expanduser(fs_bases.get("bsky", "./downloads/bluesky"))
    fs_bases["reddit"] = os.path.expanduser(
        fs_bases.get("reddit", "./downloads/reddit")
    )
    fs_bases["fa"] = os.path.expanduser(fs_bases.get("fa", "./downloads/furaffinity"))
    fs_bases["patreon"] = os.path.expanduser(
        fs_bases.get("patreon", "./downloads/patreon")
    )
    cache_path = os.path.expanduser(cache_path)

    # create base directories if not exist
    for base_path in fs_bases.values():
        if not os.path.exists(base_path):
            os.makedirs(base_path)
    os.makedirs(cache_path, exist_ok=True)
    os.makedirs("tmp", exist_ok=True)

    thumbnail_size = _config_data.get("thumbnail_size", 600)

    custom_gallery_dl_location = _config_data.get("custom_gallery_dl_location", "")
    if not custom_gallery_dl_location:
        py_exec_path = os.path.dirname(current_python)
        if py_exec_path and os.path.exists(os.path.join(py_exec_path, "gallery-dl")):
            custom_gallery_dl_location = os.path.join(py_exec_path, "gallery-dl")
    print("Using gallery-dl location:", custom_gallery_dl_location)
    if not os.path.exists(custom_gallery_dl_location):
        print(
            "gallery-dl not found! Please install gallery-dl in your python environment or specify the path to gallery-dl executable in json. You can install gallery-dl with pip: pip install gallery-dl, until then, MT will not be able to download from any sources!"
        )

    items_per_page = _config_data.get("items_per_page", 30)
    proxy = _config_data.get("proxy", "http://127.0.0.1:10808")

    no_auth = _config_data.get("no_auth", False)

    user_pin = _config_data.get("user_pin", "")
    admin_pin = _config_data.get("admin_pin", "")
    if user_pin and not admin_pin:
        print(
            "Warning: user_pin is set but admin_pin is not set, using user_pin as admin_pin."
        )
        admin_pin = user_pin
    if not (user_pin or admin_pin):
        print(
            "Warning: No user_pin or admin_pin set, authentication will be effectively disabled."
        )
        no_auth = True
    user_pin_hased = md5(user_pin.encode()).hexdigest() if user_pin else ""
    admin_pin_hased = md5(admin_pin.encode()).hexdigest() if admin_pin else ""

    super_session_key = _config_data.get("super_session_key", "")

    log_file = _config_data.get("log_file", "./log.txt")
    allow_external_url_preview = bool(_config_data.get("allow_external_url_preview", False))
    config_read = True
