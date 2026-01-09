import os
import subprocess
import traceback
import time
import signal
import select

import logger
# set environment variables to disable stdio buffering for subprocesses
os.environ["PYTHONUNBUFFERED"] = "1"

# Track the currently running process for interrupt functionality
_current_process = None

def interrupt():
    """Interrupt the currently running command"""
    global _current_process
    if _current_process is None:
        logger.log("[No command running to interrupt]")
        return
    
    logger.log("[Interrupting command]")
    time.sleep(0.4)
    
    try:
        # Send SIGTERM to the entire process group
        os.killpg(os.getpgid(_current_process.pid), signal.SIGTERM)
        time.sleep(1)
        # If still running, force kill
        if _current_process and _current_process.poll() is None:
            logger.log("[Force killing command]")
            os.killpg(os.getpgid(_current_process.pid), signal.SIGKILL)
    except ProcessLookupError:
        pass  # Process already exited
    except Exception as e:
        logger.log(f"[Error interrupting command: {traceback.format_exc()}]", type="error")

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
    logger.log(f"[Running command] {command}", type="attention")
    # Add stdbuf for line buffering on non-Python commands
    if unbuffered:
        command = f"stdbuf -oL -eL {command}"
    env = os.environ.copy()
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=True,
        env=env,
        preexec_fn=os.setsid 
    )
    
    global _current_process
    _current_process = process
    
    stop_keywords_cnt = 0
    
    def process_output(output, is_stderr=False):
        """Process a line of output, handling triggers and returning whether to stop."""
        nonlocal stop_keywords_cnt
        if not output:
            return False
        
        prefix = "[stderr] " if is_stderr else ""
        logger.log(f"{prefix}{output.strip()}", type="warning" if is_stderr else "info")
        
        if any(keyword in output for keyword in stop_keywords):
            stop_keywords_cnt += 1
            if stop_keywords_cnt >= stop_keywords_max_cnt:
                logger.log(f"[Stopping command]")
                time.sleep(0.4)
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                    time.sleep(1)
                    if process and process.poll() is None:
                        logger.log(f"[Force killing command]")
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass  # Process already exited
                return True
        else:
            stop_keywords_cnt = 0
        
        for trigger, callback in triggers:
            if trigger in output:
                source = "stderr" if is_stderr else "stdout"
                logger.log(f"[Trigger '{trigger}' activated from {source}]")
                callback()
        
        return False
    
    try:
        # Use select to monitor both stdout and stderr
        stdout_fd = process.stdout.fileno()
        stderr_fd = process.stderr.fileno()
        should_stop = False
        
        while not should_stop:
            # Wait for data on either stdout or stderr
            readable, _, _ = select.select([stdout_fd, stderr_fd], [], [], 0.1)
            
            for fd in readable:
                if fd == stdout_fd:
                    output = process.stdout.readline()
                    if output:
                        if process_output(output, is_stderr=False):
                            should_stop = True
                            break
                elif fd == stderr_fd:
                    output = process.stderr.readline()
                    if output:
                        if process_output(output, is_stderr=True):
                            should_stop = True
                            break
            
            if should_stop:
                break
            
            # Check if process has finished and no more data
            if process.poll() is not None:
                # Drain any remaining output
                for line in process.stdout:
                    process_output(line, is_stderr=False)
                for line in process.stderr:
                    process_output(line, is_stderr=True)
                break
    except KeyboardInterrupt:
        logger.log("Command interrupted by user.")
        time.sleep(0.4)
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass  # Process already exited
    except Exception as e:
        logger.log(f"[Error] {traceback.format_exc()}", type="error")
        time.sleep(0.4)
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass  # Process already exited
    finally:
        _current_process = None
        process.stdout.close()
        process.stderr.close()
        try:
            process.wait(timeout=5)  # Wait for the process to terminate
        except subprocess.TimeoutExpired:
            logger.log("Process did not terminate in time, killing it.")
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass  # Process already exited
        logger.log(f"[Command finished with exit code {process.returncode}]")