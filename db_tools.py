#!/usr/bin/env python3

import config

config.read_config()

import os, re, sqlite3, shutil, time


sqlite_file = "./data.db"


def remove_legacy_json():
    for folder in os.listdir(config.fs_bases["x"]):
        folder_path = os.path.join(config.fs_bases["x"], folder)
        if not os.path.isdir(folder_path):
            continue
        for file in os.listdir(folder_path):
            match = re.search(r"(_\d\.\w{3})\.json", file)
            if match:
                new_json = file.replace(match.group(1), "")
                legacy_file = os.path.join(folder_path, file)
                new_file = os.path.join(folder_path, new_json)
                if os.path.exists(new_file):
                    print(
                        "File already exists: "
                        + new_json
                        + " so safe to remove "
                        + file
                    )
                    print(legacy_file + " -> " + new_file)
                    os.remove(legacy_file)
        if os.path.exists(os.path.join(folder_path, "info.json")):
            os.remove(os.path.join(folder_path, "info.json"))
    for folder in os.listdir(config.fs_bases["bsky"]):
        folder_path = os.path.join(config.fs_bases["bsky"], folder)
        if not os.path.isdir(folder_path):
            continue
        for file in os.listdir(folder_path):
            match = re.search(r"(_\d\.\w{3})\.json", file)
            if match:
                new_json = file.replace(match.group(1), "")
                legacy_file = os.path.join(folder_path, file)
                new_file = os.path.join(folder_path, new_json)
                if os.path.exists(new_file):
                    print(
                        "File already exists: "
                        + new_json
                        + " so safe to remove "
                        + file
                    )
                    print(legacy_file + " -> " + new_file)
                    os.remove(legacy_file)
        if os.path.exists(os.path.join(folder_path, "info.json")):
            os.remove(os.path.join(folder_path, "info.json"))
    for folder in os.listdir(config.fs_bases["patreon"]):
        folder_path = os.path.join(config.fs_bases["patreon"], folder)
        if not os.path.isdir(folder_path):
            continue
        for file in os.listdir(folder_path):
            match = re.search(r"(\d+)_", file)
            if match and file.endswith(".json"):
                legacy_file = os.path.join(folder_path, file)
                new_json = f"{match.group(1)}.json"
                new_file = os.path.join(folder_path, new_json)
                if os.path.exists(new_file) and new_file != legacy_file:
                    print(
                        "File already exists: "
                        + new_json
                        + " so safe to remove "
                        + file
                    )
                    print(legacy_file + " -> " + new_file)
                    os.remove(legacy_file)
        if os.path.exists(os.path.join(folder_path, "info.json")):
            os.remove(os.path.join(folder_path, "info.json"))


def drop_table_users():
    if input("drop users table?[y/n]") == "y":
        table_name_to_drop = "users"
        conn = sqlite3.connect(sqlite_file)
        cursor = conn.cursor()
        cursor.execute(f"DROP TABLE IF EXISTS {table_name_to_drop}")
        conn.commit()
        conn.close()


missing_users = set()
duplicated_users = []


def sanity_check():
    global missing_users, duplicated_users
    suggestions = set()
    duplicated_users = set()
    print("Starting sanity check...")
    conn = sqlite3.connect(sqlite_file)
    cursor = conn.cursor()
    users = set()
    cursor.execute("SELECT uid,user_name,type FROM users")
    for row in cursor.fetchall():
        users.add((row[0], row[1], row[2]))
    cursor.execute("SELECT uid,type FROM media")
    for row in cursor.fetchall():
        uid = row[0]
        type_ = row[1]
        user_name = uid.split("@")[0] if "@" in uid else uid
        users.add((uid, user_name, type_))
    cursor.execute("SELECT uid,type FROM posts")
    for row in cursor.fetchall():
        uid = row[0]
        type_ = row[1]
        user_name = uid.split("@")[0] if "@" in uid else uid
        users.add((uid, user_name, type_))
    conn.close()
    files_list = dict()
    for uid, user, type_ in list(users):
        if not type_:
            print(f"There is a entry with empty type: {uid}, skipping.")
            continue
        user_fs_base = os.path.join(config.fs_bases[type_], user)
        if not os.path.exists(user_fs_base):
            suggestions.add(
                f"UID {uid} no longer exsists in filesystem. Try remove_user()."
            )
            missing_users.add(uid)
            continue
        elif user_fs_base != user_fs_base.lower():
            suggestions.add(
                f"UID {uid} has inconsistent casing in filesystem. Try fs_format_fix()."
            )
            continue
        if type_ == "reddit":
            # skip reddit users
            continue
        for file in os.listdir(user_fs_base):
            if file in files_list:
                files_list[file].append(uid)
            else:
                files_list[file] = [uid]
    for file, users_with_file in files_list.items():
        if file in [
            "info.json",
            "avatar",
            "banner",
            "avatar_bck",
            "banner_bck",
            "user.json",
            "extend.txt",
            "profile.json"
        ]:
            continue
        if not file.split(".")[-1] in [
            "jpg",
            "jpeg",
            "png",
            "gif",
            "mp4",
            "webm",
            "mov",
            "mp3",
            "wav",
            "flac",
            "m4a",
            "json",
        ]:
            continue
        if len(users_with_file) > 1:
            # print(f"File {file} is shared by {users_with_file}")
            if not tuple(users_with_file) in duplicated_users:
                types = [ uid.split("@")[1] if "@" in uid else "unknown" for uid in users_with_file]
                if len(set(types)) > 1:
                    print(f"File {file} is shared by users from different sources: {users_with_file}, skipping suggestion.")
                    continue
                if not types[0] in ["x", "bsky"]:
                    continue
                suggestions.add(
                    f"{' '.join(users_with_file)} may be the same user. Try user_rename(). Overlapping file: {file}"
                )
                duplicated_users.add(tuple(users_with_file))
    duplicated_users = list(set(duplicated_users))
    print("Sanity check done.")
    if suggestions:
        print("Suggestions:")
        for suggestion in suggestions:
            print(suggestion)


def remove_user():
    global missing_users
    if missing_users:
        print("Missing users from previous run:")
        for uid in missing_users:
            print(uid)
        if input("Do you want to remove these users?[y/n]>>") != "y":
            return
    else:
        uid = input("uid (format: user_name@source)>>")
        uid = uid.lower().strip()
        if len(uid) < 2 or "@" not in uid:
            print("Not allowed. Must be in format user_name@source")
            return
        missing_users = set()
        missing_users.add(uid)
    for uid in missing_users:
        sql1 = f'DELETE FROM posts WHERE uid = "{uid}"'
        sql2 = f'DELETE FROM media WHERE uid = "{uid}"'
        sql3 = f'DELETE FROM users WHERE uid = "{uid}"'
        sql4 = f'DELETE FROM user_group WHERE uid = "{uid}"'
        # if input(f"{sql1}\n{sql2}\n{sql3}\nSure?[y/n]>>") == "y":
        if 1:
            conn = sqlite3.connect(sqlite_file)
            cursor = conn.cursor()
            cursor.execute(sql1)
            cursor.execute(sql2)
            cursor.execute(sql3)
            cursor.execute(sql4)
            conn.commit()
            conn.close()
    missing_users = set()


def rn_user(id_, to_id_):
    # Extract user_name and type from old and new ids
    if "@" not in id_ or "@" not in to_id_:
        print("Both IDs must be in format user_name@source")
        return

    old_user_name, old_type = id_.rsplit("@", 1)
    new_user_name, new_type = to_id_.rsplit("@", 1)

    if old_type != new_type:
        print("Cannot rename users across different sources")
        return

    type_ = old_type

    if input(f"Rename {id_} to {to_id_}?[y/n]>>") == "y":

        if not os.path.exists(os.path.join(config.fs_bases[type_], old_user_name)):
            print(f"UID {id_} no longer exsists in filesystem.")
            return
        # move os.path.join(config.fs_bases[type_],old_user_name) to os.path.join(config.fs_bases[type_],new_user_name)
        # if os.path.join(config.fs_bases[type_],new_user_name) exists, move all files in os.path.join(config.fs_bases[type_],old_user_name) to os.path.join(config.fs_bases[type_],new_user_name)
        if os.path.exists(os.path.join(config.fs_bases[type_], new_user_name)):
            for file in os.listdir(os.path.join(config.fs_bases[type_], old_user_name)):
                # skip exsisitng files
                if os.path.exists(
                    os.path.join(config.fs_bases[type_], new_user_name, file)
                ):
                    continue
                os.rename(
                    os.path.join(config.fs_bases[type_], old_user_name, file),
                    os.path.join(config.fs_bases[type_], new_user_name, file),
                )
            shutil.rmtree(os.path.join(config.fs_bases[type_], old_user_name))
        else:
            os.rename(
                os.path.join(config.fs_bases[type_], old_user_name),
                os.path.join(config.fs_bases[type_], new_user_name),
            )

        conn = sqlite3.connect(sqlite_file)
        cursor = conn.cursor()
        try:
            cursor.execute(f'UPDATE posts SET uid = "{to_id_}" WHERE uid = "{id_}"')
        except Exception as e:
            print(e)
        try:
            cursor.execute(f'UPDATE media SET uid = "{to_id_}" WHERE uid = "{id_}"')
        except Exception as e:
            print(e)
            pass
        try:
            cursor.execute(
                f'UPDATE users SET uid = "{to_id_}", user_name = "{new_user_name}" WHERE uid = "{id_}"'
            )
        except Exception as e:
            print(e)
            if "UNIQUE constraint failed" in str(e):
                print(f"Trying to delete {id_} from users table.")
                cursor.execute(f'DELETE FROM users WHERE uid = "{id_}"')
                print("Done.")
            pass
        # set flagged to 0 for the user
        try:
            cursor.execute(f'UPDATE users SET flagged = 0 WHERE uid = "{to_id_}"')
        except Exception as e:
            print(e)
            pass
        conn.commit()
        conn.close()


def user_rename():
    global duplicated_users
    print("Rename user.")
    print(
        "Figure out the user's latest id first, search on x.com or bsky.social to see which one currently exsits."
    )
    print("Format should be: user_name@source (e.g., alice@bsky, bob@x)")
    if len(duplicated_users) == 0:
        id_ = input("from_id (format: user_name@source)>>").lower().strip()
        to_id_ = input("to_id (format: user_name@source)>>").lower().strip()
        if "@" not in id_ or "@" not in to_id_:
            print("Both IDs must be in format user_name@source")
            return
        rn_user(id_, to_id_)
    else:
        while len(duplicated_users) > 0:
            print("Which one is the old one?")
            print("[1]", duplicated_users[0][0])
            print("[2]", duplicated_users[0][1])
            choice = input("[1/2]>>")
            if choice == "1":
                old_user = duplicated_users[0][0]
                new_user = duplicated_users[0][1]
            elif choice == "2":
                old_user = duplicated_users[0][1]
                new_user = duplicated_users[0][0]
            else:
                print("Invalid choice.")
                duplicated_users.pop(0)
                continue

            # Extract type from uid if it contains @
            if "@" in old_user and "@" in new_user:
                old_type = old_user.rsplit("@", 1)[1]
                new_type = new_user.rsplit("@", 1)[1]
                if old_type == new_type:
                    # Same type, can proceed directly
                    id_ = old_user if "@" in old_user else f"{old_user}@{old_type}"
                    to_id_ = new_user if "@" in new_user else f"{new_user}@{new_type}"
                    rn_user(id_, to_id_)
                    duplicated_users.pop(0)
                    continue

            # If types are different or missing, prompt for type
            type_ = (
                input(f"Enter source type for these users (x/bsky/reddit/fa)>>")
                .strip()
                .lower()
            )
            if type_ not in ["x", "bsky", "reddit", "fa"]:
                print("Invalid type")
                duplicated_users.pop(0)
                continue

            id_ = f"{old_user}@{type_}" if "@" not in old_user else old_user
            to_id_ = f"{new_user}@{type_}" if "@" not in new_user else new_user
            rn_user(id_, to_id_)
            duplicated_users.pop(0)


def sql_console():
    conn = sqlite3.connect(sqlite_file)
    cursor = conn.cursor()
    # sql = "DELETE FROM posts WHERE type = 'reddit'"
    while True:
        try:
            sql = input("sql>>")
            if sql == "exit":
                break
            try:
                cursor.execute(sql)
                print(cursor.fetchall())
            except Exception as e:
                print(e)
        except KeyboardInterrupt:
            break
        conn.commit()
    conn.close()


def external_vid_fix():
    print(
        "This will try to download all external videos of posts, which gallery-dl won't. Only twitter/x has this issue."
    )
    print(
        "This will take a while, so be patient. If the download keeps failing, you are rate limited, wait and try again later."
    )
    print("Using yt-dlp to download videos. Make sure yt-dlp is installed.")
    user_id = input("user_id(twitter/x) >>")
    if "bsky" in user_id:
        print("This is a bsky user, so no need to download videos.")
        return
    json_list = os.listdir(os.path.join(config.fs_bases["x"], user_id))
    json_list.sort(reverse=True)
    for json_file in json_list:
        if not json_file.endswith(".json"):
            continue
        post_id = re.match("(\d+)", json_file)
        if not post_id:
            continue

        post_id = post_id.group(1)
        if (
            os.path.exists(
                os.path.join(config.fs_bases["x"], user_id, f"{post_id}.mp4")
            )
            or os.path.exists(
                os.path.join(config.fs_bases["x"], user_id, f"{post_id}_1.mp4")
            )
            or os.path.exists(
                os.path.join(config.fs_bases["x"], user_id, f"{post_id}.jpg")
            )
            or os.path.exists(
                os.path.join(config.fs_bases["x"], user_id, f"{post_id}_1.jpg")
            )
        ):
            print(f"Post {post_id}'s media already exists, skipping.")
            continue

        time.sleep(1)

        post_url = f"https://x.com/{user_id}/status/{post_id}"
        video_file_name = os.path.join(
            config.fs_bases["x"], user_id, f"{post_id}.%(ext)s"
        )
        cmd = [
            "yt-dlp",
            post_url,
            "-o",
            f'"{video_file_name}"',
            "--cookies",
            config.cookies_list["x"],
        ]
        print(
            f"Downloading {post_url} to {video_file_name}, pay attention to the output of yt-dlp."
        )
        print(" ".join(cmd))
        os.system(" ".join(cmd))
        print("Done.")


def remove_deleted_media():
    print(
        "This will remove all media database entries that do not have a corresponding file in the filesystem."
    )
    conn = sqlite3.connect(sqlite_file)
    cursor = conn.cursor()
    cursor.execute("SELECT media_id, file_name, uid, type FROM media")
    media_files = cursor.fetchall()
    for i, (media_id, file_name, uid, type_) in enumerate(media_files):
        user_name = uid.split("@")[0] if "@" in uid else uid
        if not file_name:
            print("What? Empty row??", (media_id, file_name, user_name, type_))
            file_name = "None"
        file_path = os.path.join(config.fs_bases[type_], user_name, file_name)
        if i % 100 == 0:
            print(f"Checking {i+1}/{len(media_files)}", end="\r", flush=True)
        if not os.path.exists(file_path):
            print(f"Removing {file_path} from database, file not found.")
            cursor.execute(
                "DELETE FROM media WHERE media_id = ? AND type = ?", (media_id, type_)
            )
    conn.commit()
    conn.close()


def create_avatar_and_banner_backup():
    print("Creating avatar and banner backups for all users.")
    for type_ in config.fs_bases:
        for user in os.listdir(config.fs_bases[type_]):
            user_path = os.path.join(config.fs_bases[type_], user)
            if not os.path.isdir(user_path):
                continue
            avatar_path = os.path.join(user_path, "avatar")
            banner_path = os.path.join(user_path, "banner")
            if os.path.exists(avatar_path) and os.path.getsize(avatar_path) > 100:
                shutil.copy(avatar_path, os.path.join(user_path, "avatar_bck"))
            if os.path.exists(banner_path) and os.path.getsize(banner_path) > 100:
                shutil.copy(banner_path, os.path.join(user_path, "banner_bck"))
    print("Done creating backups.")


def remove_avatar_and_banner():
    print("Removing avatar and banner, so new ones can be downloaded.")
    print("This will remove avatar and banner only if backup exists.")
    for type_ in config.fs_bases:
        for user in os.listdir(config.fs_bases[type_]):
            user_path = os.path.join(config.fs_bases[type_], user)
            if not os.path.isdir(user_path):
                continue
            avatar_path = os.path.join(user_path, "avatar")
            banner_path = os.path.join(user_path, "banner")
            avatar_bck_path = os.path.join(user_path, "avatar_bck")
            banner_bck_path = os.path.join(user_path, "banner_bck")
            # Remove backup files if their size is less than 100 bytes
            if (
                os.path.exists(avatar_bck_path)
                and os.path.getsize(avatar_bck_path) < 100
            ):
                print(f"Removing backup avatar {avatar_bck_path} as it is too small.")
                os.remove(avatar_bck_path)
            if (
                os.path.exists(banner_bck_path)
                and os.path.getsize(banner_bck_path) < 100
            ):
                print(f"Removing backup banner {banner_bck_path} as it is too small.")
                os.remove(banner_bck_path)
            # Remove avatar and banner if backup exists
            if os.path.exists(avatar_bck_path) and os.path.exists(avatar_path):
                print(f"Removing {avatar_path}")
                os.remove(avatar_path)
            if os.path.exists(banner_bck_path) and os.path.exists(banner_path):
                print(f"Removing {banner_path}")
                os.remove(banner_path)


def remove_empty_files():
    print("Removing empty files in the filesystem.")
    for type_ in config.fs_bases:
        for user in os.listdir(config.fs_bases[type_]):
            user_path = os.path.join(config.fs_bases[type_], user)
            if not os.path.isdir(user_path):
                continue
            for file in os.listdir(user_path):
                file_path = os.path.join(user_path, file)
                if os.path.isfile(file_path) and os.path.getsize(file_path) < 10:
                    print(f"Removing empty file: {file_path}")
                    os.remove(file_path)


def delete_site():
    print(
        "Delete all data for a specific site from the database. Files in filesystem are not affected."
    )
    sites_to_choose = ["x", "bsky", "reddit", "fa", "patreon"]
    print("Select site to delete all data:")
    for i, site in enumerate(sites_to_choose):
        print(f"[{i}] {site}")
    choice = input(">> ")
    if choice not in [str(i) for i in range(len(sites_to_choose))]:
        print("Invalid choice.")
        return
    site = sites_to_choose[int(choice)]
    if input(f"Are you sure you want to delete all data for {site}? [y/n] >>") != "y":
        print("Aborting.")
        return
    print(f'DELETE FROM posts WHERE type = "{site}"')
    print(f'DELETE FROM media WHERE type = "{site}"')
    print(f'DELETE FROM users WHERE type = "{site}"')
    if input("Confirm? [y/n] >>") != "y":
        print("Aborting.")
        return
    # Delete from database
    conn = sqlite3.connect(sqlite_file)
    cursor = conn.cursor()
    cursor.execute(f'DELETE FROM posts WHERE type = "{site}"')
    cursor.execute(f'DELETE FROM media WHERE type = "{site}"')
    cursor.execute(f'DELETE FROM users WHERE type = "{site}"')
    conn.commit()
    conn.close()


def fs_format_fix():
    print("Fix filesystem casing issues for user directories.")
    types_ = list(config.fs_bases.keys())
    for type_ in types_:
        for user in os.listdir(config.fs_bases[type_]):
            user_path = os.path.join(config.fs_bases[type_], user)
            if not os.path.isdir(user_path):
                continue
            correct_user = user.lower()
            if user != correct_user:
                correct_user_path = os.path.join(config.fs_bases[type_], correct_user)
                if os.path.exists(correct_user_path):
                    print(f"Merging {user_path} into {correct_user_path}")
                    for file in os.listdir(user_path):
                        src_file = os.path.join(user_path, file)
                        dst_file = os.path.join(correct_user_path, file)
                        if not os.path.exists(dst_file):
                            os.rename(src_file, dst_file)
                    shutil.rmtree(user_path)
                else:
                    print(f"Renaming {user_path} to {correct_user_path}")
                    os.rename(user_path, correct_user_path)
    print("Filesystem format fix completed.")


# FTS5 sync functions for posts_fts table


def check_fts_table_exists(cursor):
    """Check if the posts_fts table exists."""
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='posts_fts'"
    )
    return cursor.fetchone() is not None


def drop_fts_table(cursor):
    """Drop the existing posts_fts table and triggers."""
    cursor.execute("DROP TABLE IF EXISTS posts_fts")
    cursor.execute("DROP TRIGGER IF EXISTS posts_ai")
    cursor.execute("DROP TRIGGER IF EXISTS posts_ad")
    cursor.execute("DROP TRIGGER IF EXISTS posts_au")
    print("Dropped existing posts_fts table and triggers.")


def create_fts_table(cursor):
    """Create the FTS5 virtual table if it doesn't exist.

    The rowid of posts_fts is synchronized with the posts table's rowid.
    This ensures uniqueness without needing slow EXISTS checks in triggers.
    """
    cursor.execute(
        """CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts USING fts5(
            post_id,
            uid,
            nick,
            real_user,
            text_content,
            alt
        )"""
    )
    print("Created posts_fts table.")


def create_fts_triggers(cursor):
    """Create triggers to keep FTS table in sync.

    Uses idmap.numid as rowid for posts_fts to ensure uniqueness.
    This eliminates slow EXISTS checks - SQLite's rowid uniqueness handles deduplication.

    The idmap table provides a numeric ID for each post_id, which is used
    as the FTS5 docid (rowid) for efficient synchronization.
    """
    triggers = [
        """CREATE TRIGGER IF NOT EXISTS posts_ai AFTER INSERT ON posts BEGIN
            INSERT INTO posts_fts(rowid, post_id, uid, nick, real_user, text_content, alt)
            SELECT (SELECT numid FROM idmap WHERE post_id = new.post_id), new.post_id, new.uid, new.nick, COALESCE(new.real_user, ''), new.text_content, COALESCE(new.alt, '');
        END""",
        """CREATE TRIGGER IF NOT EXISTS posts_ad AFTER DELETE ON posts BEGIN
            DELETE FROM posts_fts WHERE rowid = (SELECT numid FROM idmap WHERE post_id = old.post_id);
        END""",
        """CREATE TRIGGER IF NOT EXISTS posts_au AFTER UPDATE ON posts BEGIN
            INSERT OR REPLACE INTO posts_fts(rowid, post_id, uid, nick, real_user, text_content, alt)
            SELECT (SELECT numid FROM idmap WHERE post_id = new.post_id), new.post_id, new.uid, new.nick, COALESCE(new.real_user, ''), new.text_content, COALESCE(new.alt, '');
        END""",
    ]
    for trigger in triggers:
        cursor.execute(trigger)
    print("Created FTS sync triggers.")


def rebuild_fts_table():
    """Rebuild the FTS table from scratch by dropping and recreating.

    Uses idmap.numid as rowid for posts_fts, ensuring uniqueness
    and fast lookups without slow EXISTS checks.
    """
    print("Rebuilding FTS table...")
    conn = sqlite3.connect(sqlite_file)
    cursor = conn.cursor()

    # Drop and recreate to handle schema changes
    drop_fts_table(cursor)
    create_fts_table(cursor)
    create_fts_triggers(cursor)

    # Create idmap
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS idmap (
            numid INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id TEXT,
            UNIQUE(post_id)
        )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idmap_post_id ON idmap(post_id)")

    # Ensure idmap is populated for all posts
    cursor.execute(
        """
        INSERT OR IGNORE INTO idmap(post_id)
        SELECT post_id FROM posts
    """
    )

    cursor.execute(
        """
        INSERT INTO posts_fts(rowid, post_id, uid, nick, real_user, text_content, alt)
        SELECT im.numid, p.post_id, p.uid, p.nick, COALESCE(p.real_user, ''), p.text_content, COALESCE(p.alt, '')
        FROM posts p
        JOIN idmap im ON p.post_id = im.post_id
    """
    )
    conn.commit()
    conn.close()
    print("FTS table rebuilt.")


def sync_fts_incremental():
    """Sync FTS table incrementally by checking for missing entries.

    Uses idmap.numid as rowid for posts_fts.
    """
    conn = sqlite3.connect(sqlite_file)
    cursor = conn.cursor()

    # Check if table needs recreation due to schema change
    if check_fts_table_exists(cursor):
        cursor.execute("PRAGMA table_info(posts_fts)")
        columns = cursor.fetchall()
        expected_columns = [
            "post_id",
            "uid",
            "nick",
            "real_user",
            "text_content",
            "alt",
        ]
        current_columns = [col[1] for col in columns]
        if current_columns != expected_columns:
            print("Schema mismatch detected. Recreating FTS table...")
            drop_fts_table(cursor)
            create_fts_table(cursor)
            create_fts_triggers(cursor)
            conn.commit()

    if not check_fts_table_exists(cursor):
        print("FTS table does not exist. Creating...")
        create_fts_table(cursor)
        create_fts_triggers(cursor)
        conn.commit()

    # Ensure idmap is populated for all posts
    cursor.execute(
        """
        INSERT OR IGNORE INTO idmap(post_id)
        SELECT post_id FROM posts
    """
    )
    conn.commit()

    # Get count of posts
    cursor.execute("SELECT COUNT(*) FROM posts")
    posts_count = cursor.fetchone()[0]

    # Get count of FTS entries
    cursor.execute("SELECT COUNT(*) FROM posts_fts")
    fts_count = cursor.fetchone()[0]

    print(f"Posts table has {posts_count} entries.")
    print(f"FTS table has {fts_count} entries.")

    if posts_count == fts_count:
        print("Counts match. FTS table appears to be in sync.")
        conn.close()
        return 0

    print(f"Count mismatch. Syncing missing entries...")

    # Find posts not in FTS table using idmap.numid
    cursor.execute(
        """
        SELECT im.numid, p.post_id, p.uid, p.nick, COALESCE(p.real_user, ''), p.text_content, COALESCE(p.alt, '')
        FROM posts p
        JOIN idmap im ON p.post_id = im.post_id
        LEFT JOIN posts_fts fts ON im.numid = fts.rowid
        WHERE fts.rowid IS NULL
    """
    )

    missing_posts = cursor.fetchall()
    synced = 0
    for row in missing_posts:
        numid, post_id, uid, nick, real_user, text_content, alt = row
        try:
            cursor.execute(
                "INSERT INTO posts_fts(rowid, post_id, uid, nick, real_user, text_content, alt) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (numid, post_id, uid, nick, real_user, text_content, alt),
            )
            synced += 1
            if synced % 1000 == 0:
                print(f"  Synced {synced} entries...")
        except sqlite3.IntegrityError as e:
            print(f"  Warning: Could not sync post {post_id}: {e}")

    conn.commit()
    conn.close()
    print(f"Synced {synced} entries.")
    return synced


def verify_fts():
    """Verify FTS table is working correctly."""
    print("\nVerifying FTS table...")
    conn = sqlite3.connect(sqlite_file)
    cursor = conn.cursor()

    # Test a simple query
    cursor.execute("SELECT COUNT(*) FROM posts_fts")
    fts_count = cursor.fetchone()[0]
    print(f"  FTS table contains {fts_count} entries.")

    # Check for duplicates
    cursor.execute("SELECT COUNT(*) FROM posts")
    posts_count = cursor.fetchone()[0]
    print(f"  Posts table contains {posts_count} entries.")

    if fts_count != posts_count:
        print(
            f"  WARNING: Count mismatch! FTS has {fts_count - posts_count} extra/missing entries."
        )

    # Check for duplicate post_ids in FTS
    cursor.execute(
        "SELECT post_id, COUNT(*) as cnt FROM posts_fts GROUP BY post_id HAVING cnt > 1"
    )
    duplicates = cursor.fetchall()
    if duplicates:
        print(f"  WARNING: Found {len(duplicates)} duplicate post_ids in FTS table!")
        for dup in duplicates[:5]:
            print(f"    post_id={dup[0]}, count={dup[1]}")
        if len(duplicates) > 5:
            print(f"    ... and {len(duplicates) - 5} more")

    # Test search functionality
    try:
        cursor.execute("SELECT COUNT(*) FROM posts_fts WHERE posts_fts MATCH 'test'")
        match_count = cursor.fetchone()[0]
        print(f"  Full-text search is working ({match_count} entries match 'test').")
    except Exception as e:
        print(f"  Warning: Search test failed: {e}")

    # Check triggers exist
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'posts_%'"
    )
    triggers = cursor.fetchall()
    print(f"  Found {len(triggers)} sync triggers: {[t[0] for t in triggers]}")

    conn.close()
    print("Verification complete.")


def fix_fts_duplicates():
    """Fix duplicate entries in the FTS table by rebuilding it.

    Uses idmap.numid as rowid for posts_fts to ensure uniqueness.
    """
    print("Fixing duplicate entries in FTS table...")
    conn = sqlite3.connect(sqlite_file)
    cursor = conn.cursor()

    # Check for duplicates first
    cursor.execute(
        "SELECT post_id, COUNT(*) as cnt FROM posts_fts GROUP BY post_id HAVING cnt > 1"
    )
    duplicates = cursor.fetchall()
    if duplicates:
        print(f"Found {len(duplicates)} duplicate post_ids in FTS table.")

    # Drop and recreate to fix duplicates
    drop_fts_table(cursor)
    create_fts_table(cursor)
    create_fts_triggers(cursor)

    # Ensure idmap is populated
    cursor.execute(
        """
        INSERT OR IGNORE INTO idmap(post_id)
        SELECT post_id FROM posts
    """
    )
    conn.commit()

    # Repopulate from posts table using idmap.numid for uniqueness
    cursor.execute(
        """
        INSERT INTO posts_fts(rowid, post_id, uid, nick, real_user, text_content, alt)
        SELECT im.numid, p.post_id, p.uid, p.nick, COALESCE(p.real_user, ''), p.text_content, COALESCE(p.alt, '')
        FROM posts p
        JOIN idmap im ON p.post_id = im.post_id
    """
    )

    conn.commit()

    # Verify the fix
    cursor.execute("SELECT COUNT(*) FROM posts_fts")
    fts_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM posts")
    posts_count = cursor.fetchone()[0]

    print(f"FTS table now contains {fts_count} entries (posts table: {posts_count}).")

    if fts_count == posts_count:
        print("SUCCESS: FTS table is now in sync with posts table.")
    else:
        print(
            f"WARNING: Count mismatch remains. FTS: {fts_count}, Posts: {posts_count}"
        )

    conn.close()


if __name__ == "__main__":
    while True:
        choice = input(
            "[0] sanity_check()\n[1] remove_legacy_json()\n[2] drop_table_users()\n[3] remove_user()\n[4] user_rename()\n[5] sql_console()\n[6] external_vid_fix()\n[7] remove_deleted_media()\n[8] create_avatar_and_banner_backup()\n[9] remove_avatar_and_banner()\n[10] remove_empty_files()\n[11] delete_site()\n[12] fs_format_fix()\n[13] rebuild_fts_table()\n[14] sync_fts_incremental()\n[15] verify_fts()\n[16] fix_fts_duplicates()\n >> "
        )
        if choice == "0":
            sanity_check()
        if choice == "1":
            remove_legacy_json()
        elif choice == "2":
            drop_table_users()
        elif choice == "3":
            remove_user()
        elif choice == "4":
            user_rename()
        elif choice == "5":
            sql_console()
        elif choice == "6":
            external_vid_fix()
        elif choice == "7":
            remove_deleted_media()
        elif choice == "8":
            create_avatar_and_banner_backup()
        elif choice == "9":
            remove_avatar_and_banner()
        elif choice == "10":
            remove_empty_files()
        elif choice == "11":
            delete_site()
        elif choice == "12":
            fs_format_fix()
        elif choice == "13":
            rebuild_fts_table()
        elif choice == "14":
            sync_fts_incremental()
        elif choice == "15":
            verify_fts()
        elif choice == "16":
            fix_fts_duplicates()
