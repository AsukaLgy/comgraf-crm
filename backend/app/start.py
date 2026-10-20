import subprocess
import sys
import os
import uvicorn

def main():
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], check=True)
    worker = subprocess.Popen([sys.executable, '-m', 'app.mail_worker'])
    try:
        uvicorn.run('app.main:app', host=os.getenv('APP_HOST', '0.0.0.0'), port=int(os.getenv('APP_PORT', '8000')), workers=int(os.getenv('APP_WORKERS', '2')))
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=15)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait(timeout=5)


if __name__ == '__main__':
    main()
