from threading import Lock
import time

import config

log_file_handle = open(config.log_file, "a")
global_logs = []
global_cnt = 0

global_err_logs = []

VERBOSE_LEVEL = 0

def log(*s, type="info", verbose=0):
    global global_cnt
    if verbose > VERBOSE_LEVEL:
        return
    s = " ".join([str(x) for x in s])
    x = f"{time.ctime()} {s}"
    print(x)
    global_logs.append((type, x))
    if type == "error":
        global_err_logs.append(x)
    log_file_handle.write(x + "\n")
    global_cnt += 1
    if global_cnt % 500 == 0:
        log_file_handle.flush()


def get_recent_logs(n=100):
    return global_logs[-n:]
