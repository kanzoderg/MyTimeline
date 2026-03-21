import os, re, requests, time, json
from threading import Thread
from bs4 import BeautifulSoup as BS

import config, logger, utils, backend


class XTimelineMonitor:
    def __init__(self):
        self.ok = False
        logger.log("X timeline monitor is not implemented yet.", "warning")

    def start(self):
        pass


class BlueskyTimelineMonitor:
    def __init__(self):
        self.ok = False
        self.access_token = ""
        self.session_api = "https://bsky.social/xrpc/com.atproto.server.createSession"
        self.api_url = "https://bsky.social/xrpc/app.bsky.feed.getTimeline"
        self.login()
        self.monitor_thread = None

    def login(self):
        if os.path.isfile(config.auth_files["bsky"]):
            with open(config.auth_files["bsky"], "r") as f:
                auth_info = f.read()
                auth_info = json.loads(auth_info)
            try:
                auth_payload = {
                    "identifier": auth_info["username"],
                    "password": auth_info["password"],
                }
                logger.log("Logging in to Bluesky...")
                print(auth_payload)
                response = utils.post(self.session_api, json=auth_payload)
                if response.status_code == 200:
                    self.access_token = response.json().get("accessJwt", "")
                    if self.access_token:
                        self.ok = True
                        logger.log("Successfully logged in to Bluesky.")
                    else:
                        logger.log(
                            "Failed to retrieve access token from Bluesky response.",
                            type="error",
                        )
                else:
                    logger.log(
                        f"Failed to login to Bluesky, status code: {response.status_code}",
                        type="error",
                    )
                    print(response.text)
            except Exception as e:
                logger.log(f"Failed to login to Bluesky: {e}", type="error")
        else:
            logger.log(
                "Bluesky auth file not found, skipping Bluesky timeline monitoring.",
                type="warning",
            )

    def start(self):
        def monitor_thread(monitor):
            while True:
                users_in_tl = monitor.get_timeline_users()
                logger.log(f"Bluesky timeline users: {users_in_tl}", verbose=2)
                users_in_local = backend.get_all_usernames("bsky")
                logger.log(f"Local Bluesky users: {users_in_local}", verbose=2)
                users_to_update = utils.list_and(users_in_tl, users_in_local)
                if users_to_update:
                    logger.log(f"Users to update: {users_to_update}", type="info")
                    for user in users_to_update:
                        url = f"https://bsky.app/profile/{user}"
                        utils.download_jobs.append((url, False, False, utils.TYPE_DOWNLOAD))
                else:
                    logger.log("No new users to update from Bluesky timeline.")
                time.sleep(60*60*3) # Check every 3 hours
                self.login() # Refresh access token periodically

        if self.ok:
            self.monitor_thread = Thread(target=monitor_thread, args=(self,))
            self.monitor_thread.daemon = True
            self.monitor_thread.start()
            logger.log("Started Bluesky timeline monitor thread.")
            return 0
        else:
            logger.log(
                "Bluesky timeline monitor is not available, cannot start monitoring.",
                type="error",
            )
        return -1

    def get_timeline_users(self):
        if not self.ok:
            logger.log(
                "Bluesky timeline monitor is not available, cannot get timeline.",
                type="error",
            )
            return []
        try:
            response = utils.get(
                self.api_url, headers={"Authorization": f"Bearer {self.access_token}"}
            )
            # logger.log(response.text)
        except Exception as e:
            logger.log(f"Failed to get Bluesky timeline: {e}", type="error")
            return []
        if response.status_code == 200:
            data = response.json()
            with open("tmp/bsky_timeline.json", "w") as f:
                json.dump(data, f, indent=4)
            users = []
            for post in data.get("feed", []):
                post = post.get("post", {})
                user = post.get("author", {}).get("handle", "")
                if user:
                    users.append(user)
            users = list(set(users))
            return users
        else:
            logger.log(
                f"Failed to get Bluesky timeline, status code: {response.status_code}",
                type="error",
            )
            return []


def start_timeline_monitors():
    monitors = {}
    monitors["x"] = XTimelineMonitor()
    monitors["x"].start()
    monitors["bsky"] = BlueskyTimelineMonitor()
    monitors["bsky"].start()
    return monitors


if __name__ == "__main__":
    monitors = start_timeline_monitors()
    users = monitors["bsky"].get_timeline_users()
    print(users)
