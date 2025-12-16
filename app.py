#!/usr/bin/python3

from flask import (
    Flask,
    render_template,
    render_template_string,
    send_file,
    redirect,
    request,
    send_from_directory,
    Response,
    jsonify,
)
import os, time
import re
import natsort
from urllib.parse import unquote, quote
import posixpath
from math import ceil, floor
from random import sample, randint
from threading import Thread
import argparse
import requests
import signal
import sys

import config, backend, utils, logger

parser = argparse.ArgumentParser(description="Check for command-line options.")

parser.add_argument(
    "--debug", action="store_true", help="Include this option to enable 'debug'."
)

parser.add_argument("--skip-scan", action="store_true", help="Skip startup scan.")
parser.add_argument("--update-daemon", action="store_true", help="Regularly update.")

args, unknown = parser.parse_known_args()
args.debug = bool(args.debug)
backend.debug_mode = args.debug
args.skip_scan = bool(args.skip_scan)

# temporarily enable debug mode
# args.debug = True

app = Flask(__name__)


def set_cache_header(response):
    if not args.debug:
        response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@app.after_request
def set_csp_header(response):
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:;"
    )
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.route(posixpath.join("/", config.url_base, "mt.webmanifest"))
def _webmanifest():
    with open("mt.webmanifest", "r") as f:
        return render_template_string(f.read(), url_base=config.url_base)


@app.route(posixpath.join("/", config.url_base, "js", "<fn>"))
def _js(fn):
    return set_cache_header(send_from_directory("js", fn))


@app.route(posixpath.join("/", config.url_base, "css", "<fn>"))
def _css(fn):
    print(posixpath.join("/", config.url_base, "css", fn))
    return set_cache_header(send_from_directory("css", fn))


@app.route(posixpath.join("/", config.url_base, "img", "<fn>"))
def _img(fn):
    return set_cache_header(send_from_directory("img", fn))


@app.route(posixpath.join("/", config.url_base, "avatar", "<type>", "<name>"))
def _avatar(type, name):
    # print(type, name)
    if not type or type == "None":
        return send_file("img/default_avatar.png", mimetype="image/jpeg")
    fn = f"{config.fs_bases[type]}/{name}/avatar"
    fn_bck = f"{config.fs_bases[type]}/{name}/avatar_bck"
    if not os.path.exists(fn):
        user = backend.User(name, type)
        user.load_from_db(db)
        avatar_url = user.avatar
        if (not user.avatar) or (not avatar_url.startswith("http")) or user.flagged:
            if user.flagged:
                logger.log(name, "is flagged, skip avatar downloading.")
            if os.path.exists(fn_bck):
                logger.log("copying", fn_bck, "to", fn)
                with open(fn_bck, "rb") as f:
                    with open(fn, "wb") as f2:
                        f2.write(f.read())
        else:
            logger.log("Downloading avatar:", avatar_url)
            r = requests.get(avatar_url, headers=utils.headers)
            if r.status_code == 200:
                with open(fn, "wb") as f:
                    f.write(r.content)
    # Check file size
    if not os.path.exists(fn) or os.path.getsize(fn) < 100:
        logger.log(f"Avatar file {fn} is too small or does not exist.")
        if os.path.exists(fn_bck):
            return set_cache_header(send_file(fn_bck, mimetype="image/jpeg"))
        else:
            if type == "reddit":
                return set_cache_header(
                    send_file("img/reddit.png", mimetype="image/jpeg")
                )
            else:
                return set_cache_header(
                    send_file("img/default_avatar.png", mimetype="image/jpeg")
                )
    return set_cache_header(send_file(fn, mimetype="image/jpeg"))


@app.route(posixpath.join("/", config.url_base, "banner", "<type>", "<name>"))
def _banner(type, name):
    # print(type, name)
    if not type or type == "None":
        return send_file("img/default_avatar.png", mimetype="image/jpeg")
    fn = f"{config.fs_bases[type]}/{name}/banner"
    fn_bck = f"{config.fs_bases[type]}/{name}/banner_bck"
    if not os.path.exists(fn):
        user = backend.User(name, type)
        user.load_from_db(db)
        banner_url = user.banner
        if (not user.banner) or (not banner_url.startswith("http")) or user.flagged:
            if user.flagged:
                logger.log(name, "is flagged, skip banner downloading.")
            if os.path.exists(fn_bck):
                logger.log("copying", fn_bck, "to", fn)
                with open(fn_bck, "rb") as f:
                    with open(fn, "wb") as f2:
                        f2.write(f.read())
        else:
            logger.log("Downloading banner:", banner_url)
            r = requests.get(banner_url, headers=utils.headers)
            if r.status_code == 200:
                with open(fn, "wb") as f:
                    f.write(r.content)
    # Check file size
    if not os.path.exists(fn) or os.path.getsize(fn) < 100:
        logger.log(f"Banner file {fn} is too small or does not exist.")
        if os.path.exists(fn_bck):
            return set_cache_header(send_file(fn_bck, mimetype="image/jpeg"))
        else:
            return set_cache_header(send_file("img/empty.png", mimetype="image/jpeg"))
    return set_cache_header(send_file(fn, mimetype="image/jpeg"))


@app.route(posixpath.join("/", config.url_base + "/"))
@app.route(posixpath.join("/", config.url_base))
def _index():
    return redirect(posixpath.join("/", config.url_base, "tl"))


@app.route(posixpath.join("/", config.url_base, "userlist"))
def _userlist():
    if "p" in request.args:
        page = int(request.args["p"]) - 1
        page = max(0, page)
    else:
        page = 0
    if "q" in request.args:
        query = request.args["q"]
    else:
        query = ""
    if query:
        all_users = [
            u
            for u in backend.all_users
            if query.lower() in u.nick.lower()
            or query.lower() in u.user_name.lower()
            or query.lower() in u.description.lower()
        ]
        users = all_users[
            page * config.items_per_page : (page + 1) * config.items_per_page
        ]
        max_page = ceil(len(all_users) / config.items_per_page)
    else:
        users = backend.all_users[
            page * config.items_per_page : (page + 1) * config.items_per_page
        ]
        max_page = ceil(len(backend.all_users) / config.items_per_page)
    seach_bar = render_template("searchbar.html", url_base=config.url_base)
    userlist = render_template("userlist.html", users=users, url_base=config.url_base)
    return render_template(
        "nav.html",
        current_page=page + 1,
        current_q=query,
        current_url=posixpath.join("/", config.url_base, "userlist"),
        max_page=max_page,
        content=seach_bar + userlist,
        section="users",
        url_base=config.url_base,
    )


tl_current_sort = "new"
tl_current_page = {
    "new": 0,
    "top": 0,
    "random": 0,
}


@app.route(posixpath.join("/", config.url_base, "tl"))
def _timeline():
    global tl_current_sort, tl_current_page
    if utils.busy_flag:
        return render_template("busy.html", url_base=config.url_base)
    if "q" in request.args:
        query = request.args["q"]
        query = query.strip()
    else:
        query = ""
    if "sort" in request.args:
        sort_ = request.args["sort"]
        tl_current_sort = sort_
    else:
        sort_ = tl_current_sort
    if "p" in request.args:
        page = int(request.args["p"]) - 1
        page = max(0, page)
        tl_current_page[tl_current_sort] = page
    else:
        if query:
            page = 0
        else:
            page = tl_current_page[tl_current_sort]
    if "tab" in request.args:
        tab = request.args["tab"]
    else:
        tab = "posts"
    if query:
        sorted_posts_id = db.query_post_by_text(query)
        all_post_count = len(sorted_posts_id)
        sorted_posts_id = sorted_posts_id[
            page * config.items_per_page : (page + 1) * config.items_per_page
        ]
        sorted_posts_id = [i[0] for i in sorted_posts_id]
    else:
        all_post_count = len(backend.cache_all_posts_id)
        if sort_ == "new":
            sorted_posts_id = backend.cache_all_posts_id[
                page * config.items_per_page : (page + 1) * config.items_per_page
            ]
        elif sort_ == "top":
            sorted_posts_id = backend.cache_all_posts_id_top[
                page * config.items_per_page : (page + 1) * config.items_per_page
            ]
        elif sort_ == "random":
            sorted_posts_id = backend.cache_all_posts_id_random[
                page * config.items_per_page : (page + 1) * config.items_per_page
            ]
        else:
            return "Invalid sort type."
        sorted_posts_id = [i[0] for i in sorted_posts_id]
    posts = dict()
    media_entries = dict()
    users = dict()
    for post_id in sorted_posts_id:
        if post_id in ("redgifs",):
            continue
        post = backend.Post(post_id, None, None)
        post.load_from_db(db)
        post.init_embed(db)
        posts[post_id] = post
        for row in db.query_rows(selected_db="media", key="post_id", value=post_id):
            media_id = row[0]
            media = backend.Media(media_id, post_id, None, post.type, post.time)
            media.load_from_db(db)
            if post_id not in media_entries:
                media_entries[post_id] = [media]
            else:
                media_entries[post_id].append(media)
        if post_id in media_entries:
            media_entries[post_id] = natsort.natsorted(
                media_entries[post_id], key=lambda x: x.media_id
            )
        if post.user_name not in users:
            user = backend.User(post.user_name)
            user.load_from_db(db)
            users[post.user_name] = user
    seach_bar = render_template("searchbar.html", url_base=config.url_base)
    timeline = render_template(
        "timeline.html",
        posts=posts,
        media_entries=media_entries,
        sorted_posts_id=sorted_posts_id,
        page=page,
        items_per_page=config.items_per_page,
        user_name="",
        type="tl",
        users=users,
        url_base=config.url_base,
        page_url=f"{config.url_base}/tl",
        show_sort_type=True,
        sort_type=sort_,
    )
    return render_template(
        "nav.html",
        content=seach_bar + timeline,
        current_page=page + 1,
        current_q=query,
        current_url=posixpath.join("/", config.url_base, "tl")
        + "?tab="
        + tab
        + "&sort="
        + sort_,
        max_page=ceil(all_post_count / config.items_per_page),
        section="tl",
        url_base=config.url_base,
    )


@app.route(posixpath.join("/", config.url_base, "fav"))
def _timeline_fav():
    if "p" in request.args:
        page = int(request.args["p"]) - 1
    else:
        page = 0
    if "tab" in request.args:
        tab = request.args["tab"]
    else:
        tab = "posts"
    try:
        sorted_posts_id = backend.get_fav(db)
        all_post_count = len(sorted_posts_id)
        sorted_posts_id = [i[0] for i in sorted_posts_id if i[0]][::-1]
        if tab == "posts":
            sorted_posts_id = sorted_posts_id[
                page * config.items_per_page : (page + 1) * config.items_per_page
            ]
        else:
            sorted_posts_id = sorted_posts_id[
                page
                * config.items_per_page
                * 2 : (page + 1)
                * config.items_per_page
                * 2
            ]
    except ValueError:
        return "Not enough posts. Download more and come back later."

    if tab == "posts":
        posts = dict()
        media_entries = dict()
        users = dict()
        exsiting_sorted_posts_id = []
        for post_id in sorted_posts_id:
            if post_id in ("redgifs",):
                continue
            post = backend.Post(post_id, None, None)
            if not post.load_from_db(db):
                print(f"Post [{post_id}] not found.")
                post.user_name = "None"
                post.text_content = (
                    f"This post is missing from file system. [{post_id}]"
                )
                post.fav = True
            post.init_embed(db)
            posts[post_id] = post
            exsiting_sorted_posts_id.append(post_id)
            for row in db.query_rows(selected_db="media", key="post_id", value=post_id):
                media_id = row[0]
                media = backend.Media(media_id, post_id, None, post.type, post.time)
                media.load_from_db(db)
                if post_id not in media_entries:
                    media_entries[post_id] = [media]
                else:
                    media_entries[post_id].append(media)
            if post_id in media_entries:
                media_entries[post_id] = natsort.natsorted(
                    media_entries[post_id], key=lambda x: x.media_id
                )
            if post.user_name not in users:
                user = backend.User(post.user_name, post.type)
                user.load_from_db(db)
                if post.user_name == "None":
                    user.nick = "None"
                users[post.user_name] = user
        timeline = render_template(
            "timeline.html",
            posts=posts,
            media_entries=media_entries,
            sorted_posts_id=exsiting_sorted_posts_id,
            page=page,
            items_per_page=config.items_per_page,
            user_name="",
            type="",
            users=users,
            url_base=config.url_base,
            page_url=f"{config.url_base}/fav",
            show_media_toggle=True,
        )
        max_page = ceil(all_post_count / config.items_per_page)
    elif tab == "media":
        media_entries = dict()
        users = dict()
        sorted_media_id = []
        for post_id in sorted_posts_id:
            if post_id in ("redgifs",):
                continue
            post = backend.Post(post_id, None, None)
            if not post.load_from_db(db):
                print(f"Post [{post_id}] not found.")
                continue
            for row in db.query_rows(selected_db="media", key="post_id", value=post_id):
                media_id = row[0]
                sorted_media_id.append(media_id)
                media = backend.Media(media_id, post_id, None, post.type, post.time)
                media.load_from_db(db)
                media_entries[media_id] = media
            if post.user_name not in users:
                user = backend.User(post.user_name, post.type)
                user.load_from_db(db)
                users[post.user_name] = user
        timeline = render_template(
            "mediagrid.html",
            media_entries=media_entries,
            sorted_media_id=sorted_media_id,
            page=page,
            items_per_page=config.items_per_page * 2,
            user_name="",
            type=type,
            users=users,
            url_base=config.url_base,
            page_url=f"{config.url_base}/fav",
        )
        max_page = ceil(all_post_count / config.items_per_page / 2)
    return render_template(
        "nav.html",
        current_page=page + 1,
        current_url=posixpath.join("/", config.url_base, "fav") + "?tab=" + tab,
        max_page=max_page,
        content=timeline,
        section="fav",
        url_base=config.url_base,
        adjust_padding_top=True,
    )


@app.route(posixpath.join("/", config.url_base, "user", "<type>", "<name>"))
def _timeline_user(type, name):
    if utils.busy_flag:
        return render_template("busy.html", url_base=config.url_base)
    if "p" in request.args:
        page = int(request.args["p"]) - 1
    else:
        page = 0
    if "tab" in request.args:
        tab = request.args["tab"]
    else:
        tab = "posts"

    user = backend.User(name, type)
    user.load_from_db(db)
    if not user.url:
        user.concat_url()

    uid = f"{name}@{type}"

    if tab == "posts":
        posts = dict()
        media_entries = dict()
        sorted_posts_id = []
        all_rows = db.query_rows(
            selected_db="posts", key="uid", value=uid, sort_key=lambda x: x[4]
        )
        for row in all_rows[
            page * config.items_per_page : (page + 1) * config.items_per_page
        ]:
            post_id = row[0]
            sorted_posts_id.append(post_id)
            post = backend.Post(post_id, name, type)
            post.load_from_db(db)
            post.init_embed(db)
            posts[post_id] = post
            for row in db.query_rows(selected_db="media", key="post_id", value=post_id):
                media_id = row[0]
                media = backend.Media(media_id, post_id, name, type, post.time)
                media.load_from_db(db)
                if post_id not in media_entries:
                    media_entries[post_id] = [media]
                else:
                    media_entries[post_id].append(media)
            if post_id in media_entries:
                media_entries[post_id] = natsort.natsorted(
                    media_entries[post_id], key=lambda x: x.media_id
                )
        timeline = render_template(
            "timeline.html",
            posts=posts,
            media_entries=media_entries,
            sorted_posts_id=sorted_posts_id,
            page=page,
            items_per_page=config.items_per_page,
            user_name=name,
            type=type,
            users={f"{name}": user},
            url_base=config.url_base,
            page_url=f"{config.url_base}/user/{type}/{name}",
            show_media_toggle=True,
        )
        max_page = ceil(len(all_rows) / config.items_per_page)
    elif tab == "media":
        media_entries = dict()
        all_rows = db.query_rows(
            selected_db="media", key="uid", value=uid, sort_key=lambda x: x[5]
        )
        sorted_media_id = []
        for row in all_rows[
            page * config.items_per_page * 2 : (page + 1) * config.items_per_page * 2
        ]:
            media_id = row[0]
            post_id = row[1]
            media = backend.Media(media_id, post_id, name, type, "")
            media.load_from_db(db)
            media_entries[media_id] = media
            sorted_media_id.append(media_id)

        timeline = render_template(
            "mediagrid.html",
            media_entries=media_entries,
            sorted_media_id=sorted_media_id,
            page=page,
            items_per_page=config.items_per_page * 2,
            user_name=name,
            type=type,
            users={f"{name}": user},
            url_base=config.url_base,
            page_url=f"{config.url_base}/user/{type}/{name}",
        )
        max_page = ceil(len(all_rows) / config.items_per_page / 2)
    userheader = render_template(
        "userheader.html",
        type=type,
        user=user,
        url_base=config.url_base,
        posts_cnt=len(all_rows),
    )
    return render_template(
        "nav.html",
        content=userheader + timeline,
        section="users",
        current_page=page + 1,
        current_url=posixpath.join("/", config.url_base, "user", type, name)
        + "?tab="
        + tab,
        max_page=max_page,
        alt_home_icon=posixpath.join("/", config.url_base, "avatar", type, name),
        title=f"{user.nick} (@{name}) - {type}",
        url_base=config.url_base,
        user=user,
        adjust_padding_top=True,
    )


@app.route(posixpath.join("/", config.url_base, "add_fav"))
def _add_fav():
    post_id = request.args["post_id"]
    if db.query_rows(
        selected_db="fav", key="post_id", value=post_id, ignore_cache=True
    ):
        print("remove favorite", post_id)
        backend.remove_favorite(db, post_id)
        return {
            "result": "removed",
        }
    else:
        print("add favorite", post_id)
        backend.add_favorite(db, post_id)
        return {
            "result": "added",
        }


@app.route(
    posixpath.join("/", config.url_base, "card", "<type>", "<name>", "<filename>")
)
def _card(type, name, filename):
    if type in ["x", "bsky", "reddit"]:
        media_id = filename.split(".")[0]
    elif type == "fa":
        media_id = filename
    else:
        return "Invalid type."
    media = backend.Media(media_id, None, name, type, "")
    media.load_from_db(db)
    user = backend.User(name, type)
    user.load_from_db(db)
    post = backend.Post(media.post_id, name, type)
    post.load_from_db(db)
    card = render_template(
        "card.html", media=media, user=user, post=post, url_base=config.url_base
    )
    return card


@app.route(
    posixpath.join("/", config.url_base, "ruffle", "<type>", "<name>", "<filename>")
)
def _ruffle(type, name, filename):
    return render_template(
        "ruffle.html",
        type=type,
        user_name=name,
        file_name=filename,
        url_base=config.url_base,
    )


@app.route(posixpath.join("/", config.url_base, "download"))
def _download():
    url = ""
    # print('-'*10,request.args)
    # print('+'*10,"url" in request.args)
    if "url" in request.args:
        url = request.args["url"]
    download = render_template(
        "download.html",
        default_input=url,
        msg=utils.current_url,
        queue="",
        url_base=config.url_base,
    )
    return render_template(
        "nav.html",
        content=download,
        current_page=0,
        current_url="",
        max_page=0,
        section="download",
        url_base=config.url_base,
    )


@app.route(posixpath.join("/", config.url_base, "file", "<type>", "<name>", "<fn>"))
def _file(type, name, fn):
    fn = unquote(fn)
    # print(f"Requesting file: {type}/{name}/{fn}")
    return set_cache_header(send_from_directory(config.fs_bases[type], f"{name}/{fn}"))


@app.route(posixpath.join("/", config.url_base, "thumb", "<type>", "<name>", "<fn>"))
def _thumb(type, name, fn):
    fn = unquote(fn)
    size = config.thubnail_size
    if "size" in request.args:
        size = int(request.args["size"])
        size = min(max(size, 32), 2500)
    path = f"{config.fs_bases[type]}/{name}/{fn}"
    thumbnail_path = utils.create_thumbnail(path, size)
    if not thumbnail_path or not os.path.exists(thumbnail_path):
        print(f"Thumbnail not found for {path}.")
        return set_cache_header(send_file("img/error.jpg", mimetype="image/jpeg"))
    return set_cache_header(send_file(thumbnail_path, mimetype="image/jpeg"))


@app.route(posixpath.join("/", config.url_base, "view", "<type>", "<name>", "<fn>"))
def _view(type, name, fn):
    return render_template(
        "viewer.html",
        type=type,
        user_name=name,
        file_name=fn,
        isvideo=fn.endswith(".mp4") or fn.endswith(".webm"),
        url_base=config.url_base,
    )


@app.route(posixpath.join("/", config.url_base, "add"), methods=["POST"])
def _add_download_job():
    data = request.get_json()
    # print("Received data:", data)
    if "url" in data and data["url"]:
        url = data["url"]
        full = data.get("full", False)
        media_only = data.get("media_only", False)
        if not (
            "bsky" in url
            or "x.com" in url
            or "twitter" in url
            or "reddit" in url
            or "furaffinity" in url
        ):
            msg = f"Invalid URL: {url}\n"
            print(msg)
            return jsonify(
                {"msg": msg, "current": utils.current_url, "queue": utils.download_jobs}
            )
        if "did:" in url:
            msg = f"Go get the actual bsky handle like 'xxx.bsky.social', {url} won't do.\n"
            print(msg)
            return jsonify(
                {"msg": msg, "current": utils.current_url, "queue": utils.download_jobs}
            )
        if re.match("\w+\.bsky\.social", url):
            url = "https://bsky.app/profile/" + utils.filter_ascii(url).strip()
        if not url.startswith("http"):
            url = f"https://{url}"
        url = url.replace("http://", "https://").strip("/")
        if url.endswith("/media"):
            url = url[:-6]
        if "twitter.com" in url:
            url = url.replace("twitter.com", "x.com")
        if "/photo/" in url:
            url = re.sub(r"photo/\d+", "", url)
        if "/video/" in url:
            url = re.sub(r"video/\d+", "", url)
        if not (url, full, media_only) in utils.download_jobs:
            # utils.download_jobs.append((url, full, media_only))
            utils.download_jobs.insert(0, (url, full, media_only))
            msg = f"Added {url} to download queue.\n"
        else:
            msg = f"{url} already in download queue.\n"
        print(msg)
    else:
        msg = "Enter your url above.\n"
    return jsonify(
        {"msg": msg, "current": utils.current_url, "queue": utils.download_jobs}
    )


@app.route(posixpath.join("/", config.url_base, "shorts"), methods=["GET"])
def _shorts():
    if utils.busy_flag:
        return render_template("busy.html", url_base=config.url_base)
    type = request.args.get("type", "")
    user_name = request.args.get("user", "")
    uid = f"{user_name}@{type}"
    query = request.args.get("q", "")
    if user_name:
        if not uid in backend.cache_user_media_id:
            print(f"cache miss for {user_name}, building cache...")
            # Need to find media for this user across all sources
            media_ids = db.raw_query(
                f"SELECT media_id, time, file_name FROM media WHERE uid = '{user_name}@{type}'"
            )
            media_ids = [
                (i[0], i[1])
                for i in media_ids
                if i[2].split(".")[-1] in backend.valid_video_types
            ]
            if len(media_ids) > 0:
                media_ids = sorted(media_ids, key=lambda s: s[1], reverse=True)
                media_ids = [i[0] for i in media_ids]
                backend.cache_user_media_id[uid] = media_ids
            total_cnt = len(media_ids)
        else:
            total_cnt = len(backend.cache_user_media_id[uid])
        return render_template(
            "shorts.html",
            url_base=config.url_base,
            type=type,
            user=user_name,
            query=query,
            total_cnt=total_cnt,
            current_cnt=0,
        )
    elif query:
        media_ids = db.query_media_by_text(query)
        total_cnt = len(media_ids)
        return render_template(
            "shorts.html",
            url_base=config.url_base,
            type=type,
            user=user_name,
            query=query,
            current_cnt=0,
            total_cnt=total_cnt,
        )
    else:
        user_name = ""
        idx = randint(0, len(backend.cache_all_media_id))
        return render_template(
            "shorts.html",
            url_base=config.url_base,
            type=type,
            current_cnt=idx,
            user=user_name,
            query=query,
            total_cnt=0,
        )


@app.route(posixpath.join("/", config.url_base, "get-a-vid"), methods=["GET"])
def _get_a_vid():
    user_name = request.args.get("user", "")
    type = request.args.get("type", "")
    uid = f"{user_name}@{type}"
    idx = int(request.args.get("idx", 0))
    query = request.args.get("q", "").strip()
    if user_name:
        # get a media_id from user by order
        if not uid in backend.cache_user_media_id:
            print(f"cache miss for {user_name}, building cache...")
            # Need to find the uid for this user - try to get type from most recent media
            media_rows = db.raw_query(
                f"SELECT media_id, time, file_name, type FROM media WHERE uid = '{uid}'"
            )
            if not media_rows:
                return {
                    "error": f"No media found for user {user_name}.",
                }
            media_ids = [
                (i[0], i[1])
                for i in media_rows
                if i[2].split(".")[-1] in backend.valid_video_types
            ]
            if len(media_ids) > 0:
                media_ids = sorted(media_ids, key=lambda s: s[1], reverse=True)
                media_ids = [i[0] for i in media_ids]
                backend.cache_user_media_id[uid] = media_ids
            if not media_ids:
                return {
                    "error": f"No video found for user {user_name}.",
                }
            backend.cache_user_media_id[uid] = media_ids
        else:
            print(f"cache hit for {uid}")
            media_ids = backend.cache_user_media_id[uid]
        idx = idx % len(media_ids)
        media_id = media_ids[idx]
    elif query:
        media_ids = db.query_media_by_text(query)
        idx = idx % len(media_ids)
        media_id = media_ids[idx]
    else:
        if len(backend.cache_all_media_id) == 0:
            return {
                "error": f"No video found.",
            }
        media_id = backend.cache_all_media_id[idx % len(backend.cache_all_media_id)]
    media = backend.Media(media_id, None, None, "", "")
    media.load_from_db(db)
    post = backend.Post(media.post_id, None, None)
    post.load_from_db(db)
    user = backend.User(post.user_name, post.type)
    user.load_from_db(db)
    data = {
        "url": posixpath.join(
            "/", config.url_base, "file", post.type, post.user_name, media.file_name
        ),
        "preview": posixpath.join(
            "/", config.url_base, "thumb", post.type, post.user_name, media.file_name
        ),
        "author": user.nick,
        "author_id": post.real_user if post.real_user else post.user_name,
        "avatar": f"{config.url_base}/avatar/{post.type}/{post.user_name}",
        "likes": post.likes,
        "comments": post.comments,
        "reposts": post.reposts,
        "description": post.text_content,
        "post_id": post.post_id,
        "media_id": media.media_id,
        "fav": bool(
            db.query_rows(selected_db="fav", key="post_id", value=post.post_id)
        ),
        "post_url": post.url,
        "user_url": f"{config.url_base}/user/{post.type}/{post.user_name}",
        "time": post.time,
        "type": post.type,
    }
    return jsonify(data)


@app.route(posixpath.join("/", config.url_base, "api", "favs"))
def _api_favs():
    favs = backend.get_fav(db)
    fav_files = []
    for fav in favs:
        post_id = fav[0]
        fav_time = fav[1]  # Wed Jun  4 19:01:32 2025
        # to timestamp
        fav_time = time.mktime(time.strptime(fav_time, "%a %b %d %H:%M:%S %Y"))
        post = backend.Post(post_id, None, None)
        if not post.load_from_db(db):
            print(f"Post [{post_id}] not found.")
            continue
        for row in db.query_rows(selected_db="media", key="post_id", value=post_id):
            media_id = row[0]
            media = backend.Media(media_id, post_id, None, post.type, post.time)
            media.load_from_db(db)
            fav_files.append(
                (
                    os.path.join(
                        config.fs_bases[post.type], post.user_name, media.file_name
                    ),
                    fav_time,
                )
            )
    return jsonify(fav_files)


@app.route(posixpath.join("/", config.url_base, "cache_proxy", "<path:subpath>"))
def cache_proxy(subpath):
    print(f"Proxying request for: {subpath}")
    subpath.lstrip("/")
    subpath = "https://" + subpath
    filename = subpath.split("/")[-1]
    cache_path = os.path.join("tmp/.cached", filename)
    if os.path.exists(cache_path):
        if args.debug:
            print(f"Serving from cache: {cache_path}")
        return set_cache_header(send_file(cache_path))
    else:
        os.makedirs("tmp/.cached", exist_ok=True)
        print(f"Fetching from remote: {subpath}")
        r = requests.get(subpath, headers=utils.headers)
        with open(cache_path, "wb") as f:
            f.write(r.content)
        print(f"Cached to: {cache_path}")
        return set_cache_header(send_file(cache_path))


def build_cache_all_posts_id_thread(db):
    while True:
        try:
            if utils.has_new_download:
                utils.busy_flag = True
                print("Building cache...")
                backend.build_cache(db)
                print("Cache built.")
                utils.has_new_download = False
                utils.busy_flag = False
            else:
                print("No new download, skipping cache build.")
        except Exception as e:
            print(e)
            utils.busy_flag = False
        # Sleep for a while to avoid busy looping
        time.sleep(30 * 60)


def init(db, skip_scan):
    backend.scan_for_users("x", db)
    backend.scan_for_users("bsky", db)
    backend.scan_for_users("reddit", db)
    backend.scan_for_users("fa", db)
    if not skip_scan:
        backend.scan_for_posts("x", db)
        backend.scan_for_media("x", db)
        backend.scan_for_posts("bsky", db)
        backend.scan_for_media("bsky", db)
        backend.scan_for_posts("reddit", db)
        backend.scan_for_media("reddit", db)
        # Do not scan FA for now due to compatibility issues
        # backend.scan_for_posts("fa", db)
        # backend.scan_for_media("fa", db)
    db.commit()
    print("Scan finished.")

    Thread(target=build_cache_all_posts_id_thread, args=(db,), daemon=True).start()
    print("Cache building thread started.")

    if args.update_daemon:
        print("Starting update daemon...")
        Thread(target=utils.update_daemon, daemon=True).start()


def shutdown_cleanup():
    utils.global_running_flag = False
    db.conn.close()


def signal_handler(signal, frame):
    shutdown_cleanup()
    sys.exit(0)


def wsgi_app(skip_scan=False):
    init(db, skip_scan)
    return app


db = backend.Database("data.db", "fav.db")
db.prepare_db()
utils.global_running_flag = True
worker = utils.DownloadWorker(db)
worker.setDaemon(True)
worker.start()
print("Download worker started.")
print("Ready.")

if __name__ == "__main__":
    init(db, args.skip_scan)
    print(f"app is ready at: http://{config.host}:{config.port}/{config.url_base}")
    app.run(host=config.host, port=config.port, debug=args.debug)
    shutdown_cleanup()
else:
    signal.signal(signal.SIGTERM, signal_handler)  # Handle SIGTERM
