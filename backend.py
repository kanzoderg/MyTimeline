import sqlite3
import os, json, re, time, sys, gzip
from datetime import datetime, timezone
import natsort, random
from urllib.parse import unquote, quote
import threading
import traceback

import config

if not config.config_read:
    print("Unit test: config not read, reading now...")
    config.read_config()

import utils, logger, database

debug_mode = False


class ExternalLink:
    def __init__(self, url):
        self.url = url
        self.clean_url = url.split("?")[0].split("#")[0]
        self.domain = utils.extract_domain(self.clean_url)
        self.favicon = f"https://{self.domain}/favicon.ico"
        self.title = "Link Title"
        self.description = "External Link"
        self.thumbnail = ""

    def probe(self):
        rows = database.db.query_rows(
            "external_link_lookup", "url", self.url, ignore_cache=True
        )
        if len(rows) > 0:
            self.title, self.description, self.thumbnail = rows[0][1:]
        elif utils.current_status != utils.RUNNING:
            logger.log(
                f"Current status is {utils.current_status}, skipping probing external URL {self.url} to avoid blocking the main thread during startup.",
                verbose=2,
            )
            self.title = "External Link"
            self.description = "URL preview will be available after scan is completed."
            self.thumbnail = ""
        elif config.allow_external_url_preview:
            self.title, self.description, self.thumbnail = utils.probe_url(self.url)
            database.db.insert_or_update_url(
                self.url, self.title, self.description, self.thumbnail
            )
            database.db.commit()
        else:
            logger.log(
                f"Probing external URL is disabled, cannot probe {self.url}",
                type="warning",
            )
            self.title = "External Link"
            self.description = "URL preview is disabled in config."
            self.thumbnail = ""


class Post:
    def __init__(self, post_id, user_name, type):
        self.post_id = post_id
        if not "@" in post_id:
            if type:
                self.post_id = f"{post_id}@{type}"
            else:
                raise ValueError(
                    f"post_id must contain type suffix, e.g. '12345@x' user: {user_name}, post_id: {post_id}"
                )
        self.post_id_inner = post_id.split("@")[0]
        self.user_name = user_name
        self.user = None
        if type:
            self.type = type
        else:
            if "@" in post_id:
                self.type = post_id.split("@")[1]
            else:
                raise ValueError(
                    f"Type must be provided if post_id does not contain type suffix, e.g. '12345@x' user: {user_name}, post_id: {post_id}"
                )
        self.uid = f"{user_name}@{type}" if user_name and type else None
        self.likes = 0
        self.reposts = 0
        self.comments = 0
        self.nick = ""
        self.fav = False
        self.embed = ""
        self.isreply = False
        self.reply_to = ""
        self.reply_to_id = ""
        self.reply_root = ""
        self.reply_root_id = ""
        self.text_content = ""
        self.text_content_rendered = ""
        self.url = ""
        self.time = 0
        self.piority = 0
        if type == "reddit":
            self.real_user = "[deleted]"
        else:
            self.real_user = ""
        self.alt = ""
        self.alts = []
        self.tags = []
        self.loaded = False
        self.json_error = False
        self.external_link = None
        self.external_links = []
        self.has_attachment = False
        self.medias = []
        self.attachments = []
        self.extra_data = dict()
        self.tags = []
        self.isplaceholder = False

    def load_from_db(self, ignore_cache=False):
        rows = database.db.query_rows(
            "posts", "post_id", self.post_id, ignore_cache=ignore_cache
        )
        if len(rows) == 0:
            self.isplaceholder = True
            self.concat_url()
            self.loaded = False
            return False
        self.isplaceholder = False
        row = rows[0]
        self.uid = row[2]
        # Extract user_name and type from uid if not already set
        if self.uid and "@" in self.uid:
            parts = self.uid.rsplit("@", 1)
            self.user_name = parts[0]
            self.type = parts[1]
        self.user = User(self.user_name, self.type)
        self.user.load_from_db()
        self.nick = row[3]
        self.time = row[4]
        self.type = row[5]
        self.url = row[6]
        self.likes = row[7]
        self.reposts = row[8]
        self.comments = row[9]
        self.embed = row[10]
        self.embeds = []
        self.embed_objs = []
        self.isreply = bool(row[11])
        self.reply_to = row[11]
        self.reply_to_id = (
            f"{self.reply_to.split('@')[0]}@{self.type}" if "@" in self.reply_to else ""
        )
        self.reply_root = row[12]
        self.reply_root_id = (
            f"{self.reply_root.split('@')[0]}@{self.type}"
            if "@" in self.reply_root
            else ""
        )
        self.real_user = row[13]
        self.alt = row[14]
        self.extra_data = json.loads(row[15]) if row[15] else dict()
        self.tags = row[16].split(" ") if row[16] else []
        if self.alt:
            self.alts = self.alt.split("<sep>")
        self.text_content = row[1]
        self.text_content_rendered, links = utils.embed_hyperlink(
            self.type, self.text_content, self.post_id
        )
        links.append(self.embed)
        print(f"Extra links for post {self.post_id}: {self.extra_data.get('links', [])}")
        # for extra_link in self.extra_data.get("links", []):
        #     if utils.check_allowed_to_embed(extra_link):
        #         links.append(extra_link)
        #         logger.log(f"Embedding link: {extra_link}", type="attention", verbose=0)
        #     else:
        #         logger.log(
        #             f"Link {extra_link} is not allowed to be embedded.",
        #             type="warning",
        #             verbose=0,
        #         )
        links += self.extra_data.get("links", [])
        links = list(set([link for link in links if link]))
        # logger.log(links, type="warning", verbose=0)
        for link in links:
            # logger.log(f"Processing link: {link} for post {self.post_id}", verbose=0)
            if utils.check_allowed_to_embed(link):
                self.embeds.append(link)
                logger.log(
                    f"Added embed link: {link} for post {self.post_id}",
                    verbose=2,
                )
            elif (
                utils.check_allowed_to_probe(link) and config.allow_external_url_preview
            ):
                external_link = ExternalLink(link)
                external_link.probe()
                self.external_links.append(external_link)
        self.embeds += self.extra_data.get("embeds", [])
        self.embeds = list(set(self.embeds))
        self.embeds.sort()
        # check if post is in fav, ignore cache to always get latest fav status
        rows = database.db.query_rows("fav", "post_id", self.post_id, ignore_cache=True)
        if len(rows) > 0:
            self.fav = True
        self.loaded = True
        return True

    def save_to_db(self):
        if self.loaded and self.json_error:
            logger.log(
                "Failed to load json for",
                self.uid,
                self.url,
                ", but since its successfully loaded before, dummy info won't be wrote.",
                type="warning",
            )
            return
        database.db.insert_or_update_post(
            self.post_id,
            self.text_content,
            self.uid,
            self.nick,
            self.time,
            self.type,
            self.url,
            self.likes,
            self.reposts,
            self.comments,
            self.embed,
            self.reply_to,
            self.reply_root,
            self.real_user,
            self.alt,
            self.extra_data,
            " ".join(self.tags),
        )

    def load_from_json(self, json):
        try:
            if self.type == "x":
                self.post_id_inner = str(json["tweet_id"])
                self.post_id = f"{self.post_id_inner}@x"
                self.text_content = json["content"]
                if not self.user_name:
                    self.user_name = json.get("author", {})["name"].lower()
                self.nick = json.get("author", {})["nick"]
                self.time = json["date"]  # eg. 2026-02-07 04:09:32
                dt = datetime.strptime(self.time, "%Y-%m-%d %H:%M:%S")
                dt = dt.replace(tzinfo=timezone.utc)
                self.time = int(dt.timestamp())
                self.url = f"https://x.com/{self.user_name}/status/{self.post_id_inner}"
                self.likes = json["favorite_count"]
                self.reposts = json["retweet_count"]
                self.comments = json["reply_count"]
                self.isreply = "reply_to" in json
                reply_id = json.get("reply_id", "")
                reply_to_user = json.get("reply_to", "")
                if reply_id and reply_to_user:
                    self.reply_to = f"{reply_id}@{reply_to_user.lower()}"
                reply_root = json.get("conversation_id", "")
                if reply_root and reply_root != self.post_id_inner:
                    self.reply_root = f"{reply_root}@i"
            elif self.type == "bsky":
                self.post_id_inner = str(json["post_id"])
                self.post_id = f"{self.post_id_inner}@bsky"
                self.text_content = json["text"]
                if "facets" in json:
                    self.text_content = bsky_link_fix(self.text_content, json["facets"])
                if not self.user_name:
                    self.user_name = json.get("author", {})["handle"].lower()
                self.nick = json.get("author", {})["displayName"]
                self.time = json["date"]  # eg. 2026-02-07 04:09:32
                dt = datetime.strptime(self.time, "%Y-%m-%d %H:%M:%S")
                dt = dt.replace(tzinfo=timezone.utc)
                self.time = int(dt.timestamp())
                self.url = f"https://bsky.app/profile/{self.user_name}/post/{self.post_id_inner}"
                self.likes = json["likeCount"]
                self.reposts = json["repostCount"]
                self.comments = json["replyCount"]
                if "embed" in json and "record" in json["embed"]:
                    try:
                        self.embed = json["embed"]["record"]["uri"]
                    except:
                        self.embed = json["embed"]["record"]["record"]["uri"]
                self.isreply = "reply" in json
                reply_parent = json.get("reply", {}).get("parent", {})
                if reply_parent:
                    # print("*"*1000,"reply parent:", reply_parent)
                    reply_match = re.match(
                        r"at://([^/]+)/app.bsky.feed.post/([^/]+)$",
                        reply_parent.get("uri", ""),
                    )
                    if reply_match:
                        self.reply_to = f"{reply_match.group(2)}@{reply_match.group(1)}"
                reply_root = json.get("reply", {}).get("root", {})
                if reply_root:
                    reply_root_match = re.match(
                        r"at://([^/]+)/app.bsky.feed.post/([^/]+)$",
                        reply_root.get("uri", ""),
                    )
                    if reply_root_match:
                        self.reply_root = (
                            f"{reply_root_match.group(2)}@{reply_root_match.group(1)}"
                        )
                self.alt = ""
                self.alts = []
                for image in json.get("embed", {}).get("images", []) or json.get(
                    "embed", {}
                ).get("media", {}).get("images", []):
                    image_alt = image.get("alt", "")
                    if image_alt:
                        self.alts.append(image_alt)
                if self.alts:
                    self.alt = "<sep>".join(self.alts)
            elif self.type == "reddit":
                self.post_id_inner = json["id"]
                self.post_id = f"{self.post_id_inner}@reddit"
                self.text_content = (
                    f"<span class='rdt_title'>{json['title']}</span>{json['selftext']}"
                )
                self.user_name = json["subreddit"].lower()
                self.nick = self.user_name
                self.time = int(json["created_utc"])
                self.url = f"https://reddit.com/r/{self.user_name}/comments/{self.post_id_inner}"
                self.likes = json["score"]
                self.reposts = 0
                self.comments = json["num_comments"]
                self.isreply = False
                self.real_user = json.get("author", {})
            elif self.type == "fa":
                self.post_id_inner = str(json["id"])
                self.post_id = f"{self.post_id_inner}@fa"
                self.text_content = f"<span class='rdt_title'>{json['title']}</span>{json['description']}"
                self.user_name = json.get("user", {}).lower()
                self.nick = json.get("artist", self.user_name)
                try:
                    self.time = json["date"]  # eg. 2022-03-10 10:09:11
                    self.time = int(
                        time.mktime(time.strptime(self.time, "%Y-%m-%d %H:%M:%S"))
                    )
                except:
                    self.time = 0
                if (
                    json.get("subcategory") == "journals"
                    or json.get("category") == "journals"
                ):
                    self.url = (
                        f"https://www.furaffinity.net/journal/{self.post_id_inner}/"
                    )
                else:
                    self.url = f"https://www.furaffinity.net/view/{self.post_id_inner}/"
                self.likes = json.get("favorites", 0)
                self.reposts = 0
                self.comments = json.get("comments", 0)
                self.isreply = False
                self.real_user = self.user_name
            elif self.type == "patreon":
                self.post_id_inner = str(json["id"])
                self.post_id = f"{self.post_id_inner}@patreon"
                title = json.get("title", "")
                text = json.get("content", "") or json.get("substring", "")
                self.text_content = f"<span class='rdt_title'>{title}</span>{text}"
                self.user_name = json.get("user_name", "").lower() or self.user_name
                self.nick = self.user_name
                self.time = json.get("published", "")  # eg. 2025-11-27T05:11:59
                try:
                    self.time = int(
                        time.mktime(time.strptime(self.time, "%Y-%m-%dT%H:%M:%S"))
                    )
                except:
                    try:
                        # eg. 2025-11-27T05:11:59.123000
                        self.time = int(
                            time.mktime(
                                time.strptime(self.time, "%Y-%m-%dT%H:%M:%S.%f")
                            )
                        )
                    except:
                        self.time = 0
                self.url = f"https://www.patreon.com/posts/{self.post_id_inner}"
                self.likes = json.get("likes", 0)
                self.reposts = 0
                self.comments = 0
                self.isreply = False
                self.real_user = json.get("user", self.user_name)
            elif self.type == "e621":
                self.text_content = json.get("description", "" or self.text_content)
                tags = json.get("tags", [] or self.tags)
                if isinstance(tags, str):
                    tags = tags.split(" ")
                self.tags = tags
                self.time = json.get(
                    "upload_time", utils.id2time_gueeser(self.post_id_inner, "e621")
                )
                if json.get("parents", []):
                    self.isreply = True
                    parent_id = json["parents"][0]
                    self.reply_to = f"{parent_id}@e621"
                self.extra_data["links"] = json.get("source_links", [])
                self.url = f"https://e621.net/posts/{self.post_id_inner}"
                self.likes = int(json.get("score", 0)) + int(json.get("favorites", 0))
                self.reposts = 0
                self.comments = 0
                if json.get("pools", {}):
                    for pool_id in json["pools"]:
                        pool_title = json["pools"][pool_id].get("title", "")
                        # create a dummy post for pool
                        pool_post = Post(f"pool_{pool_id}@e621", self.user_name, "e621")
                        pool_post.text_content = (
                            f"<span class='rdt_title'>{pool_title}</span>"
                        )
                        pool_post.time = self.time
                        pool_post.uid = self.uid
                        pool_post.url = f"https://e621.net/pools/{pool_id}"
                        pool_post.save_to_db()
                        # copy media to pool post
                        self.init_medias()
                        for media in self.medias:
                            media.media_id = f"pool_{pool_id}_{media.media_id}"
                            media.post_id = pool_post.post_id
                            media.save_to_db()
                        self.extra_data.setdefault("embeds", []).append(
                            pool_post.post_id
                        )

        except Exception as e:
            logger.log(f"Error loading post from JSON: {e}", type="error")
            logger.log(json, type="error")
            self.json_error = True
        # Set uid after user_name and type are determined
        self.uid = f"{self.user_name}@{self.type}"
        self.save_to_db()

    def init_medias(self):
        self.medias = []
        self.attachments = []
        for row in database.db.query_rows(
            selected_table="media", key="post_id", value=self.post_id
        ):
            media_id = row[0]
            media = Media(media_id, self.post_id, self.user_name, self.type)
            media.load_from_db()
            if utils.media_type_from_extension(media.file_name) > utils.FLASH:
                self.has_attachment = True
                self.attachments.append(media)
            else:
                self.medias.append(media)
        self.medias = natsort.natsorted(self.medias, key=lambda x: x.media_id)
        logger.log(
            f"Initialized medias for post {self.post_id}, found {len(self.medias)} medias and {len(self.attachments)} attachments.",
            verbose=2,
        )

    def init_embeds(self):
        self.embeds = [
            embed.replace("https://", "").replace("http://", "").strip("/")
            for embed in self.embeds
            if embed
        ]
        self.embeds = list(set(self.embeds))
        seen = set()
        print(f"Embeds for post {self.post_id}: {self.embeds}")
        for embed in self.embeds:
            logger.log(f"Processing embed {embed} for post {self.post_id}", verbose=2)
            if embed == self.url.replace("https://", "").replace("http://", "").strip(
                "/"
            ):
                # If embed is same as url, skip to avoid infinite loop
                embed = ""
                continue
            if re.match(r"[a-zA-Z0-9_.-]+@[a-zA-Z0-9_.-]+", embed):
                post_id = embed
                type_ = embed.split("@")[-1]
                user_name = (
                    ""  # will automatically load from post_id in Post.load_from_db
                )
            elif embed.startswith("at://"):
                post_id = embed.split("/")[-1] + "@bsky"
                user_name = embed.split("/")[-3]
                type_ = "bsky"
            elif (
                "furaffinity.net/view/" in embed or "furaffinity.net/journal/" in embed
            ):
                post_id = embed.split("?")[0].strip("/").split("/")[-1] + "@fa"
                user_name = ""
                type_ = "fa"
            elif "/status/" in embed and ("x.com" in embed or "twitter.com" in embed):
                post_id = embed.split("/")[-1] + "@x"
                user_name = embed.split("/")[-3]
                type_ = "x"
            elif "/post/" in embed and "bsky.app" in embed:
                post_id = embed.split("/")[-1] + "@bsky"
                user_name = embed.split("/")[-3]
                type_ = "bsky"
            else:
                embed = ""
                continue
            if post_id in seen:
                continue
            seen.add(post_id)
            embed_obj = Post(post_id, user_name, type_)
            embed_obj.is_external = not embed_obj.load_from_db()
            # logger.log(f"Created embed object for {embed}: {embed_obj.post_id}", verbose=0)
            if not embed_obj.is_external:
                embed_obj.init_medias()
                embed_obj.user = User(embed_obj.user_name, embed_obj.type)
                embed_obj.user.load_from_db()
            embed_obj.concat_url()
            if "furaffinity.net/journal/" in embed:
                embed_obj.url = (
                    f"https://www.furaffinity.net/journal/{embed_obj.post_id_inner}/"
                )
            self.embed_objs.append(embed_obj)

    def concat_url(self):
        if self.type == "x":
            if not self.user_name:
                self.url = f"https://x.com/i/status/{self.post_id_inner}"
            else:
                self.url = f"https://x.com/{self.user_name}/status/{self.post_id_inner}"
        elif self.type == "bsky":
            self.url = (
                f"https://bsky.app/profile/{self.user_name}/post/{self.post_id_inner}"
            )
        elif self.type == "reddit":
            self.url = (
                f"https://reddit.com/r/{self.user_name}/comments/{self.post_id_inner}"
            )
        elif self.type == "fa":
            self.url = f"https://www.furaffinity.net/view/{self.post_id_inner}/"
        elif self.type == "e621":
            self.url = f"https://e621.net/posts/{self.post_id_inner}"

    def get_time_str(self):
        try:
            now = time.time()
            if now - self.time < 60:
                return "Just now"
            elif now - self.time < 3600:
                return f"{int((now - self.time) / 60)} minutes ago"
            elif now - self.time < 86400:
                return f"{int((now - self.time) / 3600)} hours ago"
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.time))
        except Exception as e:
            logger.log(
                f"Error formatting time for post {self.post_id}: {e}", type="error"
            )
            logger.log(f"User: {self.user_name}, Type: {self.type}", type="error")
            logger.log(f"Post time value: {self.time}", type="error")
            logger.log(traceback.format_exc(), type="error")
            return "Unknown time"

    def get_date_str(self):
        try:
            return time.strftime("%Y-%m-%d", time.localtime(self.time))
        except Exception as e:
            logger.log(
                f"Error formatting date for post {self.post_id}: {e}", type="error"
            )
            logger.log(f"User: {self.user_name}, Type: {self.type}", type="error")
            logger.log(f"Post time value: {self.time}", type="error")
            logger.log(traceback.format_exc(), type="error")
            return "Unknown date"


class User:
    def __init__(self, user_name, type=""):
        self.placeholder = False
        if not user_name:
            self.placeholder = True
        self.user_name = user_name.lower() if user_name else ""
        self.type = type
        self.uid = f"{self.user_name}@{self.type}" if type else None
        self.nick = ""
        self.udid = self.user_name
        self.update_time = 0
        self.flagged = 0
        self.avatar = ""
        self.banner = ""
        self.description = ""
        self.url = ""
        self.extra_data = dict()

    def load_from_db(self, ignore_cache=False):
        self.concat_url()
        if self.placeholder:
            return False
        # Query by uid if we have type, otherwise query by user_name
        if self.type:
            rows = database.db.query_rows("users", "uid", self.uid, ignore_cache)
        else:
            rows = database.db.query_rows(
                "users", "user_name", self.user_name, ignore_cache
            )
        if rows:
            try:
                row = rows[0]
                self.uid = row[0]
                self.user_name = row[1]
                self.udid = row[2]
                self.nick = row[3]
                self.avatar = row[4]
                self.banner = row[5]
                self.description, _ = utils.embed_hyperlink(self.type, row[6], "")
                self.type = row[7]
                self.update_time = row[8]
                self.flagged = row[9]
                self.extra_data = json.loads(row[10]) if row[10] else dict()
                if not self.nick:
                    self.nick = self.user_name
                self.concat_url()
                return True
            except Exception as e:
                logger.log(
                    f"Error loading user {self.user_name} from database: {e}",
                    type="error",
                )
                logger.log(rows, type="error")
                raise e
                return False
        return False

    def save_to_db(self):
        if self.placeholder:
            return
        database.db.insert_or_update_user(
            self.uid,
            self.user_name,
            self.udid,
            self.nick,
            self.avatar,
            self.banner,
            self.description,
            self.type,
            self.update_time,
            self.flagged,
            self.extra_data,
        )

    def load_from_json(self, json, use_fs_modified_time=False):
        if self.placeholder:
            return
        if self.type == "x":
            self.nick = json.get("author", {}).get("nick", self.user_name)
            self.udid = self.user_name
            self.avatar = json.get("author", {}).get("profile_image", "")
            self.banner = json.get("author", {}).get("profile_banner", "")
            self.description = json.get("author", {}).get("description", "")
            self.extra_data["url"] = (
                json.get("author", {})
                .get("url", "")
                .replace("http://", "")
                .replace("https://", "")
            )
            self.extra_data["location"] = json.get("author", {}).get("location", "")
        elif self.type == "bsky":
            self.nick = json.get("author", {})["displayName"]
            self.udid = json.get("author", {})["did"]
            self.avatar = json.get("author", {}).get("avatar", "")
            self.banner = json.get("user", {}).get("banner", "")
            self.description = json.get("user", {}).get("description", "")
        elif self.type == "reddit":
            self.nick = self.user_name
            self.udid = self.user_name
            self.avatar = ""
            self.banner = ""
            self.description = f"Reddit subreddit {self.user_name}.\n"
            try:
                about_json = utils.get_reddit_about(self.user_name)
                self.description += about_json.get("public_description", "")
                self.banner = (
                    about_json.get("banner_background_image", "").split("?")[0]
                    or about_json.get("banner_img", "").split("?")[0]
                )
                self.avatar = (
                    about_json.get("community_icon", "").split("?")[0]
                    or about_json.get("icon_img", "").split("?")[0]
                )
            except Exception as e:
                logger.log(
                    f"warning: could not fetch reddit about for {self.user_name}: {e}",
                    type="error",
                )
        elif self.type == "fa":
            self.nick = json["display_name"]
            self.udid = self.user_name
            self.avatar = json["avatar_url"]
            self.banner = json["banner_url"]
            self.description = json["description"]
        elif self.type == "patreon":
            self.nick = self.user_name
            if "public_id" in json:
                self.udid = json["id"]
            else:
                self.udid = json.get("user", self.user_name)
            service = json["service"]
            self.extra_data["service"] = service
            if service in ["patreon", "fanbox", "gumroad"] and re.match(
                r"\d+", self.udid
            ):
                self.avatar = (
                    f"https://img.{config.kemono_proxy}/icons/{service}/{self.udid}"
                )
                self.banner = (
                    f"https://img.{config.kemono_proxy}/banners/{service}/{self.udid}"
                )
                self.description = f"{service.capitalize()} user {self.user_name}."
            elif service == "onlyfans":
                self.avatar = (
                    f"https://img.{config.coomer_proxy}/icons/{service}/{self.udid}"
                )
                self.banner = (
                    f"https://img.{config.coomer_proxy}/banners/{service}/{self.udid}"
                )
                self.description = f"OnlyFans user {self.user_name}."
        elif self.type == "e621":
            self.description = json.get(
                "description", f"E621 tag <b>{self.user_name}</b> ."
            )
        if use_fs_modified_time:
            self.update_time = os.path.getmtime(
                os.path.join(config.fs_bases[self.type], self.user_name)
            )
        else:
            self.update_time = time.time()
        if not self.banner:
            logger.log(
                f"warning: user {self.user_name} has no banner.",
                type="warning",
            )
        if not self.description:
            logger.log(
                f"warning: user {self.user_name} has no description.",
                type="warning",
            )
        self.concat_url()
        self.save_to_db()

    def concat_url(self):
        self.url = self.uid
        if self.type == "x":
            self.url = f"https://x.com/{self.user_name}"
        elif self.type == "bsky":
            self.url = f"https://bsky.app/profile/{self.user_name}"
        elif self.type == "reddit":
            self.url = f"https://reddit.com/r/{self.user_name}"
        elif self.type == "fa":
            self.url = f"https://www.furaffinity.net/user/{self.user_name}"
        elif self.type == "patreon":
            service = self.extra_data.get("service", "")
            logger.log(f"User data: {self.extra_data}", verbose=2)
            if service in ["patreon", "fanbox", "gumroad"]:
                self.url = f"https://{config.kemono_proxy}/{service}/user/{self.udid}"
                self.extra_data["party_url"] = (
                    f"https://{config.kemono_proxy}/{service}/user/{self.udid}"
                )
            elif service == "onlyfans":
                self.url = f"https://onlyfans.com/{self.user_name}"
                self.extra_data["party_url"] = (
                    f"https://{config.coomer_proxy}/onlyfans/user/{self.udid}"
                )
        elif self.type == "e621":
            self.url = (
                f"https://e621.net/posts?tags={self.user_name.replace('[at]', '@')}"
            )

    def get_update_time_str(self):
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.update_time))


class Media:
    def __init__(self, media_id, post_id, user_name, type):
        self.media_id = media_id
        self.post_id = post_id
        self.user_name = user_name
        self.type = type
        self.uid = f"{user_name}@{type}" if user_name and type else None
        self.size = 0
        self.duration = 0
        self.duration_str = "00:00"
        self.extra_data = dict()
        self.media_type = utils.UNKNOWN

    def save_to_db(self):
        # print(f"Saving media {self.media_id} to database with post_id {self.post_id}, file_name {self.file_name}, uid {self.uid}, type {self.type}, size {self.size}, duration {self.duration}")
        database.db.insert_or_update_media(
            self.media_id,
            self.post_id,
            self.file_name,
            self.uid,
            self.type,
            self.media_type,
            self.size,
            self.duration,
        )

    def load_from_db(self):
        rows = database.db.query_rows("media", "media_id", self.media_id)
        if len(rows) == 0:
            return False
        row = rows[0]
        try:
            self.post_id = row[1]
            self.file_name = row[2]
            if not self.file_name:
                return False
            self.file_name = self.file_name
            self.uid = row[3]
            # Extract user_name and type from uid if not already set
            if self.uid and "@" in self.uid:
                parts = self.uid.rsplit("@", 1)
                if not self.user_name:
                    self.user_name = parts[0]
                if not self.type:
                    self.type = parts[1]
            self.type = row[4]
            self.size = row[6]
            self.duration = row[7]
            self.duration_str = utils.format_duration(self.duration)
            self.isvideo = (
                utils.media_type_from_extension(self.file_name) == utils.VIDEO
            )
            self.isaudio = (
                utils.media_type_from_extension(self.file_name) == utils.AUDIO
            )
            self.isimage = (
                utils.media_type_from_extension(self.file_name) == utils.IMAGE
            )
            self.isflash = (
                utils.media_type_from_extension(self.file_name) == utils.FLASH
            )
            self.isattachment = (
                utils.media_type_from_extension(self.file_name) == utils.TEXT
            )
            self.media_type = utils.media_type_from_extension(self.file_name)
        except Exception as e:
            logger.log("Error:", e, type="error")
            logger.log("Rows:", rows, type="error")
            return False
        return True

    def get_size_str(self):
        return utils.format_size(self.size)


def bsky_link_fix(text, facets):
    try:
        for facet in facets:
            for feature in facet["features"]:
                if feature["$type"] != "app.bsky.richtext.facet#link":
                    continue
                uri = feature["uri"].replace("https://", "").replace("http://", "")
                length = abs(facet["index"]["byteEnd"] - facet["index"]["byteStart"])
                if length < len(uri):
                    shortened_uri = uri[: length - 3] + "..."
                    text = text.replace(shortened_uri, uri)
                    # logger.log(
                    #     f"warning: bsky link {shortened_uri} is shortened, replaced with {uri}"
                    # )
    except Exception as e:
        logger.log(f"Error fixing bsky link: {e}", type="error")
    return text


# scan for content downloaded using gallery-dl
def scan_for_users(type, user_name=None, force=False):
    global all_users
    if user_name == "TBD":
        return
    fs_base = config.fs_bases[type]
    # assume that the user name is the same as the directory name
    if not user_name:
        user_names = os.listdir(fs_base)
    else:
        user_names = [user_name]
    with utils.scan_lock:
        for user_name in user_names:
            try:
                if not os.path.exists(os.path.join(fs_base, user_name)):
                    logger.log(user_name, "does not exists!")
                    continue
                elif not os.path.isdir(os.path.join(fs_base, user_name)):
                    logger.log(user_name, "is not a dir!")
                    continue
                elif user_name.startswith("."):
                    continue
                logger.log(f"scanning for user {user_name}@{type}")
                user = User(user_name, type)
                if (not user.load_from_db(True)) or len(user_names) == 1 or force:
                    # user not found in database, create a new entry
                    # select the first json file
                    json_files = []
                    if type == "fa":
                        if os.path.exists(
                            os.path.join(fs_base, user_name, "user.json")
                        ):
                            json_files = ["user.json"]
                        elif os.path.exists(
                            os.path.join(fs_base, user_name, "user.json.gz")
                        ):
                            json_files = ["user.json.gz"]
                    elif (
                        type in ["x", "bsky", "reddit", "patreon"]
                        and not user_name == "reddit_users"
                    ):
                        file_list = os.listdir(os.path.join(fs_base, user_name))
                        json_files = [
                            f
                            for f in file_list
                            if f.endswith(".json") or f.endswith(".json.gz")
                        ]
                        json_files = natsort.natsorted(json_files, reverse=True)
                    elif type == "e621":
                        json_files = (
                            ["profile.json"]
                            if os.path.exists(
                                os.path.join(fs_base, user_name, "profile.json")
                            )
                            else []
                        )
                    else:
                        logger.log(
                            f"Unknown type {type} for user {user_name}, cannot determine json file.",
                            type="error",
                        )
                        return
                    if len(json_files) > 0:
                        logger.log(f"found user json file: {json_files[0]}")
                        if json_files[0].endswith(".json.gz"):
                            file_handle = gzip.open(
                                os.path.join(fs_base, user_name, json_files[0]),
                                "rt",
                                encoding="utf-8",
                            )
                        else:
                            file_handle = open(
                                os.path.join(fs_base, user_name, json_files[0]),
                                "r",
                                encoding="utf-8",
                            )
                        with file_handle as f:
                            user_json = json.load(f)
                            if len(user_names) == 1:
                                user.load_from_json(user_json)
                            else:
                                user.load_from_json(user_json, True)
                    else:
                        # no json file found, use dummy values
                        user.nick = user_name
                        user.avatar = ""
                        user.banner = ""
                        if user_name == "reddit_users":
                            user.description = "Includes user posts that not belonging to any subreddit."
                        elif type == "e621":
                            user.description = f"E621 tag <b>{user_name}</b> ."
                        else:
                            user.description = ""
                        user.update_time = time.time()
                        user.save_to_db()
            except Exception as e:
                logger.log(e, type="error")
                logger.log("Error loading user:", user_name, type="error")
                raise e
            database.db.commit()
        database.db.clear_cache()
        all_users = get_users()


def check_for_missing_media(uid, remove=False):
    rows = database.db.query_rows("media", "uid", uid, ignore_cache=True)
    for row in rows:
        media_id = row[0]
        file_name = row[2]
        uid = row[3]
        user_name, type = uid.split("@")
        fs_base = config.fs_bases[type]
        media_path = os.path.join(fs_base, user_name, file_name)
        if not os.path.exists(media_path):
            logger.log(
                f"Media file {media_path} is missing for media_id {media_id}, post_id {row[1]}, uid {uid}",
                type="warning",
            )
            if remove:
                database.db.raw_query(
                    ("DELETE FROM media WHERE media_id = ?", (media_id,)),
                    ignore_cache=True,
                )
                logger.log(
                    f"Removed media_id {media_id} from database.", type="warning"
                )


current_scan_user = None


def scan_for_posts(type, user_name=None, force=False):
    global current_scan_user
    if force:
        logger.log("Forced rescan, will read all json files.")
    if user_name == "TBD":
        return
    fs_base = config.fs_bases[type]
    if not user_name:
        user_names = os.listdir(fs_base)
    else:
        user_names = [user_name]
    try:
        for cnt, user_name in enumerate(user_names):
            time.sleep(0.1)  # allow manually added job to acquire scan_lock faster
            with utils.scan_lock:
                utils.current_status = utils.SCANNING
                current_scan_user = user_name
                uid = f"{user_name}@{type}"
                user_fs_path = os.path.join(fs_base, user_name)
                if not os.path.exists(user_fs_path) or not os.path.isdir(user_fs_path):
                    logger.log(user_name, "does not exists!")
                    continue

                logger.log(
                    f"[{cnt+1}/{len(user_names)}] scanning for posts of user {user_name}@{type}"
                )

                filelist = os.listdir(user_fs_path)
                if force:
                    check_for_missing_media(uid, True)
                # check if is file
                filelist = [
                    f
                    for f in filelist
                    if os.path.isfile(os.path.join(fs_base, user_name, f))
                ]
                filelist = [f for f in filelist if f.lower() not in utils.exclude_files]

                # read json files in the user directory
                json_files = [
                    f for f in filelist if f.endswith(".json") or f.endswith(".json.gz")
                ]
                regex_map = {
                    "x": {
                        "file_patterns": [r"\d+.+json"],
                        "id_pattern": [r"(\d+)", r"([a-zA-Z0-9]+)"],
                    },
                    "bsky": {
                        "file_patterns": [
                            r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}.+\.json"
                        ],
                        "id_pattern": [
                            r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}_([^_]+).+",
                            r"([a-zA-Z0-9]+)",
                        ],
                    },
                    "reddit": {
                        "file_patterns": [r".+json"],
                        "id_pattern": [r"([a-zA-Z0-9]+)"],
                    },
                    "fa": {
                        "file_patterns": [r"\d+"],
                        "id_pattern": [r"(\d+)", r"([a-zA-Z0-9]+)"],
                    },
                    "patreon": {
                        "file_patterns": [r"\d{4,20}"],
                        "id_pattern": [r"(\d{4,20})", r"([a-zA-Z0-9]+)"],
                    },
                    "e621": {
                        "file_patterns": [r"\d+.+json"],
                        "id_pattern": [r"(\d+)"],
                    },
                }
                post_files = []
                patterns = regex_map[type]
                for pat in patterns["file_patterns"]:
                    post_files += [f for f in json_files if re.match(pat, f)]
                for post_file in post_files:
                    id_match = [
                        re.match(pat, post_file) for pat in patterns["id_pattern"]
                    ]
                    id_match = [m for m in id_match if m]
                    if not id_match:
                        logger.log(
                            f"warning: filename {post_file} does not match any id pattern for type {type}, skipped.",
                            type="warning",
                        )
                        continue
                    post_id = id_match[0].group(1) + "@" + type
                    post = Post(post_id, user_name, type)
                    if (not post.load_from_db()) or force:
                        json_file = os.path.join(fs_base, user_name, post_file)
                        logger.log("Reading json file:", json_file, verbose=3)
                        if post_file.endswith(".json.gz"):
                            file_handle = gzip.open(json_file, "rt", encoding="utf-8")
                        else:
                            file_handle = open(json_file, "r", encoding="utf-8")
                        with file_handle as f:
                            try:
                                post_json = json.load(f)
                                post.load_from_json(post_json)
                            except Exception as e:
                                logger.log(e, type="error")
                                logger.log(
                                    "Error loading:",
                                    json_file,
                                    type="error",
                                )
                                if debug_mode:
                                    traceback.print_exc()
                database.db.commit()
                #
                # scan for media
                #
                logger.log(
                    f"[{cnt+1}/{len(user_names)}] scanning for media of user {user_name}@{type}"
                )
                for root, dirs, files in os.walk(os.path.join(fs_base, user_name)):
                    for media_file in files:
                        if utils.media_type_from_extension(media_file) == utils.UNKNOWN:
                            continue
                        if media_file.lower() in utils.exclude_files:
                            continue
                        media_full_path = os.path.join(root, media_file)
                        rel_path = os.path.relpath(
                            media_full_path, os.path.join(fs_base, user_name)
                        )
                        if rel_path.startswith("."):
                            continue
                        if type in ["x", "bsky", "reddit", "patreon", "e621"]:
                            media_id = (
                                f"{rel_path.replace(os.sep, '_')}@{user_name}@{type}"
                            )
                            id_match = [
                                re.match(pat, rel_path)
                                for pat in patterns["id_pattern"]
                            ]
                            id_match = [m for m in id_match if m]
                            # print("rel_path:", rel_path, "media_file:", media_file)
                            # print([m.group(1) for m in id_match])
                            if id_match:
                                related_post_id = f"{id_match[0].group(1)}@{type}"
                            else:
                                # strip file extension and use the remaining as media id
                                related_post_id = media_id.split(".")[0] + "@" + type
                                logger.log(
                                    "warning: no post_id found in filename:",
                                    media_file,
                                    type="warning",
                                )
                        elif type == "fa":
                            media_id = media_file
                            if media_file.endswith("_thumb.jpg"):
                                base_media_id = media_file[:-10]
                            else:
                                base_media_id = media_file
                            related_json_path = os.path.join(
                                fs_base, user_name, base_media_id + ".json"
                            )
                            if os.path.exists(related_json_path):
                                with open(
                                    related_json_path,
                                    "r",
                                    encoding="utf-8",
                                ) as f:
                                    try:
                                        post_json = json.load(f)
                                        related_post_id = str(post_json["id"]) + "@fa"
                                    except Exception as e:
                                        logger.log(e)
                                        related_post_id = f"{media_file}@{user_name}@fa"
                                        logger.log(
                                            "Error loading:",
                                            related_json_path,
                                            type="error",
                                        )
                                        if debug_mode:
                                            raise e
                            else:
                                related_post_id = f"{media_file}@{user_name}@fa"
                                logger.log(
                                    "warning: no json file found for media:",
                                    related_json_path,
                                    type="warning",
                                )
                        else:
                            logger.log(
                                f"Unknown type {type} for media scanning.", type="error"
                            )
                            continue
                        # test if related post exists
                        post = Post(related_post_id, user_name, type)
                        if not post.load_from_db(True):
                            logger.log(
                                f"warning: media {media_id} has no related post {related_post_id} in database",
                                type="warning",
                            )
                            # create a dummy post
                            post.text_content = f"[no meta] {rel_path}"
                            post.user_name = user_name
                            guessed_timestamp = re.match(
                                r"(?:^|[\._ ])(\d{10})[\._ ]", media_id
                            )
                            if guessed_timestamp and time.gmtime() > time.gmtime(
                                int(guessed_timestamp.group(1))
                            ):
                                post.time = int(guessed_timestamp.group(1))
                            else:
                                post.time = int(os.path.getmtime(media_full_path))
                            post.type = type
                            if type == "reddit" and re.match(
                                r"[a-zA-Z0-9]{6,8}_\d", media_id
                            ):
                                dummy_id = media_id.split("_")[0]
                                post.url = f"https://reddit.com/r/{user_name}/comments/{dummy_id}"
                            else:
                                post.url = ""
                            if type == "e621":
                                guessed_id = re.match(r"(\d+)", media_id)
                                if guessed_id:
                                    post.url = (
                                        f"https://e621.net/posts/{guessed_id.group(1)}"
                                    )
                                    guessed_timestamp = utils.id2time_gueeser(
                                        guessed_id.group(1), "e621"
                                    )
                                    if (
                                        guessed_timestamp
                                        and time.gmtime()
                                        > time.gmtime(guessed_timestamp)
                                    ):
                                        post.time = guessed_timestamp
                            post.likes = 0
                            post.reposts = 0
                            post.comments = 0
                            post.save_to_db()
                        media = Media(media_id, related_post_id, user_name, type)
                        if (not media.load_from_db()) or force:
                            media.file_name = rel_path
                            media.post_id = related_post_id
                            try:
                                media.size = os.path.getsize(media_full_path)
                            except:
                                media.size = 0
                            if (
                                utils.media_type_from_extension(media_file)
                                == utils.VIDEO
                                and not media.duration
                            ):
                                logger.log(f"Probing video duration for {media_file}")
                                media.duration = utils.probe_video_duration(
                                    media_full_path
                                )
                            media.save_to_db()
                database.db.commit()
    except Exception as e:
        logger.log(e, type="error")
        logger.log(
            f"Error scanning posts for user {current_scan_user}@{type}", type="error"
        )
        if debug_mode:
            raise e
    finally:
        current_scan_user = None
        utils.current_status = utils.RUNNING
        database.db.clear_cache()


@utils.time_it
def scan_custom_user(scan_posts=True, force=False):
    """
    Scan custom users and posts.
    """
    if not os.path.exists(config.custom_user_json):
        logger.log(
            f"Custom user json file {config.custom_user_json} does not exist.",
            type="warning",
        )
        return
    users = []
    with open(config.custom_user_json, "r", encoding="utf-8") as f:
        users = json.load(f)
    with utils.scan_lock:
        for user_json in users:
            try:
                user = User(user_json.get("name", ""), user_json.get("category", ""))
                user.nick = user_json.get("nick", user.user_name)
                user.description = user_json.get("description", "")
                user.fs_base = user_json.get("fs_base", "").rstrip("/\\")
                config.fs_bases[user.type] = os.path.split(user.fs_base)[0]
                user.url = user_json.get("url", "")
                user.save_to_db()
            except Exception as e:
                logger.log(f"Error loading custom user from json: {e}", type="error")
                logger.log(user_json, type="error")
            if (
                not user.fs_base
                or not os.path.exists(user.fs_base)
                or not os.path.isdir(user.fs_base)
            ):
                logger.log(
                    f"User {user.user_name} has invalid fs_base {user.fs_base}, skipped scanning posts.",
                    type="warning",
                )
                continue
            if not scan_posts:
                continue
            if force:
                check_for_missing_media(user.uid, True)
            if user_json.get("post_organization", "") == "by_folder":
                for folder in os.listdir(user.fs_base):
                    if not os.path.isdir(os.path.join(user.fs_base, folder)):
                        continue
                    meta_file = os.path.join(
                        user.fs_base,
                        folder,
                        user_json.get("post_metadata", "meta.json"),
                    )
                    print(f"Looking for post metadata in {meta_file}")
                    post_json = {}
                    post_id = utils.md5_hash(folder) + "@" + user.type
                    post = Post(post_id, user.user_name, user.type)
                    if not post.load_from_db() or force:
                        if os.path.exists(meta_file):
                            with open(meta_file, "r", encoding="utf-8") as f:
                                post_json = json.load(f)
                        post.text_content = f"<span class='rdt_title'>{post_json.get('title', folder)}</span>"
                        post.text_content += post_json.get("text", "")
                        post.tags = post_json.get("tags", [])
                        if isinstance(post.tags, str):
                            post.tags = post.tags.split(" ")
                        post.time = post_json.get(
                            "time",
                            int(os.path.getmtime(os.path.join(user.fs_base, folder))),
                        )
                        post.url = post_json.get("url", "")
                        post.likes = post_json.get("likes", 0)
                        post.reposts = post_json.get("reposts", 0)
                        post.comments = post_json.get("comments", 0)
                        post.save_to_db()
                        # scan for media in the folder
                        for filename in os.listdir(os.path.join(user.fs_base, folder)):
                            if (
                                utils.media_type_from_extension(filename)
                                == utils.UNKNOWN
                            ):
                                continue
                            media_full_path = os.path.join(
                                user.fs_base, folder, filename
                            )
                            media_id = (
                                f"{folder}_{filename}@{user.user_name}@{user.type}"
                            )
                            media = Media(media_id, post_id, user.user_name, user.type)
                            media.load_from_db()
                            media.file_name = folder + "/" + filename
                            try:
                                media.size = os.path.getsize(media_full_path)
                            except:
                                media.size = 0
                            if (
                                utils.media_type_from_extension(filename) == utils.VIDEO
                                and not media.duration
                            ):
                                logger.log(f"Probing video duration for {filename}")
                                media.duration = utils.probe_video_duration(
                                    media_full_path
                                )
                            media.save_to_db()
            else:
                logger.log(
                    "Not implemented post organization method:",
                    user_json.get("post_organization", ""),
                    type="warning",
                )


def get_users():
    rows = database.db.raw_query("SELECT uid, type FROM users")
    users = {"new": [], "name": []}
    for row in rows:
        try:
            # uid = row[0]
            # print(row)
            user = User(*row[0].split("@"))
            user.load_from_db()
            users["new"].append(user)
            users["name"].append(user)
        except Exception as e:
            logger.log(f"Error loading user {row[0]} from database: {e}", type="error")
            continue
    users["new"].sort(key=lambda u: u.update_time, reverse=True)
    users["name"].sort(key=lambda u: u.uid)
    return users


def get_usernames_by_type(type):
    rows = database.db.raw_query(f"SELECT * FROM users WHERE type = '{type}'")
    usernames = []
    for row in rows:
        user_name = row[1]
        usernames.append(user_name)
    return usernames


def flag_user(user_name, type):
    global all_users
    uid = f"{user_name}@{type}"
    # logger.log(f"*********Flagging user {uid}")
    # logger.log(f"UPDATE users SET flagged = 1 WHERE uid = \"{uid}\"")
    database.db.raw_query(
        f'UPDATE users SET flagged = 1 WHERE uid = "{uid}"', "main", True
    )
    database.db.commit()
    database.db.clear_cache()
    all_users = get_users()


def unflag_user(user_name, type):
    global all_users
    uid = f"{user_name}@{type}"
    # logger.log(f"*********Unflagging user {uid}")
    # logger.log(f"UPDATE users SET flagged = 0 WHERE uid = \"{uid}\"")
    database.db.raw_query(
        f'UPDATE users SET flagged = 0 WHERE uid = "{uid}"', "main", True
    )
    database.db.commit()
    database.db.clear_cache()
    all_users = get_users()


all_users = {}


@utils.time_it
def get_fav():
    res = database.db.query_rows(
        selected_table="fav", key="", value="", ignore_cache=True
    )
    res = [r[0] for r in res]
    return res


def add_favorite(post_id):
    if not database.db.query_rows("posts", "post_id", post_id):
        return
    database.db.raw_query(
        f"INSERT OR REPLACE INTO fav VALUES ('{post_id}', '{time.ctime()}')",
        "fav",
        True,
    )
    database.db.commit()


def remove_favorite(post_id):
    database.db.raw_query(f"DELETE FROM fav WHERE post_id = '{post_id}'", "fav", True)
    database.db.commit()


def get_user_groups():
    rows = database.db.raw_query(
        "SELECT DISTINCT group_name, group_id FROM user_group ORDER BY timestamp DESC",
        ignore_cache=True,
    )
    return rows


def get_uids_in_group(group_id):
    rows = database.db.raw_query(
        f"SELECT uid FROM user_group WHERE group_id = '{group_id}'",
        ignore_cache=True,
    )
    return [row[0] for row in rows]


def add_user_to_group(uid, group_name):
    group_id = utils.md5_hash(group_name)
    database.db.raw_query(
        f"INSERT OR REPLACE INTO user_group VALUES ('{group_id}','{group_name}','{uid}','{time.ctime()}')",
        "user_group",
        ignore_cache=True,
    )
    database.db.commit()


def remove_user_from_group(uid, group_id):
    database.db.raw_query(
        f"DELETE FROM user_group WHERE uid = '{uid}' AND group_id = '{group_id}'",
        "user_group",
        ignore_cache=True,
    )
    database.db.commit()


def rename_group(old_group_name, new_group_name):
    old_group_id = utils.md5_hash(old_group_name)
    new_group_id = utils.md5_hash(new_group_name)
    database.db.raw_query(
        f"UPDATE user_group SET group_name = '{new_group_name}', group_id = '{new_group_id}' WHERE group_id = '{old_group_id}'",
        "user_group",
        ignore_cache=True,
    )
    database.db.commit()


def get_all_usernames(type):
    rows = database.db.raw_query(
        f"SELECT user_name FROM users WHERE type = '{type}'", ignore_cache=True
    )
    return [row[0] for row in rows]


if not os.path.exists(config.fs_bases["x"]):
    os.makedirs(config.fs_bases["x"])
if not os.path.exists(config.fs_bases["bsky"]):
    os.makedirs(config.fs_bases["bsky"])

if __name__ == "__main__":
    print("This is the backend module. Please run app.py to start the application.")
