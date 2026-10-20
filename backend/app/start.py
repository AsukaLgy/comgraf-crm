import subprocess
import sys
import os
import logging
import threading
import time
import uvicorn

logger = logging.getLogger(__name__)

def main():
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], check=True)
    stop = threading.Event()
    worker_process = {'process': None}

    def supervise_worker():
        delay = 1
        while not stop.is_set():
            process = subprocess.Popen([sys.executable, '-m', 'app.mail_worker'])
            worker_process['process'] = process
            while process.poll() is None and not stop.wait(2):
                pass
            if stop.is_set():
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                return
            logger.error('El proceso del trabajador de correo terminó code=%s; reinicio en %s s', process.returncode, delay)
            if stop.wait(delay):
                return
            delay = min(delay * 2, 60)

    supervisor = threading.Thread(target=supervise_worker, name='mail-worker-supervisor', daemon=True)
    supervisor.start()
    try:
        uvicorn.run('app.main:app', host=os.getenv('APP_HOST', '0.0.0.0'), port=int(os.getenv('APP_PORT', '8000')), workers=int(os.getenv('APP_WORKERS', '2')))
    finally:
        stop.set()
        supervisor.join(timeout=12)


if __name__ == '__main__':
    main()
