# fix gallery-dl's weird filename formatting
import os, json, re
import argparse

parser = argparse.ArgumentParser(description="Fix gallery-dl filename formatting")
# allow many folders to be specified
parser.add_argument("dirs", help="Directories containing the files to rename", nargs="+")
parser.add_argument("dir", help="Directory containing the files to rename", nargs="?")
args = parser.parse_args()

dirs = args.dirs if args.dirs else [args.dir]

for dir in dirs:
    print(f"Processing directory: {dir}")
    json_files = [f for f in os.listdir(dir) if f.endswith(".json") and re.match(r"\d+", f)]
    existing_files = [f for f in os.listdir(dir) if not f.endswith(".json")]
    existing_files = set(existing_files)  # convert to set for faster lookup
    for filename in json_files:
        if filename.endswith(".json") and re.match(r"\d+", filename):
            json_path = os.path.join(dir, filename)
            with open(json_path, "r") as f:
                data = json.load(f)
            id = data.get("id")
            files = [data.get("file", {})] + data.get("attachments", [])
            files = [f for f in files if f]
            for file in files:
                # loop through all files and rename them
                for existing_file in existing_files:
                    if existing_file.endswith(".json"):
                        continue
                    if existing_file.startswith(f"{id}_") and existing_file.endswith(file["name"]):
                        old_path = os.path.join(dir, existing_file)
                        new_filename = f"{id}_{file['name']}"
                        new_path = os.path.join(dir, new_filename)
                        if not os.path.exists(old_path):
                            print(f"File {old_path} does not exist, skipping.")
                            continue
                        if os.path.exists(new_path):
                            # print(f"File {new_path} already exists, skipping rename of {old_path}.")
                            continue
                        if old_path != new_path:
                            os.rename(old_path, new_path)
                            print(f"Renamed {old_path} to {new_path}")