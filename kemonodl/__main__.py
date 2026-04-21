import os, json, time, argparse, re
import requests, tqdm

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3",
    "Accept": "text/css",
}

kemono_domain = "kemono.cr"
coomer_domain = "coomer.st"

# set arguments
parser = argparse.ArgumentParser(description="Kemono Downloader")
parser.add_argument(
    "url", help="URL of the Kemono user or post to download", default="", nargs="?"
)
parser.add_argument(
    "--proxy", help="Proxy URL (e.g. http://127.0.0.1:8080)", default=None
)
parser.add_argument(
    "--target-dir", help="Directory to save downloaded content", default="downloads"
)
parser.add_argument(
    "--skip-files",
    help="Skip downloading files, only save JSON data",
    action="store_true",
)
parser.add_argument(
    "-ni",
    "--no-interactive",
    help="Run in non-interactive mode (no progress bars, minimal output)",
    action="store_true",
)
parser.add_argument(
    "--size-limit",
    help="Maximum file size to download in MB (default: 1000 MB)",
    type=int,
    default=1000,
)

args = parser.parse_args()


def check_allowed_filename(filename):
    if "http" in filename:
        return False
    if "/" in filename or "\\" in filename:
        return False
    return True


def get(url, stream=False, local_headers=None):
    if args.proxy:
        proxies = {"http": args.proxy, "https": args.proxy}
    else:
        proxies = None
    headers_to_use = local_headers if local_headers is not None else headers
    for _ in range(3):
        try:
            response = requests.get(
                url, headers=headers_to_use, proxies=proxies, timeout=10, stream=stream
            )
            response.raise_for_status()
            return response
        except requests.RequestException as e:
            print(f"Error fetching {url}: {e}.\nRetrying...")
            time.sleep(5)
    raise Exception(f"Failed to fetch {url} after multiple attempts.")


# Due to the extremely unreliable servers of kemono.party and coomer.party, we use a custom stream download function with retries and resume support
def stream_download(url, filepath, chunk_size=81920):
    max_retries = 3
    downloaded = 0
    has_new_data = False
    retry = 0
    with tqdm.tqdm(
        total=0, unit="B", unit_scale=True, disable=args.no_interactive
    ) as pbar:
        with open(filepath + ".part", "wb") as file_handle:
            while retry < max_retries:
                try:
                    current_headers = headers.copy()
                    if downloaded > 0:
                        current_headers["Range"] = f"bytes={downloaded}-"

                    resp = get(url, stream=True, local_headers=current_headers)
                    pbar.total = int(resp.headers.get("Content-Length", 0)) + downloaded
                    if (
                        int(resp.headers.get("Content-Length", 0))
                        > args.size_limit * 1024 * 1024
                    ):
                        print(
                            f"File size exceeds limit of {args.size_limit} MB. Skipping download."
                        )
                        return 1

                    if downloaded > 0 and resp.status_code != 206:
                        print(
                            f"Server does not support resuming downloads for {url}. Restarting from beginning."
                        )
                        downloaded = 0
                        file_handle.seek(0)
                        file_handle.truncate(0)
                        current_headers.pop("Range", None)
                        resp = get(url, stream=True, local_headers=current_headers)
                        pbar.total = int(resp.headers.get("Content-Length", 0))
                        pbar.refresh()

                    for chunk in resp.iter_content(chunk_size=chunk_size):
                        if chunk:
                            file_handle.write(chunk)
                            downloaded += len(chunk)
                            pbar.update(len(chunk))
                            has_new_data = True
                    os.rename(
                        filepath + ".part", filepath
                    )  # Rename temp file to final name
                    return  # Success
                except Exception as e:
                    print(
                        f"Error downloading {url}: {e}. Retrying ({retry + 1}/{max_retries})..."
                    )
                    if has_new_data:
                        print(
                            f"Bump up max retries since we made progress downloading..."
                        )
                        max_retries += 1  # Give extra retries if we are making progress
                    retry += 1
                    has_new_data = False
                    time.sleep(5)
        # remove incomplete file after max retries
        if os.path.exists(filepath + ".part"):
            os.remove(filepath + ".part")
        raise Exception(f"Failed to download {url} after multiple attempts.")


def get_user_data(uid, type_):
    if type_ in ["patreon", "fanbox"]:
        profile_api = f"https://{kemono_domain}/api/v1/{type_}/user/{uid}/profile"
    else:
        profile_api = f"https://{coomer_domain}/api/v1/{type_}/user/{uid}/profile"
    json_data = get(profile_api).text
    json_data = json.loads(json_data)
    json_data["name"] = json_data["name"].lower().strip()
    name = json_data["name"]
    post_count = json_data["post_count"]
    return name, post_count, json_data


def get_posts_data(uid, type_, offset=0):
    if type_ in ["patreon", "fanbox"]:
        posts_api = (
            f"https://{kemono_domain}/api/v1/{type_}/user/{uid}/posts?o={offset}"
        )
    else:
        posts_api = (
            f"https://{coomer_domain}/api/v1/{type_}/user/{uid}/posts?o={offset}"
        )
    json_data = get(posts_api).text
    json_data = json.loads(json_data)
    return json_data


def get_post_data(uid, pid, type_):
    if type_ in ["patreon", "fanbox"]:
        post_api = f"https://{kemono_domain}/api/v1/{type_}/user/{uid}/post/{pid}"
    else:
        post_api = f"https://{coomer_domain}/api/v1/{type_}/user/{uid}/post/{pid}"
    json_data = get(post_api).text
    json_data = json.loads(json_data)
    return json_data


def main(url):
    if not ("coomer." in url or "kemono." in url):
        print("Invalid URL. Please provide a valid Kemono or Coomer URL.")
        return
    current_post_idx = 0
    url = url.lower().split("?")[0].strip("/")
    uid = re.search(r"/user/([a-zA-Z0-9_\-\.]+)", url).group(1)
    type_ = re.search(r"/(\w+)/user/", url).group(1)
    if type_ not in ["patreon", "fanbox", "onlyfans"]:
        print(f"Unsupported type: {type_}")
        return
    name, post_count, profile_data = get_user_data(uid, type_)
    user_dir = os.path.join(args.target_dir, name)
    os.makedirs(user_dir, exist_ok=True)
    with open(os.path.join(user_dir, "profile.json"), "w") as f:
        json.dump(profile_data, f, indent=4)
    print(f"User: {name}, Posts: {post_count}")
    get_single_post = "post/" in url
    for offset in range(0, post_count, 50):
        if "post/" in url:
            posts_data = [
                {
                    "id": re.search(r"/post/(\d+)", url).group(1),
                }
            ]
            post_count = 1
        else:
            print(
                f"[{name}] Fetching posts {offset + 1} to {min(offset + 50, post_count)}..."
            )
            posts_data = get_posts_data(uid, type_, offset)
        for post in posts_data:
            current_post_idx += 1
            pid = post["id"]
            post_data = {}
            # try loading existing post data to avoid redundant API calls
            if os.path.exists(os.path.join(user_dir, f"{pid}.json")):
                print(
                    f"[{current_post_idx}/{post_count}][{name}] Loading existing data for post ID {pid}..."
                )
                try:
                    with open(os.path.join(user_dir, f"{pid}.json"), "r") as f:
                        post_data = json.load(f)
                except Exception as e:
                    print(f"Error loading existing data for post ID {pid}: {e}")
                    post_data = {}
                    print(f"Will attempt to re-fetch post data for ID {pid}.")
            try:
                if not post_data:
                    post_data = get_post_data(uid, pid, type_).get("post", {})
            except Exception as e:
                print(f"Error fetching post data for ID {pid}: {e}")
                continue
            print(
                f"[{current_post_idx}/{post_count}][{name}] Post ID: {pid}, Title: {post_data['title']}"
            )
            with open(os.path.join(user_dir, f"{pid}.json"), "w") as f:
                json.dump(post_data, f, indent=4)
            files = [post_data.get("file", {})]
            files += post_data.get("attachments", [])
            for file in files:
                if not file or args.skip_files:
                    continue
                filename = f"{pid}_{file['name']}"
                if not check_allowed_filename(filename):
                    print(f"File with invalid filename: {filename}")
                    filename = f"{pid}_{file['path'].strip('/').split('/')[-1]}"
                    print(f"Renamed to: {filename}")
                if type_ in ["patreon", "fanbox"]:
                    url = f"https://{kemono_domain}{file['path']}"
                else:
                    url = f"https://{coomer_domain}{file['path']}"
                filepath = os.path.join(user_dir, filename)
                if not os.path.exists(filepath):
                    print(f"Downloading {filename}...")
                    try:
                        result = stream_download(
                            url, filepath
                        )  # Use stream_download for better reliability
                        if result:
                            print(f"Not downloaded {filename}")
                        else:
                            print(f"Saved {filename}")
                    except Exception as e:
                        print(f"Error downloading {filename}: {e}")
                else:
                    print(f"{filename} already exists, skipping.")
        if get_single_post:
            break


if __name__ == "__main__":
    if not args.url:
        print("Please provide a URL to download.")
        parser.print_help()
    else:
        main(args.url)
