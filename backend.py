import sqlite3
import os, json, re, time, sys
import natsort, random
import threading

import config, utils, logger

debug_mode = False


class Database:
    def __init__(self, db_file, fav_db_file):
        self.conn = sqlite3.connect(db_file, check_same_thread=False)
        self.fav_conn = sqlite3.connect(fav_db_file, check_same_thread=False)
        self.cached_query_words = dict()
        self.last_text_query_time = -1
        self.db_lock = threading.Lock()

    def prepare_db(self):
        cursor = self.conn.cursor()
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS users (
            uid TEXT PRIMARY KEY,
            user_name TEXT,
            udid TEXT,
            nick TEXT,
            avatar TEXT,
            banner TEXT,
            description TEXT,
            type TEXT,
            update_time NUMBER,
            flagged BOOLEAN DEFAULT 0
        )"""
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_user_name ON users(user_name)"
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_udid ON users(udid)")
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS posts (
            post_id TEXT PRIMARY KEY,
            text_content TEXT,
            uid TEXT,
            nick TEXT,
            time TEXT,
            type TEXT,
            url TEXT,
            likes INTEGER,
            reposts INTEGER,
            comments INTEGER,
            embed TEXT,
            isreply BOOLEAN,
            reply_to TEXT,
            real_user TEXT
        )"""
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_posts_uid ON posts(uid)")
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS media (
            media_id TEXT PRIMARY KEY,
            post_id TEXT,
            file_name TEXT,
            uid TEXT,
            type TEXT,
            time TEXT
        )"""
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_post_id ON media(post_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_uid ON media(uid)")
        fav_cursor = self.fav_conn.cursor()
        fav_cursor.execute(
            """CREATE TABLE IF NOT EXISTS fav (
            post_id TEXT PRIMARY KEY,
            fav_time TEXT
        )"""
        )
        fav_cursor.close()
        self.fav_conn.commit()
        cursor.close()
        self.conn.commit()

    def insert_or_update_user(
        self,
        uid,
        user_name,
        udid,
        nick,
        avatar,
        banner,
        description,
        type,
        update_time=None,
        flagged=0,
    ):
        if not update_time:
            update_time = time.time()
        with self.db_lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO users VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    uid,
                    user_name,
                    udid,
                    nick,
                    avatar,
                    banner,
                    description,
                    type,
                    update_time,
                    flagged,
                ),
            )
            cursor.close()

    def insert_or_update_post(
        self,
        post_id,
        text_content,
        uid,
        nick,
        time,
        type,
        url,
        likes,
        reposts,
        comments,
        embed,
        isreply,
        reply_to="",
        real_user="",
    ):
        with self.db_lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO posts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    post_id,
                    text_content,
                    uid,
                    nick,
                    time,
                    type,
                    url,
                    likes,
                    reposts,
                    comments,
                    embed,
                    isreply,
                    reply_to,
                    real_user,
                ),
            )
            cursor.close()

    def insert_or_update_media(self, media_id, post_id, file_name, uid, type, time):
        with self.db_lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO media VALUES (?,?,?,?,?,?)",
                (media_id, post_id, file_name, uid, type, time),
            )
            cursor.close()

    def query_rows(self, selected_db, key, value, ignore_cache=False, sort_key=None):
        if key:
            if isinstance(key, list):
                key = " AND ".join([f"{k} = ?" for k in key])
                value = tuple(value)
                res = self.raw_query(
                    (f"SELECT * FROM {selected_db} WHERE {key}", value),
                    selected_db=selected_db,
                    ignore_cache=ignore_cache,
                )
            else:
                res = self.raw_query(
                    (f"SELECT * FROM {selected_db} WHERE {key} = ?", (value,)),
                    selected_db=selected_db,
                    ignore_cache=ignore_cache,
                )
        else:
            res = self.raw_query(
                f"SELECT * FROM {selected_db}",
                selected_db=selected_db,
                ignore_cache=ignore_cache,
            )
        if sort_key:
            res = natsort.natsorted(res, key=sort_key, reverse=True)
        return res

    def raw_query(self, sql, selected_db="main", ignore_cache=False):
        global query_cache
        if len(query_cache) > 5000:
            # logger.log("Clear query cache.")
            query_cache = dict()
        if sql in query_cache and not ignore_cache:
            if debug_mode:
                logger.log("Use cached raw query for", sql, verbose=2)
            return query_cache[sql]
        else:
            with self.db_lock:
                if selected_db == "fav":
                    cursor = self.fav_conn.cursor()
                else:
                    cursor = self.conn.cursor()
                if type(sql) == str:
                    sql = sql.strip()
                    cursor.execute(sql)
                elif type(sql) in (list, tuple):
                    cursor.execute(*sql)
                else:
                    raise ValueError("sql must be a string or a tuple/list")
                res = cursor.fetchall()
                cursor.close()
            if not ignore_cache:
                query_cache[sql] = res
            return res

    def query_post_by_text(self, text_content):
        global cached_query_words
        text_content = text_content.strip()
        words = tuple(
            set([i.lstrip("u/") for i in text_content.split() if i and i != " "])
        )
        if words in self.cached_query_words:
            if abs(self.cached_query_words[words][0] - time.time()) > 1200:
                logger.log("Clear outdated query cache.")
                self.cached_query_words = dict()
            else:
                logger.log("Use cached query for", words)
                return self.cached_query_words[words][1]
        logger.log("Querying posts by text:", words)
        placeholders = " AND ".join(
            ["(text_content || ' ' || nick || real_user) LIKE ?"] * len(words)
        )
        sql_query = f"SELECT post_id, time FROM posts WHERE {placeholders}"
        params = tuple([f"%{word}%" for word in words])
        res = self.raw_query((sql_query, params))
        res = natsort.natsorted(res, key=lambda x: x[1], reverse=True)
        self.cached_query_words[words] = (time.time(), res)
        return res

    def query_media_by_text(self, text_content):
        global cache_query_media_id
        text_content = text_content.strip()
        words = tuple(
            set([i.lstrip("u/") for i in text_content.split() if i and i != " "])
        )
        if words in cache_query_media_id:
            if abs(cache_query_media_id[words][0] - time.time()) > 1200:
                logger.log("Clear outdated query cache.")
                cache_query_media_id = dict()
            else:
                logger.log("Use cached query for", words)
                return cache_query_media_id[words][1]
        logger.log("Querying media by text:", words)
        placeholders = " AND ".join(
            ["(text_content || ' ' || nick || real_user) LIKE ?"] * len(words)
        )
        sql_query = f"SELECT media_id, time FROM media WHERE (file_name LIKE '%.mp4' OR file_name LIKE '%.webm' OR file_name LIKE '%.m4v') AND post_id IN (SELECT post_id FROM posts WHERE {placeholders})"
        params = tuple([f"%{word}%" for word in words])
        res = self.raw_query((sql_query, params))
        res = natsort.natsorted(res, key=lambda x: x[1], reverse=True)
        if res:
            res = [i[0] for i in res]
            cache_query_media_id[words] = (time.time(), res)
        else:
            cache_query_media_id[words] = (time.time(), [])
        return cache_query_media_id[words][1]

    def commit(self):
        self.conn.commit()
        self.fav_conn.commit()

    def clear_cache(self):
        global query_cache
        query_cache = dict()


class Embed:
    def __init__(self, post_id, udid, type=""):
        self.post_id = post_id
        self.udid = udid
        self.user_name = ""
        self.nick = ""
        self.type = type
        self.medias = []
        self.external = True
        self.text_content = ""
        self.time = ""
        if type == "x":
            self.url = f"https://x.com/{self.udid}/status/{self.post_id}"
        elif type == "bsky":
            self.url = f"https://bsky.app/profile/{self.udid}/post/{self.post_id}"

    def load_from_db(self, db):
        # check if user exists
        rows = db.query_rows("users", "udid", self.udid)
        if len(rows) == 0:
            logger.log(f"User {self.udid} not found in database")
            return False
        self.uid = rows[0][0]
        self.user_name = rows[0][1]
        self.nick = rows[0][3]
        # check if post exists
        rows = db.query_rows("posts", "post_id", self.post_id)
        if len(rows) == 0:
            logger.log(f"Post {self.post_id} not found in database")
            return False
        self.text_content = rows[0][1]
        self.text_content = utils.embed_hyperlink(self.type, self.text_content)
        self.time = rows[0][4]
        self.external = False
        # find all media related to this post
        rows = db.query_rows("media", "post_id", self.post_id)
        for row in rows:
            # Extract user_name from uid
            uid = row[3]
            user_name = uid.split("@")[0] if "@" in uid else uid
            media = Media(row[0], row[1], user_name, self.type, row[5])
            media.file_name = row[2]
            media.load_from_db(db)
            self.medias.append(media)


class Post:
    def __init__(self, post_id, user_name, type):
        self.post_id = post_id
        self.user_name = user_name
        self.type = type
        self.uid = f"{user_name}@{type}" if user_name and type else None
        self.nick = ""
        self.fav = False
        self.embed = ""
        self.isreply = False
        self.is_externalreply = False
        self.reply_to = ""
        if type == "reddit":
            self.real_user = "[deleted]"
        else:
            self.real_user = ""

    def load_from_db(self, db: Database):
        rows = db.query_rows("posts", "post_id", self.post_id)
        if len(rows) == 0:
            return False
        row = rows[0]
        self.uid = row[2]
        # Extract user_name and type from uid if not already set
        if self.uid and "@" in self.uid:
            parts = self.uid.rsplit("@", 1)
            if not self.user_name:
                self.user_name = parts[0]
            if not self.type:
                self.type = parts[1]
        self.nick = row[3]
        self.time = row[4]
        self.type = row[5]
        self.url = row[6]
        self.likes = row[7]
        self.reposts = row[8]
        self.comments = row[9]
        self.embed = row[10]
        self.isreply = row[11]
        self.reply_to = row[12]
        self.real_user = row[13]
        self.text_content = utils.embed_hyperlink(self.type, row[1])
        # check if post is in fav
        rows = db.query_rows("fav", "post_id", self.post_id, True)
        if len(rows) > 0:
            self.fav = True
        return True

    def save_to_db(self, db):
        db.insert_or_update_post(
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
            self.isreply,
            self.reply_to,
            self.real_user,
        )

    def load_from_json(self, json, db):
        if self.type == "x":
            self.post_id = str(json["tweet_id"])
            self.text_content = json["content"]
            if not self.user_name:
                self.user_name = json["author"]["name"].lower()
            self.nick = json["author"]["nick"]
            self.time = json["date"]
            self.url = f"https://x.com/{self.user_name}/status/{self.post_id}"
            self.likes = json["favorite_count"]
            self.reposts = json["retweet_count"]
            self.comments = json["reply_count"]

            self.isreply = "reply_to" in json
            reply_id = json.get("reply_id", "")
            reply_to_user = json.get("reply_to", "")
            if reply_id and reply_to_user:
                self.reply_to = f"{reply_id}@{reply_to_user.lower()}"
        elif self.type == "bsky":
            self.post_id = str(json["post_id"])
            self.text_content = json["text"]
            if "facets" in json:
                self.text_content = bsky_link_fix(self.text_content, json["facets"])
            if not self.user_name:
                self.user_name = json["author"]["handle"].lower()
            self.nick = json["author"]["displayName"]
            self.time = json["date"]
            self.url = f"https://bsky.app/profile/{self.user_name}/post/{self.post_id}"
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
                reply_match = re.match(r"at://([^/]+)/app.bsky.feed.post/([^/]+)$", reply_parent.get("uri", ""))
                if reply_match:
                    self.reply_to = f"{reply_match.group(2)}@{reply_match.group(1)}"

        elif self.type == "reddit":
            self.post_id = json["id"]
            self.text_content = (
                f"<span class='rdt_title'>{json['title']}</span>{json['selftext']}"
            )
            self.user_name = json["subreddit"].lower()
            self.nick = self.user_name
            self.time = time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(json["created_utc"])
            )
            self.url = f"https://reddit.com/r/{self.user_name}/comments/{self.post_id}"
            self.likes = json["score"]
            self.reposts = 0
            self.comments = json["num_comments"]
            self.isreply = False
            self.real_user = json["author"]
        elif self.type == "fa":
            self.post_id = str(json["id"])
            self.text_content = (
                f"<span class='rdt_title'>{json['title']}</span>{json['description']}"
            )
            self.user_name = json["user"].lower()
            self.nick = json.get("artist", self.user_name)
            self.time = json["date"]
            if (
                json.get("subcategory") == "journals"
                or json.get("category") == "journals"
            ):
                self.url = f"https://www.furaffinity.net/journal/{self.post_id}/"
            else:
                self.url = f"https://www.furaffinity.net/view/{self.post_id}/"
            self.likes = json.get("favorites", 0)
            self.reposts = 0
            self.comments = json.get("comments", 0)
            self.isreply = False
            self.real_user = self.user_name
        # Set uid after user_name and type are determined
        self.uid = f"{self.user_name}@{self.type}"
        self.save_to_db(db)

    def init_embed(self, db):
        if self.embed:
            if self.type == "bsky":
                *_, embed_udid, _, embed_post_id = self.embed.split("/")
                self.embed_url = (
                    f"https://bsky.app/profile/{embed_udid}/post/{embed_post_id}"
                )
            elif self.type == "x":
                embed_udid, embed_post_id = self.embed.split("/")[-2:]
                self.embed_url = f"https://x.com/{embed_udid}/status/{embed_post_id}"
            embed = Embed(embed_post_id, embed_udid, self.type)
            embed.load_from_db(db)
            self.embed_obj = embed
    
    def concat_url(self):
        if self.type == "x":
            self.url = f"https://x.com/{self.user_name}/status/{self.post_id}"
        elif self.type == "bsky":
            self.url = f"https://bsky.app/profile/{self.user_name}/post/{self.post_id}"
        elif self.type == "reddit":
            self.url = f"https://reddit.com/r/{self.user_name}/comments/{self.post_id}"
        elif self.type == "fa":
            self.url = f"https://www.furaffinity.net/view/{self.post_id}/"


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

    def load_from_db(self, db, ignore_cache=False):
        if self.placeholder:
            return False
        # Query by uid if we have type, otherwise query by user_name
        if self.type:
            rows = db.query_rows("users", "uid", self.uid, ignore_cache)
        else:
            rows = db.query_rows("users", "user_name", self.user_name, ignore_cache)
        if len(rows) == 0:
            return False
        try:
            row = rows[0]
            self.uid = row[0]
            self.user_name = row[1]
            self.udid = row[2]
            self.nick = row[3]
            self.avatar = row[4]
            self.banner = row[5]
            self.description = utils.embed_hyperlink(self.type, row[6])
            self.type = row[7]
            self.update_time = row[8]
            self.flagged = row[9]
            self.concat_url()
            if not self.nick:
                self.nick = self.user_name
            return True
        except Exception as e:
            logger.log(
                f"Error loading user {self.user_name} from database: {e}", type="error"
            )
            logger.log(rows, type="error")
            return False

    def load_from_inline(
        self,
        uid,
        user_name,
        nick,
        udid,
        avatar,
        banner,
        description,
        type,
        update_time,
        flagged,
    ):
        self.uid = uid
        self.user_name = user_name
        self.nick = nick
        self.udid = udid
        self.avatar = avatar
        self.banner = banner
        self.description = description
        self.type = type
        self.update_time = update_time
        self.flagged = flagged
        self.concat_url()
        if not self.nick:
            self.nick = self.user_name

    def save_to_db(self, db):
        if self.placeholder:
            return
        db.insert_or_update_user(
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
        )

    def load_from_json(self, json, db, use_fs_modified_time=False):
        if self.placeholder:
            return
        if self.type == "x":
            self.nick = json["author"]["nick"]
            self.udid = self.user_name
            self.avatar = json["author"]["profile_image"]
            self.banner = ""
            self.description = ""
            try:
                self.banner = json["author"]["profile_banner"]
            except:
                logger.log(
                    f"warning: user {self.user_name} has no banner.\ndownload again with lasest gallery-dl version to fix this.",
                    type="warning",
                )
            try:
                self.description = json["author"]["description"]
            except:
                logger.log(
                    f"warning: user {self.user_name} has nodescription.\ndownload again with lasest gallery-dl version to fix this.",
                    type="warning",
                )
        elif self.type == "bsky":
            self.nick = json["author"]["displayName"]
            self.udid = json["author"]["did"]
            try:
                self.avatar = json["author"]["avatar"]
            except:
                self.avatar = ""
                logger.log(
                    f"warning: user {self.user_name} has no avatar.\ndownload again with lasest gallery-dl version to fix this.",
                    type="error",
                )
            self.banner = ""
            self.description = ""
            try:
                self.banner = json["user"]["banner"]
            except:
                logger.log(
                    f"warning: user {self.user_name} has no banner.", type="error"
                )
            try:
                self.description = json["user"]["description"]
            except:
                logger.log(
                    f"warning: user {self.user_name} has no description.", type="error"
                )
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
        if use_fs_modified_time:
            self.update_time = os.path.getmtime(
                os.path.join(config.fs_bases[self.type], self.user_name)
            )
        else:
            self.update_time = time.time()
        self.concat_url()
        self.save_to_db(db)

    def concat_url(self):
        if self.type == "x":
            self.url = f"https://x.com/{self.user_name}"
        elif self.type == "bsky":
            self.url = f"https://bsky.app/profile/{self.user_name}"
        elif self.type == "reddit":
            self.url = f"https://reddit.com/r/{self.user_name}"
        elif self.type == "fa":
            self.url = f"https://www.furaffinity.net/user/{self.user_name}"

    def get_update_time_str(self):
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.update_time))


class Media:
    def __init__(self, media_id, post_id, user_name, type, time):
        self.media_id = media_id
        self.post_id = post_id
        self.user_name = user_name
        self.type = type
        self.uid = f"{user_name}@{type}" if user_name and type else None
        self.time = time

    def save_to_db(self, db):
        db.insert_or_update_media(
            self.media_id,
            self.post_id,
            self.file_name,
            self.uid,
            self.type,
            self.time,
        )

    def load_from_db(self, db):
        rows = db.query_rows("media", "media_id", self.media_id)
        if len(rows) == 0:
            return False
        row = rows[0]
        try:
            self.post_id = row[1]
            self.file_name = row[2]
            if not self.file_name:
                return False
            self.uid = row[3]
            # Extract user_name and type from uid if not already set
            if self.uid and "@" in self.uid:
                parts = self.uid.rsplit("@", 1)
                if not self.user_name:
                    self.user_name = parts[0]
                if not self.type:
                    self.type = parts[1]
            self.type = row[4]
            self.time = row[5]
            self.isvideo = self.file_name.split(".")[-1] in valid_video_types
            self.isaudio = self.file_name.split(".")[-1] in valid_audio_types
            self.isimage = self.file_name.split(".")[-1] in valid_image_types
            self.isflash = self.file_name.split(".")[-1] in valid_flash_types
            self.isattachment = self.file_name.split(".")[-1] in valid_attachment_types
        except Exception as e:
            logger.log("Error:", e, type="error")
            logger.log("Rows:", rows, type="error")
            return False
        return True


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
def scan_for_users(type, db, user_name=None):
    global all_users
    if user_name == "ignore":
        return
    fs_base = config.fs_bases[type]
    # assume that the user name is the same as the directory name
    if not user_name:
        user_names = os.listdir(fs_base)
    else:
        user_names = [user_name]
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
            logger.log(f"scanning for user {user_name}")
            user = User(user_name, type)
            if not user.load_from_db(db, True) or len(user_names) == 1:
                # user not found in database, create a new entry
                # select the first json file
                if type == "fa":
                    if os.path.exists(os.path.join(fs_base, user_name, "user.json")):
                        json_files = ["user.json"]
                    else:
                        json_files = []
                elif type in ["x", "bsky", "reddit"]:
                    file_list = os.listdir(os.path.join(fs_base, user_name))
                    json_files = [f for f in file_list if f.endswith(".json")]
                    json_files = natsort.natsorted(json_files, reverse=True)
                if len(json_files) > 0:
                    logger.log(f"found user json file: {json_files[0]}")
                    with open(
                        os.path.join(fs_base, user_name, json_files[0]),
                        "r",
                        encoding="utf=8",
                    ) as f:
                        user_json = json.load(f)
                        if len(user_names) == 1:
                            user.load_from_json(user_json, db)
                        else:
                            user.load_from_json(user_json, db, True)
                else:
                    # no json file found, use dummy values
                    user.nick = user_name
                    user.avatar = ""
                    user.banner = ""
                    user.description = ""
                    user.update_time = time.time()
                    user.save_to_db(db)
        except Exception as e:
            logger.log(e, type="error")
            logger.log("Error loading user:", user_name, type="error")
    db.clear_cache()
    all_users = get_users(db)


def scan_for_posts(type, db, user_name=None):
    if user_name == "ignore":
        return
    fs_base = config.fs_bases[type]
    if not user_name:
        user_names = os.listdir(fs_base)
    else:
        user_names = [user_name]
    for cnt, user_name in enumerate(user_names):
        logger.log(
            f"[{cnt+1}/{len(user_names)}] scanning for posts of user {user_name}".ljust(
                90, " "
            )
        )
        sys.stdout.flush()
        filelist = os.listdir(os.path.join(fs_base, user_name))
        json_files = [f for f in filelist if f.endswith(".json")]
        regex_map = {
            "x": {"file_patterns": [r"\d+.+json"], "id_pattern": r"(\d+)"},
            "bsky": {
                "file_patterns": [r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}.+\.json"],
                "id_pattern": r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}_([^_]+).+",
            },
            "reddit": {
                "file_patterns": [r".+json"],
                "id_pattern": r"([a-zA-Z0-9]+)",
            },
            "fa": {
                "file_patterns": [r"\d+"],
                "id_pattern": r"(\d+)",
            },
        }

        patterns = regex_map.get(type)
        if patterns:
            post_files = []
            for pat in patterns["file_patterns"]:
                post_files += [f for f in json_files if re.match(pat, f)]
            for post_file in post_files:
                post_id = re.match(patterns["id_pattern"], post_file).group(1)
                post = Post(post_id, user_name, type)
                if not post.load_from_db(db) or len(user_names) == 1:
                    with open(
                        os.path.join(fs_base, user_name, post_file),
                        "r",
                        encoding="utf=8",
                    ) as f:
                        try:
                            post_json = json.load(f)
                            post.load_from_json(post_json, db)
                        except Exception as e:
                            logger.log(e, type="error")
                            logger.log(
                                "Error loading:",
                                os.path.join(fs_base, user_name, post_file),
                                type="error",
                            )
                            if debug_mode:
                                raise e
    db.commit()
    db.clear_cache()


def scan_for_media(type, db, user_name=None):
    if user_name == "ignore":
        return
    fs_base = config.fs_bases[type]
    if not user_name:
        user_names = os.listdir(fs_base)
    else:
        user_names = [user_name]
    for cnt, user_name in enumerate(user_names):
        logger.log(
            f"[{cnt+1}/{len(user_names)}] scanning for media of user {user_name}".ljust(
                90, " "
            )
        )
        filelist = os.listdir(os.path.join(fs_base, user_name))
        # check if is file
        filelist = [
            f for f in filelist if os.path.isfile(os.path.join(fs_base, user_name, f))
        ]
        media_files = [f for f in filelist if f.split(".")[-1] in valid_media_types]
        for media_file in media_files:
            if type in ["x", "bsky", "reddit"]:
                media_id = media_file.split(".")[0]
                if type == "x":
                    id_pattern = r"(\d+)"
                elif type == "bsky":
                    id_pattern = r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}_([^_]+).+"
                elif type == "reddit":
                    id_pattern = r"([a-zA-Z0-9]+)"
                elif type == "fa":
                    id_pattern = r"(\d+)"
                try:
                    related_post_id = re.match(id_pattern, media_file).group(1)
                    # logger.log("matching post_id from filename:", related_post_id)
                except:
                    related_post_id = "0" + media_file
                    logger.log(
                        "warning: no post_id found in filename:",
                        media_file,
                        type="error",
                    )
                if related_post_id in ["redgifs", "tumblr", "imgur", "gfycat"]:
                    related_post_id = "-1" + user_name + "_" + related_post_id
            elif type == "fa":
                media_id = media_file
                if os.path.exists(
                    os.path.join(fs_base, user_name, media_file + ".json")
                ):
                    with open(
                        os.path.join(fs_base, user_name, media_file + ".json"),
                        "r",
                        encoding="utf=8",
                    ) as f:
                        try:
                            post_json = json.load(f)
                            related_post_id = str(post_json["id"])
                        except Exception as e:
                            logger.log(e)
                            related_post_id = "0" + media_file
                            logger.log(
                                "Error loading:",
                                os.path.join(fs_base, user_name, media_file + ".json"),
                                type="error",
                            )
                            if debug_mode:
                                raise e
                else:
                    related_post_id = "0" + media_file
                    logger.log(
                        "warning: no json file found for media:",
                        os.path.join(fs_base, user_name, media_file + ".json"),
                    )
            else:
                continue
            # test if related post exists
            post = Post(related_post_id, user_name, type)
            if not post.load_from_db(db):
                logger.log(
                    f"warning: media {media_id} has no related post {related_post_id} in database"
                )
                # create a dummy post
                post.text_content = media_file
                post.user_name = user_name
                guessed_timestamp = re.search(r"\d{10}", media_id)
                if guessed_timestamp and time.gmtime() > time.gmtime(
                    int(guessed_timestamp.group(0))
                ):
                    post.time = time.strftime(
                        "%Y-%m-%d %H:%M:%S",
                        time.gmtime(int(guessed_timestamp.group(0))),
                    )
                else:
                    post.time = time.strftime(
                        "%Y-%m-%d %H:%M:%S",
                        time.gmtime(
                            os.path.getmtime(
                                os.path.join(
                                    config.fs_bases[type], user_name, media_file
                                )
                            )
                        ),
                    )
                post.type = type
                if type == "reddit" and re.match(r"[a-zA-Z0-9]{6,8}_\d", media_id):
                    dummy_id = media_id.split("_")[0]
                    post.url = f"https://reddit.com/r/{user_name}/comments/{dummy_id}"
                else:
                    post.url = ""
                post.likes = 0
                post.reposts = 0
                post.comments = 0
                post.save_to_db(db)
            media = Media(media_id, related_post_id, user_name, type, post.time)
            media.file_name = media_file
            if not media.load_from_db(db):
                media.save_to_db(db)
    db.commit()
    db.clear_cache()


def get_users(db):
    rows = db.raw_query("SELECT * FROM users")
    users = []
    for row in rows:
        uid = row[0]
        user_name = row[1]
        # Extract type from uid
        type = uid.split("@")[1] if "@" in uid else row[7]
        user = User(user_name, type)
        user.load_from_inline(
            row[0],
            row[1],
            row[3],
            row[2],
            row[4],
            row[5],
            row[6],
            row[7],
            row[8],
            row[9],
        )
        users.append(user)
    users.sort(key=lambda u: u.update_time, reverse=True)
    return users


def flag_user(db: Database, user_name, type):
    uid = f"{user_name}@{type}"
    # logger.log(f"*********Flagging user {uid}")
    # logger.log(f"UPDATE users SET flagged = 1 WHERE uid = \"{uid}\"")
    db.raw_query(f'UPDATE users SET flagged = 1 WHERE uid = "{uid}"', "main", True)
    db.commit()


all_users = []
cache_all_posts_id = []
cache_all_posts_id_top = []
cache_all_posts_id_random = []
cache_all_media_id = []
cache_user_media_id = dict()
query_cache = dict()
cache_query_media_id = dict()


valid_video_types = set(("mp4", "webm", "m4v"))
valid_audio_types = set(("mp3", "wav", "ogg"))
valid_image_types = set(("jpg", "jpeg", "png", "gif"))
valid_flash_types = set(("swf",))
valid_attachment_types = set(("pdf", "epub", "txt", "doc", "docx"))
valid_media_types = (
    valid_video_types.union(valid_audio_types)
    .union(valid_image_types)
    .union(valid_flash_types)
    .union(valid_attachment_types)
)


def build_cache(db: Database):
    global cache_all_posts_id, cache_all_media_id, cache_user_media_id, cache_all_posts_id_top, cache_all_posts_id_random, query_cache

    # debug, skip cache building
    # if debug_mode:
    #     return

    rows = db.raw_query("SELECT post_id,type,time,likes FROM posts")
    cache_all_posts_id = []
    for row in rows:
        post_id = row[0]
        post_time = row[2]
        cache_all_posts_id.append((post_id, post_time))
    cache_all_posts_id = natsort.natsorted(
        cache_all_posts_id, key=lambda p: p[1], reverse=True
    )
    cache_all_posts_id_top = []
    for row in rows:
        post_id = row[0]
        post_likes = row[3]
        cache_all_posts_id_top.append((post_id, post_likes))
    cache_all_posts_id_top = natsort.natsorted(
        cache_all_posts_id_top, key=lambda p: p[1], reverse=True
    )
    cache_all_posts_id_random = list(cache_all_posts_id)
    random.shuffle(cache_all_posts_id_random)
    # get media that ends with .mp4
    rows = db.raw_query(
        "SELECT media_id FROM media WHERE file_name LIKE '%.mp4' OR file_name LIKE '%.webm' OR file_name LIKE '%.m4v'"
    )
    cache_all_media_id = []
    for row in rows:
        cache_all_media_id.append(row[0])
    random.shuffle(cache_all_media_id)
    cache_user_media_id = dict()
    query_cache = dict()


def get_fav(db: Database):
    return db.query_rows(selected_db="fav", key="", value="", ignore_cache=True)


def add_favorite(db: Database, post_id):
    if not db.query_rows("posts", "post_id", post_id):
        return
    db.raw_query(
        f"INSERT OR REPLACE INTO fav VALUES ('{post_id}', '{time.ctime()}')",
        "fav",
        True,
    )
    db.commit()


def remove_favorite(db: Database, post_id):
    db.raw_query(f"DELETE FROM fav WHERE post_id = '{post_id}'", "fav", True)
    db.commit()


if not os.path.exists(config.fs_bases["x"]):
    os.makedirs(config.fs_bases["x"])
if not os.path.exists(config.fs_bases["bsky"]):
    os.makedirs(config.fs_bases["bsky"])

if __name__ == "__main__":
    db = Database("test.db")
    db.prepare_db()
    scan_for_users("x", db)
    scan_for_posts("x", db)
    scan_for_media("x", db)
    db.conn.close()
