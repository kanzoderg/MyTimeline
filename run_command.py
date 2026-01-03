import os
import subprocess
import time
import signal

import logger
# set environment variables to disable stdio buffering for subprocesses
os.environ["PYTHONUNBUFFERED"] = "1"

def run_command(
    command, stop_keywords=None, stop_keywords_max_cnt=12, unbuffered=True, triggers=[]
):
    """
    Run a shell command and monitor its output in real-time. Stop the command if any of the stop keywords are found in the output.
    """
    if not stop_keywords:
        stop_keywords = []
    if isinstance(command, list):
        command = " ".join(command)
    logger.log(f"[Running command] {command}")
    # Add stdbuf for line buffering on non-Python commands
    if unbuffered:
        command = f"stdbuf -oL -eL {command}"
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=True,
        env=env,
        preexec_fn=os.setsid 
    )
    stop_keywords_cnt = 0
    try:
        while True:
            output = process.stdout.readline()
            if output == "" and process.poll() is not None:
                break
            if output:
                logger.log(output.strip())
                if any(keyword in output for keyword in stop_keywords):
                    stop_keywords_cnt += 1
                    # logger.log(f"[Stop keyword found: {output.strip()}]")
                    # logger.log(f"[Stop keywords count: {stop_keywords_cnt}]")
                    if stop_keywords_cnt >= stop_keywords_max_cnt:
                        logger.log(f"[Stopping command]")
                        time.sleep(0.4)
                        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                        time.sleep(1)
                        if process.poll() is None:  # Check if the process is still running
                            logger.log(f"[Force killing command]")
                            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                        break
                else:
                    stop_keywords_cnt = (
                        0  # Reset count if a keyword is found but not enough times
                    )
                for trigger, callback in triggers:
                    if trigger in output:
                        logger.log(f"[Trigger '{trigger}' activated]")
                        callback()
    except KeyboardInterrupt:
        logger.log("Command interrupted by user.")
        time.sleep(0.4)
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except Exception as e:
        logger.log(f"[Error] {e}")
        time.sleep(0.4)
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    finally:
        errout = process.stderr.read()
        if errout:
            logger.log(f"[Error output] {errout.strip()}")
            # Handle trigger actions for stderr output
            for trigger, callback in triggers:
                if trigger in errout:
                    logger.log(f"[Trigger '{trigger}' activated from stderr]")
                    callback()
        process.stdout.close()
        process.stderr.close()
        try:
            process.wait(timeout=5)  # Wait for the process to terminate
        except subprocess.TimeoutExpired:
            logger.log("Process did not terminate in time, killing it.")
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        logger.log(f"[Command finished with exit code {process.returncode}]")