from threading import Lock
import config
import time

log_lock = Lock()
log_file_handle = open(config.log_file,'a')

def log(*s):
    s = " ".join(s)
    with log_lock:
        x = f"{time.ctime()}: {s}"
        print(x)
        log_file_handle.write(x+"\n")
        log_file_handle.flush()