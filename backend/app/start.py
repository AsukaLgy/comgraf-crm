import subprocess
import sys
import uvicorn

def main():
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], check=True)
    uvicorn.run('app.main:app', host='0.0.0.0', port=8000, workers=2)


if __name__ == '__main__':
    main()
